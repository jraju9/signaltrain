
# -*- coding: utf-8 -*-
__author__ = 'S.H. Hawley'

# imports
import numpy as np
import scipy.signal as scipy_signal
import torch
#import torchaudio
import librosa
from numba import autojit, njit, jit   # Note: nopython version gives symbol errors when used w/ Jupyter Notebook, so using autojit instead
import os
from helpers import io_methods
from scipy.io import wavfile

def random_ends(size=1): # probabilty dist. that emphasizes boundaries
    return np.random.beta(0.8,0.8,size=size)

@autojit
def sliding_window(x, size, overlap=0):
    """
    Stacks 1D array into a series of sliding windows with a certain amount of overlaps.
    This is fast because it generates a "view" rather than creating a new array.
    -->Unless the windows don't divide evenly, in which case we pad with zeros to get an even coverage
    Inputs:
       x:  the 1D array to be windowed
       size:  the width of each window
       overlap = amount of "lookback" (in samples), when predicting the next set of values
    Example:
        x = np.arange(10)
        print(sliding_window(x, 5, overlap=2))
         [[0 1 2 3 4]
         [3 4 5 6 7]
         [6 7 8 9 0]]
    Source: from last answer to https://stackoverflow.com/questions/4923617/efficient-numpy-2d-array-construction-from-1d-array
    """
    step = size - overlap # amount of non-overlapped values per window
    remainder = (x.shape[-1]-size) % step   # see if array will divide up evenly
    if remainder != 0:
        x = np.pad(x, (0,step-remainder), mode='constant') # pad end with zeros until it does. note this changes the size of x

    nwin = (x.shape[-1]-size)//step + 1  # this truncates any leftover rows, rather than padding with zeros
    shape = x.shape[:-1] + (nwin, size)
    strides = x.strides[:-1] + (step*x.strides[-1], x.strides[-1])
    return np.lib.stride_tricks.as_strided(x, shape=shape, strides=strides, writeable=False) # writeable=False is to avoid memory corruption. better safe than sorry!

'''
def undo_siding_window_view(x, overlap):
    """
    Undoes the the sliding window view: Returns 1-D shape of length equal to nearest
        multiple of window, minus overlap
    NOTE: This operates only on a view.  It does not remove windows from copies or new arrays
    """
    shape = [(overlap + len(x[:,overlap:])//overlap)*overlap - overlap + 1]
    return np.lib.stride_tricks.as_strided(x, shape=shape, strides=[x.strides[-1]], writeable=False)
'''
def undo_sliding_window(x, overlap):
    """
    This works in general, i.e. for views and for copies of arrays.
    NOTE: does not undo any padding that might have occurred.
    """
    if overlap != 0:
        return np.concatenate( (x[0,0:overlap],x[:,overlap:].flatten()))
    else:
        return x


#--- List of test signals:
def randsine(t, randfunc=np.random.rand, amp_range=[0.2,0.9], freq_range=[5,150], n_tones=None, t0_fac=None):
    y = np.zeros(t.shape[0])
    if n_tones is None: n_tones=np.random.randint(1,3)
    for i in range(n_tones):
        amp = amp_range[0] + (amp_range[1]-amp_range[0])*randfunc()
        freq = freq_range[0] + (freq_range[1]-freq_range[0])*randfunc()
        t0 = randfunc() * t[-1] if t0_fac is None else t0_fac*t[-1]
        y += amp*np.cos(freq*(t-t0))
    return y

def box(t, randfunc=np.random.rand, t0_fac=None):
    height_low, height_high = 0.3*randfunc()+0.1, 0.35*randfunc() + 0.6
    maxi = len(t)
    delta = 1+ maxi//100     # slope the sides slightly
    i_up = delta+int( 0.3*randfunc() * maxi) if t0_fac is None else int(t0_fac*maxi)
    i_dn = min( i_up + int( (0.3+0.35*randfunc())*maxi), maxi-delta-1)   # time for jumping back down
    x = height_low*np.ones(t.shape[0]).astype(t.dtype)  # noise of unit amplitude
    x[i_up:i_dn] = height_high
    x[i_up-delta:i_up+delta] = height_low + (height_high-height_low)*(np.arange(2*delta))/2/delta
    x[i_dn-delta:i_dn+delta] = height_high - (height_high-height_low)*(np.arange(2*delta))/2/delta
    return x

def expdecay(t, randfunc=np.random.rand, t0_fac=None):
    t0 = 0.35*randfunc()*t[-1] if t0_fac is None else t0_fac*t[-1]
    height_low, height_high = 0.1*randfunc()+0.1, 0.35*randfunc() + 0.6
    decay = 12*randfunc()
    x = np.exp(-decay * (t-t0)) * height_high   # decaying envelope
    x[np.where(t < t0)] = height_low   # without this, it grow exponentially 'to the left'
    return x

def pluck(t, randfunc=np.random.rand, freq_range=[50,6400], n_tones=None, t0_fac=None):
    y = np.zeros(t.shape[0])
    if n_tones is None: n_tones=np.random.randint(1,4)
    for i in range(n_tones):
        amp0 = (0.45 * randfunc() + 0.5) * np.random.choice([-1, 1])
        t0 = (2. * randfunc()-1)*0.3 * t[-1] if t0_fac is None else t0_fac*t[-1] # for phase
        freq = freq_range[0] + (freq_range[1]-freq_range[0])*randfunc()
        y += amp0*np.sin(freq * (t-t0))
    return y * expdecay(t, t0_fac=t0_fac)

def spikes(t, n_spikes=50, randfunc=np.random.rand):  # "bunch of random spikes"
    x = np.zeros(t.shape[0])
    for i in range(n_spikes):   # arbitrarily make a 'spike' somewhere, surrounded by silence
      loc = int( int(randfunc()*len(t)-2)+1* t[-1] )
      height = (2*randfunc()-1)*0.7    # -0.7...0.7
      x[loc] = height
      x[loc+1] = height/2  # widen the spike a bit
      x[loc-1] = height/2

    amp_n = 0.1*randfunc()
    x = x + amp_n*np.random.normal(size=t.shape[0])    # throw in noise
    return x

def triangle(t, randfunc=np.random.rand, t0_fac=None): # ramp up then down
    height = (0.4 * randfunc() + 0.4) * np.random.choice([-1,1])
    width = randfunc()/4 * t[-1]     # half-width actually
    t0 = 2*width + 0.4 * randfunc()*t[-1] if t0_fac is None else t0_fac*t[-1]
    x = height * (1 - np.abs(t-t0)/width)
    x[np.where(t < (t0-width))] = 0
    x[np.where(t > (t0+width))] = 0
    amp_n = (0.1*randfunc()+0.02)   # add noise
    return x + amp_n*(2*np.random.random(t.shape[0])-1)


def read_audio_file(filename, sr=44100):
    #signal, sr = librosa.load(filename, sr=sr, mono=True, res_type='kaiser_fast') # Librosa's reader is incredibly slow. do not use

    #signal, sr = torchaudio.load(filename)#, normalization=True)   # Torchaudio's reader is pretty fast but normalization is a problem
    #signal = signal.numpy().flatten()

    #reader = io_methods.AudioIO   # Stylios' file reader. Haven't gotten it working yet
    #signal, sr = reader.audioRead(filename, mono=True)

    sr, signal = wavfile.read(filename)   # scipy works fine and is fast
    return signal, sr

def write_audio_file(filename, data, sr=44100):
    wavfile.write(filename, sr, data)
    #librosa.output.write_wav(filename, data, sr)
    #torchaudio.save(filename, torch.Tensor(data).unsqueeze(1), sr)
    return

def readaudio_generator(seq_size,  path=os.path.expanduser('~')+'/datasets/signaltrain/Val', sr=44100,
    random_every=True):
    """
    reads audio from any number of audio files sitting in directory 'path'
    supplies a window of length "seconds". If random_every=True, this window will be randomly chosen
    """
    # seq_size = amount of audio samples to supply from file
    # basepath = directory containing Train, Val, and Test directories
    # path = audio files for dataset  (can be Train, Val or test)
    # random_every = get a random window every time next is called, or step sequentially through file
    files = os.listdir(path)
    read_new_file = True
    start = -seq_size
    while True:
        if read_new_file:
            filename = path+'/'+np.random.choice(files)  # pick a random audio file in the directory
            #print("Reading new data from "+filename+" ")
            data, sr = read_audio_file(filename, sr=sr)
            read_new_file=False   # don't keep switching files  everytime generator is called


        if (random_every): # grab a random window of the signal
            start = np.random.randint(0,data.shape[0]-seq_size)
        else:
            start += seq_size
        xraw = data[start:start+seq_size]   # the newaxis just gives us a [1,] on front
        # Note: any 'windowing' happens after the effects are applied, later
        rc = ( yield xraw )         # rc is set by generator's send() method.  YIELD here is the output
        if isinstance(rc, bool):    # can set read_new by calling send(True)
            read_new_file = rc


def synth_input_sample(t, chooser=None, randfunc=np.random.rand, t0_fac=None):
    """
    Synthesizes one instance from various 'fake' audio wave forms -- synthetic data
    """
    if chooser is None:
        chooser = np.random.randint(0, 7)

    if 0 == chooser:                     # sine, with random phase, amp & freq
        return randsine(t, t0_fac=t0_fac)
    elif 1 == chooser:                  # noisy sine
        return randsine(t,t0_fac=t0_fac) + 0.1*(2*np.random.rand(t.shape[0])-1)
    elif 2 == chooser:                    #  "pluck", decaying sine wave
        return pluck(t,t0_fac=t0_fac)
    elif 3 == chooser:                   # ramp up then down
        return triangle(t,t0_fac=t0_fac)
    elif (4 == chooser):                # 'box'
        return box(t,t0_fac=t0_fac)
    elif 5 == chooser:                 # "bunch of spikes"
        return spikes(t)
    elif 6 == chooser:                # noisy box
        return box(t,t0_fac=t0_fac) * (2*np.random.rand(t.shape[0])-1)
    elif 7 == chooser:                # noisy 'pluck'
        amp_n = (0.3*randfunc()+0.1)
        return pluck(t,t0_fac=t0_fac) + amp_n*(2*np.random.random(t.shape[0])-1)  #noise centered around 0
    elif 8 == chooser:                  # just white noise
        amp_n = (0.6*randfunc()+0.2)
        return amp_n*(2*np.random.rand(t.shape[0])-1)
    else:
        return 0.5*(synth_input_sample(t)+synth_input_sample(t)) # superposition of the above
#---- End test signals


#---- Effects
@autojit
def compressor(x, thresh=-24, ratio=2, attack=2048, dtype=np.float32):
    """
    simple compressor effect, code thanks to Eric Tarr @hackaudio
    Inputs:
       x:        the input waveform
       thresh:   threshold in dB
       ratio:    compression ratio
       attack:   attack & release time (it's a simple compressor!) in samples
    """
    fc = 1.0/float(attack)               # this is like 1/attack time
    b, a = scipy_signal.butter(1, fc, analog=False, output='ba')
    zi = scipy_signal.lfilter_zi(b, a)

    dB = 20. * np.log10(np.abs(x) + 1e-6).astype(dtype)
    in_env, _ = scipy_signal.lfilter(b, a, dB, zi=zi*dB[0])  # input envelope calculation
    out_env = np.copy(in_env)              # output envelope
    i = np.where(in_env >  thresh)          # compress where input env exceeds thresh
    out_env[i] = thresh + (in_env[i]-thresh)/ratio
    gain = np.power(10.0,(out_env-in_env)/20)
    y = (x * gain).astype(dtype)
    return y

@jit(nopython=True)
def my_clip_min(x, clip_min):  # does the work of np.clip(), which numba doesn't support yet
    # TODO: keep an eye on Numba PR https://github.com/numba/numba/pull/3468 that fixes this
    inds = np.where(x < clip_min)
    x[inds] = clip_min
    return x

@jit(nopython=True)
def compressor_new_fast(x, thresh=-24.0, ratio=2.0, attackTime=0.01,releaseTime=0.01, sr=44100.0, dtype=np.float32):
    """
    (Minimizing the for loop, removing dummy variables, and invoking numba @autojit made this "fast")
    Inputs:
      x: input signal
      sr: sample rate in Hz
      thresh: threhold in dB
      ratio: ratio (ratio:1)
      attackTime, releasTime: in seconds
      dtype: typical numpy datatype
    """
    N = len(x)
    y = np.zeros(N, dtype=dtype)
    lin_A = np.zeros(N, dtype=dtype)  # functions as gain

    # Initialize separate attack and release times
    alphaA = np.exp(-np.log(9)/(sr * attackTime))#.astype(dtype)
    alphaR = np.exp(-np.log(9)/(sr * releaseTime))#.astype(dtype)

    # Turn the input signal into a uni-polar signal on the dB scale
    x_uni = np.abs(x).astype(dtype)
    x_dB = 20*np.log10(x_uni + 1e-8).astype(dtype)

    # Ensure there are no values of negative infinity
    #x_dB = np.clip(x_dB, -96, None)   # Numba doesn't yet support np.clip but we can write our own
    x_dB = my_clip_min(x_dB, -96)

    # Static Characteristics
    gainChange_dB = np.zeros(x_dB.shape[0])
    i = np.where(x_dB > thresh)
    gainChange_dB[i] =  thresh + (x_dB[i] - thresh)/ratio - x_dB[i] # Perform Downwards Compression

    for n in range(x_dB.shape[0]):   # this loop is slow but unavoidable if alphaA != alphaR. @autojit makes it fast.
        # smooth over the gainChange
        if gainChange_dB[n] < lin_A[n-1]:
            lin_A[n] = ((1-alphaA)*gainChange_dB[n]) +(alphaA*lin_A[n-1]) # attack mode
        else:
            lin_A[n] = ((1-alphaR)*gainChange_dB[n]) +(alphaR*lin_A[n-1]) # release

    lin_A = np.power(10.0,(lin_A/20)).astype(dtype)  # Convert to linear amplitude scalar; i.e. map from dB to amplitude
    y = lin_A * x    # Apply linear amplitude to input sample

    return y.astype(dtype)


 # this is a echo or delay effect
def echo(x, delay_samples=1487, ratio=0.6, echoes=1, dtype=np.float32):
    # ratio = redution ratio
    y = np.copy(x).astype(dtype)
    for i in range(int(np.round(echoes))):   # note 'echoes' is a 'switch'; does not vary continuously
        ip1 = i+1       # literally "i plus 1"
        delay_length = ip1 * delay_samples
        delay_length_int = int(np.floor(delay_length))
        # the following is an attempt to make the delay continuously differentiable
        diff = delay_length - delay_length_int
        x_delayed = ( (1-diff)*np.pad(x,(delay_length_int,0),mode='constant')[0:-delay_length_int] #shift and pad with zeros
                        + diff*np.pad(x,(delay_length_int+1,0),mode='constant')[0:-(delay_length_int+1)])
        y += pow(ratio, ip1) * x_delayed
    return y




# Classes for Effects
class Effect():
    """Generic effect super-class
       sub-classed Effects should also define a 'go_wc()' method to execute the actual effect
       Network will call go() with normalized knob values, which then will call go_wc()
    """
    def __init__(self, sr=44100):
        self.name = 'Generic Effect'
        self.knob_names = ['knob']
        self.knob_ranges = np.array([[0,1]])  # min,max world coordinate values for "all the way counterclockwise" and "all the way clockwise"
        self.sr = sr
        self.is_inverse = False  # Does this effect perform an 'inverse problem' by reversing x & y at the end?

    def knobs_wc(self, knobs_nn):   # convert knob vals from [-.5,.5] to "world coordinates" used by effect functions
        return (self.knob_ranges[:,0] + (knobs_nn+0.5)*(self.knob_ranges[:,1]-self.knob_ranges[:,0])).tolist()

    def info(self):  # Print some information about the effect
        assert len(self.knob_names)==len(self.knob_ranges)
        print(f'Effect: {self.name}.  Knobs:')
        for i in range(len(self.knob_names)):
            print(f'                            {self.knob_names[i]}: {self.knob_ranges[i][0]} to {self.knob_ranges[i][1]}')

    # Effects should also define a 'go_wc' method which executes the effect, mapping input and knobs_nn to output y, x
    #   We return x as well as y, because some effects may reverse x & y (e.g. denoiser)
    def go_wc(self, x, knobs_wc):
        print("Warning: This effect's go_wc() is undefined")

    def go(self, x, knobs_nn, **kwargs):  # this is the interface typically called during training & inference
        knobs_w = self.knobs_wc(knobs_nn)
        return self.go_wc(x, knobs_w, **kwargs)


class Compressor(Effect):
    def __init__(self):
        super(Compressor, self).__init__()
        self.name = 'Compressor'
        self.knob_names = ['threshold', 'ratio', 'attackrelease']
        self.knob_ranges = np.array([[-30,0], [1,5], [10,2048]])
    def go_wc(self, x, knobs_w):
        return compressor(x, thresh=knobs_w[0], ratio=knobs_w[1], attack=knobs_w[2]), x

class Compressor_4c(Effect):  # compressor with 4 controls
    def __init__(self):
        super(Compressor_4c, self).__init__()
        self.name = 'Compressor_4c'
        self.knob_names = ['thresh', 'ratio', 'attackTime','releaseTime']
        self.knob_ranges = np.array([[-30,0], [1,5], [1e-3,4e-2], [1e-3,4e-2]])
    def go_wc(self, x, knobs_w):
        return compressor_new_fast(x, thresh=knobs_w[0], ratio=knobs_w[1], attackTime=knobs_w[2], releaseTime=knobs_w[3]), x

class Echo(Effect):
    def __init__(self):
        super(Echo, self).__init__()
        self.name = 'Echo'
        self.knob_names = ['delay_samples', 'ratio', 'echoes']
        #self.knob_ranges = np.array([[100,1500], [0.1,0.9],[2,2]])
        self.knob_ranges = np.array([[400,400], [0.4,1.0],[2,2]])
    def go_wc(self, x, knobs_w):
        return echo(x, delay_samples=int(np.round(knobs_w[0])), ratio=knobs_w[1], echoes=int(np.round(knobs_w[2]))), x

class PitchShifter(Effect):
    def __init__(self):
        super(PitchShifter, self).__init__()
        self.name = 'PitchShifter'
        self.knob_names = ['n_steps']
        self.knob_ranges = np.array([[-12,12]])  # number of 12-tone pitch steps by which to shift the signal
    def go_wc(self, x, knobs_w):
        return librosa.effects.pitch_shift(x, sr=self.sr, n_steps=knobs_w[0]), x   # TODO: librosa's pitch_shift is SLOW!

class Denoise(Effect):  # add noise to x, swap x and y
    """
    This doesn't really denoise: It adds noise to the input, then swaps input & output.
    So you wouldn't be able to input a noisy signal and have it get denoised.
    But when the network trains on this, it learns to take noisy input and denoise it by a tunable amount 'strength'
    """
    def __init__(self):
        super(Denoise, self).__init__()
        self.name = 'Denoise'
        self.knob_names = ['strength']
        self.knob_ranges = np.array([[0.01,0.5]])
        self.is_inverse = True
    def go_wc(self, x, knobs_w):
        return x, x + knobs_w[0]*(2*np.random.random(x.shape[0])-1)   # swaps y & x: what was the input becomes the output


class TimeAlign(Effect):  # add noise to x, swap x and y
    """
    This affect completely ignores the input x.  Instead it re-synthesizes a time-aligned y,
    shifts it randomly and outputs that as y
    """
    def __init__(self, sr=44100):
        super(TimeAlign, self).__init__()
        self.name = 'TimeAlign'
        self.knob_names = ['strength']
        self.knob_ranges = np.array([[0.001,0.5]])
        self.is_inverse = True
        chunk_size = 4096 # TODO un-hardcode this
        self.t = np.arange(chunk_size,dtype=np.float32) / sr
    def go_wc(self, x, knobs_w):
        chooser = np.random.choice([2,4,6,7])
        y = synth_input_sample(self.t, chooser, t0_fac=0.5)   # start onset in the middle of chunk
        rand_shift = int(x.shape[0]* knobs_w[0]*(2*np.random.rand()-1)) # shift forward or back by 1/3 of width
        x = np.roll(y,rand_shift)
        if rand_shift > 0:
            x[0:rand_shift] = np.zeros(rand_shift)
        elif rand_shift < 0:
            x[-np.abs(rand_shift):] = np.zeros(np.abs(rand_shift))
        return y, x


class LowPass(Effect):
    # https://gist.github.com/junzis/e06eca03747fc194e322
    def __init__(self, sr=44100):
        super(LowPass, self).__init__()
        self.name = 'LowPass'
        self.knob_names = ['cutoff']
        self.knob_ranges = np.array([[10,2000]])  # number of 12-tone pitch steps by which to shift the signal
        self.sr = 44100.
    def butter_lowpass(self, cutoff, order=3):
        nyq = 0.5 * self.sr
        normal_cutoff = cutoff / nyq
        b, a = scipy_signal.butter(order, normal_cutoff, btype='low', analog=False)
        return b, a
    def go_wc(self, x, knobs_w, order=3):
        b, a = self.butter_lowpass(knobs_w[0], order=order)
        return scipy_signal.lfilter(b, a, x), x
# End of effects

# See data.py for AudioDataGenerator, etc

# utility routine for effects
def int2knobs(idx:int, knob_ranges:list, settings_per:int) -> list:
  """
  Maps a single (0-indexed) integer to a group of knob settings.
  Useful for systematically covering a range of (linearly) equally-spaced knob
  settings for dataset creation
  NOTE: Operates in a "little-endian" format, i.e. last knob(/digit) varies most
        rapidly as index changes
  Inputs:
     idx:  integer value to convert
     knob_ranges: a list of 2-element lists consiting of [min,max] values for each knob
          Ranges can be anything,
     settings_per: Settings per knob, i.e. number of increments (assumes same inc for all knobs)

  Examples:
  print(int2knobs(12345, [[-0.5,0.5]]*4, 12))
  [0.13636363636363635, -0.40909090909090906, 0.2272727272727273, 0.31818181818181823]

  For rolling a set of 3 dice:
  print( int2knobs(100, [[1,6]]*3, 6))
  [3.0, 5.0, 5.0]

  Simple base 10 aritmetic:
  print(int2knobs(1234, [[0,9]]*4, 10))
  [1.0, 2.0, 3.0, 4.0]
  """
  sp, nk = settings_per, len(knob_ranges)  # mere abbreviations, nk=num_knobs
  assert idx < sp**nk, "idx must be less than max range of possible values"
  knobs = []
  for i in range(nk-1,-1,-1):         # loop over knobs and multiples of sp
    sp_pow = sp**i
    setting = idx // sp_pow        # which setting (of settings_per) for this knob
    ik = nk-1-i                    # because we're going in reverse order, we need to grab knob-ranges in reverse order
    dkval = (knob_ranges[ik][1]-knob_ranges[ik][0])/(sp-1)  # increment of knob value
    knobs.append(knob_ranges[ik][0] + dkval * setting)  # calc knob val and add to list
    idx -= setting * sp_pow        # prepare to calc next "digit" in next loop
  return knobs


if __name__ == "__main__":
    pass
# EOF
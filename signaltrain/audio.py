
__author__ = 'S.H. Hawley'

# imports
import numpy as np
import torch
from torch.autograd import Variable
import librosa
from multiprocessing.pool import ThreadPool as Pool
from functools import partial
import scipy.signal as signal
from torch.utils.data.dataset import Dataset
#import matplotlib.pyplot as plt   # just for debugging

# Note: torchaudio is also a thing! requires sox. See http://pytorch.org/audio/,  https://github.com/pytorch/audio


# TODO: skeleton for future Dataset class for Pytorch Dataloaders.  Not finished.
#    Reading from http://pytorch.org/audio/datasets.html
class AudioDataset(Dataset):
    def __init__(self, stuff):
        # function is where the initial logic happens like reading a csv, assigning transforms etc.
        pass

    def __getitem__(self, index):
        # function returns the data and labels. This function is called from dataloader like this:
        #      img, label = MyCustomDataset.__getitem__(99)  # For 99th item
        # this is where torchvision transforms are often used
        return (img, label)

    def __len__(self):
        return count # of how many examples(images?) you have



# reads a file. currently maps stereo to mono by throwing out the right channel
#   TODO: eventually we'd want to handle stereo somehow (e.g. for stereo effects)
def read_audio_file(filename):
    sig, fs = librosa.load(filename)
    if (len(sig.shape) > 1):   # convert stereo to mono
        return sig[0], fs
    else:
        return sig, fs


def write_audio_file(filename, sig, fs):
    librosa.output.write_wav(filename, sig, fs)
    return


# this generates a time-decaying sine wave, similar to plucking a string
def gen_pluck(length, t=None, amp=None, freq=None, decay=None, t0=0.0):
    if amp is None:
        amp = (0.6*np.random.random()+0.3)*np.random.choice([-1,1])
    if freq is None:
        freq = 300*np.random.random()
    if decay is None:
        decay = 10*np.random.random()
    if t is None:
        t = torch.linspace(0,1,length)
    pluck = amp * torch.exp(-decay * (t-t0) ) * torch.sin(freq* (t-t0))
    return pluck


# this operates using only one processor, and time-aligns one 'event' (which may be a 'pluck')
def ta_oneproc(input_sigs, target_sigs, chunk_size, event_len, num_events, strength, chunk_index):
    #  strength is a 0..1 'knob' that parameterizes the amount of time-shift applied: 0=no effect, 1=full ('on the grid')
    sig_length = chunk_size

    base_event = gen_pluck( int(event_len*1.5))            # 'base event' just the preceding event, to the right
    target_sigs[chunk_index, 0: base_event.size()[-1]] = base_event
    input_sigs[chunk_index, 0: base_event.size()[-1]] = base_event

    for eventnum in range(num_events):
        event = gen_pluck(event_len)

        # figure out where the erroneous input event should be, and where the target event should be
        grid_index = int(eventnum*event_len) + int(chunk_size/2)          # this is the target index value for "hard editing" / "on the grid"
        random_shift =  int ( (event_len/5)* (2*np.random.random()-1) )   # amount 'off' to place input event from the grid; the 1/5 is just an estimation

        input_index = grid_index + random_shift
        target_index =  int( strength*grid_index + (1.0-strength)*input_index )   # here's where the strength knob does its work

        itarget_bgn = target_index
        itarget_end = min(itarget_bgn + event_len, sig_length-1)
        target_sigs[chunk_index, itarget_bgn:itarget_end] = event[0 : itarget_end - itarget_bgn]

        # input: randomly shift it for input
        iinput_bgn = max(0, input_index )  # just don't let it go off the front end; TODO: this works but is sloppy;
        iinput_end = min(iinput_bgn + event_len, sig_length-1)
        input_sigs[chunk_index, iinput_bgn:iinput_end] = event[0: iinput_end - iinput_bgn ]

    return # note we don't have to return arrays because ThreadPool shares memory


# this runs in parallel, calling ta_oneproc many times to do multiple time-alignments
def gen_timealign_pairs(chunk_size, num_chunks, num_events=1, parallel=True, strength=1.0):
    input_sigs = torch.zeros((num_chunks, chunk_size))
    target_sigs = input_sigs.clone()#  0.1*torch.randn((num_chunks, chunk_size))
    sig_length = chunk_size
    event_len = int( sig_length / (num_events+1) )
    chunk_indices = tuple( range(num_chunks) )

    if (parallel):
        pool = Pool()
        pool.map( partial(ta_oneproc, input_sigs, target_sigs, chunk_size, event_len, num_events, strength), chunk_indices)
        pool.close()
        pool.join()
    else:
        for chunk_index in chunk_indices:
            ta_oneproc(input_sigs, target_sigs, chunk_size, event_len, num_events, strength, chunk_index)
    return input_sigs, target_sigs


# generate pitch-shifted pair, on one processor
def psp_oneproc(input_sigs, target_sigs, fs, amp_fac, freq_fac, num_waves, chunk_index):
    sig_length = input_sigs.shape[1]
    for n in range(num_waves):
        # randomize the signal
        amp = 0.2*np.random.rand()    # stay bounded well below 1.0
        freq = 2 * np.pi * ( 400 + 400*np.random.rand() )

        # learn the adaptive filter for the following input -> target pair: different amp, freq & phase
        input_sigs[chunk_index]  +=           amp * torch.cos(           freq * torch.arange(sig_length) / fs)
        target_sigs[chunk_index] += amp_fac * amp * torch.sin(freq_fac * freq * torch.arange(sig_length) / fs)
    return  # note we don't have to return arrays because ThreadPool shares memory


# this generates groups of 'pitch shifted' pairs.  Not a true pitch-shifting effect, just something
#   I used temporarily until RenderMan VST host got upgraded to Python 3.6
# Runs in parallel, calls psp_oneproc many times
def gen_pitch_shifted_pairs(chunk_size, fs, amp_fac, freq_fac, num_waves, num_chunks, parallel=True):
        input_sigs = torch.zeros((num_chunks, chunk_size))
        target_sigs = torch.zeros((num_chunks, chunk_size))
        # generate them in parallel threads that all share the input_sigs and target_sigs arrays
        chunk_indices = tuple( range(num_chunks) )
        if (parallel):
            pool = Pool()
            pool.map( partial(psp_oneproc, input_sigs, target_sigs, fs, amp_fac, freq_fac, num_waves), chunk_indices)
            pool.close()
            pool.join()
        else:
            for chunk_index in chunk_indices:
                psp_oneproc(input_sigs, target_sigs, fs, amp_fac, freq_fac, num_waves, chunk_index)
        return input_sigs, target_sigs


# Generates various 'fake' audio wave forms -- synthetic data
def gen_input_sample(t, chooser=None):
    x = np.copy(t)*0.0
    if (chooser is None):
        chooser = np.random.randint(0,4)
    #chooser = 6
    #print("   make_input_signal: chooser = ",chooser)
    if (0 == chooser):
        amp = 0.4+0.4*np.random.random()
        freq = 30*np.random.random()    # sin, with random start & freq
        t0 = np.random.random()
        x = amp*np.sin(freq*(t-t0))
        x[np.where(t < t0)] = 0.0
        return x
    elif (1 == chooser):                # fixed sine wave
        freq = 5+150*np.random.random()
        amp = 0.4+0.4*np.random.random()
        global global_freq
        global_freq = freq
        return amp*np.sin(freq*t)
    elif (2 == chooser):                  # "pluck"
        amp0 = (0.6*np.random.random()+0.3)*np.random.choice([-1,1])
        t0 = (2*np.random.random()-1)*0.3
        decay = 8*np.random.random()
        freq = 300*np.random.random()
        x = amp0*np.exp(-decay * (t-t0) ) * np.sin(freq* (t-t0))
        x[np.where(t < t0)] = 0   # without this, it grow exponentially 'to the left'
        return x
    elif (3 == chooser):                # ramp up then down
        height = (0.4*np.random.random()+0.2)*np.random.choice([-1,1])
        width = 0.3*np.random.random()/4   # half-width actually
        t0 = 2*width + 0.4*np.random.random() # make sure it fits
        x = height* ( 1 - np.abs(t-t0)/width )
        x[np.where(t < (t0-width))] = 0
        x[np.where(t > (t0+width))] = 0
        #x += 0.01
        return x
    elif (4 == chooser):                # 'box'
        height = (0.3*np.random.random()+0.2)*np.random.choice([-1,1])
        x = height*np.ones(t.shape[0])
        t1 = np.random.random()/2
        t2 = t1 + np.random.random()/2
        x[np.where(t<t1)] = 0.0
        x[np.where(t>t2)] = 0.0
        #x += 0.01
        return x
    elif (5 == chooser):                 # "bunch of spikes"
        n_spikes = 100
        for i in range(n_spikes):   # arbitrarily make a 'spike' somewhere, surrounded by silence
          loc = int(np.random.random()*len(t)-2)+1
          height = np.random.random()-0.5    # -0.5...0.5
          x[loc] = height
          x[loc+1] = height/2  # widen the spike a bit
          x[loc-1] = height/2
        x = x + 0.1*np.random.normal(0.0,scale=0.1,size=x.size)    # throw in noise
        return x
    elif (6 == chooser):                # white noise
        amp = 0.2+0.2*np.random.random()
        #amp = 2.0*np.random.random()
        x = amp*(2*np.random.random(t.shape[0])-1)
        return x
    else:
        x= 0.5*(make_input_signal(t)+make_input_signal(t)) # superposition of previous
        return x
    return np.copy(t)   # failsafe return just in case of typo above


# low pass filter
def lowpass(x, fc_fac=1.0):
    fc = fc_fac / len(x)
    b, a = signal.butter(1, fc, analog=False)
    zi = signal.lfilter_zi(b, a)
    z, _ = signal.lfilter(b, a, x, zi=zi*x[0])
    return z


# this is a echo or delay effect  TODO: note the 'effect' string name for this is 'delay', not 'echo'. Pick one.
def echo(x, delay_samples=1487, echoes=2, ratio=0.6, dtype=np.float32):
    # ratio = redution ratio
    y = np.copy(x).astype(dtype)
    for i in range(echoes):
        ip1 = i+1
        delay_length = ip1 * delay_samples
        x_delayed = np.roll(x, delay_length)
        x_delayed[0:delay_length] = 0
        y += pow(ratio, ip1) * x_delayed
    return y


# simple compressor, thanks to Eric Tarr
def compressor(x, thresh=-35, ratio=3, attack=2000, dtype=np.float32):
    fc = 1.0/(attack)               # this is like 1/attack time
    b, a = signal.butter(1, fc, analog=False)
    zi = signal.lfilter_zi(b, a)
    dB = 20*np.log10(np.abs(x) + 1e-8)
    in_env, _ = signal.lfilter(b, a, dB, zi=zi*dB[0])  # input envelope calculation
    out_env = np.copy(in_env).astype(dtype)               # output envelope
    i = np.where(in_env >  thresh)          # compress where input env exceeds thresh
    out_env[i] = thresh + (in_env[i]-thresh)/ratio
    gain = np.power(10.0,(out_env-in_env)/10)
    y = np.copy(x) * gain
    return y


'''-----------------------------------------------------------------------------
   'functions' is a repository of various audio effects, which in some cases are
    literally just simple (time-independent) functions
-------------------------------------------------------------------------------'''
def functions(x, f='id'):                # function to be learned
    if ('id' == f):
        return x 						# identity function
    elif ('x^2'==f):
        return x**2   					# given an x on [0,1], this should work
    elif ('clip' == f):
        return np.clip(x,-0.3,0.3)  	# hard limiter, clips signal
    elif ('sin' == f):
        return np.sin(4*x)              # just made this up
    elif ('dec_cos' == f):
        y = np.exp(-2 * x ) * np.cos(40* x) # decaying cosine
        return y
    elif ('wiggle' == f):
        y  = np.exp(-1*(x+0.7))*np.cos(20*x)			# decaying cos  wave
        y = y + np.exp(-40*(x-0.5)**2)					# plus  gaussian
        return y
    # low pass filter
    elif ('lpf' ==f):
        return lowpass(f)
    elif ('delay' == f) or ('echo' == f):
        return echo(x)
    elif ('comp' == f):
        return compressor(x)
    else:
        print("functions: error invalid type")
        assert(False)  # probably more graceful ways to exit, but I want to know immediately



# Chop'n'Stack!  Cuts up a signal into chunks and stacks them vertically
def chopnstack(sig, size=8192, dtype=np.float32):
    # Note the chopnstack pair uses numpy instead of pytorch.  you can convert later
    sig_len = len(sig)
    n = int(np.ceil(sig_len*1.0/size))             # number of chunks, round up
    buffer = np.zeros(n * size).astype(dtype)   # for zero-pad, embed in larger array
    buffer[0:sig_len] = sig
    return np.vstack(np.split(buffer, n))

# Inverse of Chop'n'Stack: takes vertical stack and produces 1-D signal
def inv_chopnstack(stack, orig_len=None):
    if (None == orig_len):
        return stack.reshape(-1)
    else:
        return stack.reshape(-1)[0:orig_len]



'''---------------------------------------------------------------------------------------
   gen_audio is the main routine to provide audio data.
   TODO: should probably try adding mu-law companding to see if that helps with SNR
   Inputs:
         sig_length:  is actually the totally length (in samples) of the the entire dataset,
                     conceived as if it were just one file.  TODO: change this
         chunk_size: chop up the signal into chunks of this length.
         effect:     string corresponding to a name of an audio effect
                     Some of these just generate generic input & apply the effect to that,
                     other 'effects' may involve generating input & output together
        input_var & target_var: These *can* be passed in so that memory gets freed up
                                before generating new data. (It'd be nice to be able to
                                do everything 'in place', but w/ PyTorch vs. numpy, and
                                GPU vs CPU, this can get complicated.)
---------------------------------------------------------------------------------------'''
def gen_audio(sig_length, chunk_size=8192, effect='ta', input_var=None, target_var=None):
    my_dtype = np.float32

    # Free up pytorch tensor memory usage before generating new data
    if (input_var is not None) and (target_var is not None):
        del input_var, target_var

    num_chunks = int(sig_length / chunk_size)
    if ('ps' == effect):    # pitch shift
        fs = 44100.
        num_waves = 20
        amp_fac = 0.43
        freq_fac = 0.31
        input_stack, target_stack = gen_pitch_shifted_pairs(chunk_size, fs, amp_fac, freq_fac, num_waves, num_chunks)

    elif ('ta' == effect):  # time align
        input_stack, target_stack = gen_timealign_pairs(chunk_size, num_chunks, strength=0.5)
    else:                   # other effects, where target can be generated from input instead of both together
        # Generate input signal
        clips_per_chunk = 4
        clip_size = int( chunk_size / clips_per_chunk)
        input_sig = np.zeros(sig_length).astype(my_dtype)
        #input_sig +=  (2*np.random.rand(sig_length)-1)*1e-6  # just a little noise to help it not be zero
        t = np.linspace(0,1,num=clip_size).astype(my_dtype)
        num_sample_clips = int(sig_length / clip_size)  # just overwrite some random amount
        for i in range(num_sample_clips):
            start_ind = i * clip_size
            sample_type = 2  # 'pluck'
            input_sig[start_ind:start_ind + clip_size] = gen_input_sample(t,chooser=sample_type)

        if ('delay' == effect):
            input_sig *= 0.5    # for delay, just make it smaller to avoid any clipping that may occur

        #print("Plotting input_sig:")
        #fig = plt.figure()
        #plt.plot(input_sig[0:chunk_size])    # not the whole signal, just the first chunk
        #plt.show()

        # Apply the effect, whatever it is
        target_sig = functions(input_sig, f=effect)

        # chop up the input & target signal
        input_stack = torch.from_numpy( chopnstack(input_sig, size=chunk_size) )
        target_stack = torch.from_numpy( chopnstack(target_sig, size=chunk_size) )

    input_var = Variable(input_stack)
    target_var = Variable(target_stack, requires_grad=False)
    if torch.has_cudnn:
        input_var = input_var.cuda()
        target_var = target_var.cuda()

    #print("   Leaving gen_data with input_stack.size() = ",input_stack.size())
    return input_var, target_var

# EOF

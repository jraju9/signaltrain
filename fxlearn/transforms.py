# -*- coding: utf-8 -*-
__author__ = 'S. Venkataramani, S.I. Mimilakis'
__copyright__ = 'ECE Illinois, MacSeNet'

# imports
import numpy as np
import torch
import torch.nn as nn
from scipy.signal import cosine


def core_modulation(freq_subbands, window_size):
    """
        Method to produce Analysis and Synthesis matrices.
        -https://github.com/Js-Mim/ASP/
        Arguments              :
            freq_subbands      :   (int) Number of subbands
            window_size        :   (int) Window size
        Returns                :
            Cos                :   (2D Array) Cosine Modulated Matrix
    """
    w = cosine(window_size)
    # Initialize Storing Variables
    cos_an = np.zeros((freq_subbands, window_size), dtype=np.float32)

    # Generate Matrices
    for k in xrange(0, freq_subbands):
        for n in xrange(0, window_size):
            cos_an[k, n] = w[n] * np.cos(np.pi / freq_subbands * (k + 0.5) *
                                         (n + 0.5 + freq_subbands / 2)) * np.sqrt(2./freq_subbands)

    return cos_an.astype(np.float32)


def tied_transform(analysis, x_ft, hop):
    """
        A method to compute an orthogonal transform for audio signals.
        It will use the analysis weights to perform the reconstruction, via
        transposed convolution.

        Arguments              :
            analysis           :   (object)         Pytorch module
            x_ft               :   (Torch Tensor)   Tensor containing the transformed signal
            hop                :   (int)            Hop-size
        Returns                :
            wave_from          :   (Torch Tensor)   Reconstructed waveform
    """

    sz = analysis.conv_analysis.weight.size()[0]
    wave_form = nn.functional.conv_transpose2d(torch.transpose(x_ft, 2, 1).unsqueeze(3),
                                               analysis.conv_analysis.weight.unsqueeze(3),
                                               padding=(sz, 0), stride=(hop, 1))
    return wave_form.squeeze(3)


def core_modulation(freq_subbands, window_size):
    """
        Method to produce Analysis and Synthesis matrices.
        -https://github.com/Js-Mim/ASP/

        Arguments              :
            freq_subbands      :   (int) Number of subbands
            window_size        :   (int) Window size
        Returns                :
            Cos                :   (2D Array) Cosine Modulated Polyphase Matrix
    """
    w = cosine(window_size)

    # just added the following profiling to compare the speed of the two methods
    #from profilehooks import profile
    #@profile
    def orig_method(freq_subbands, window_size, w):
        # Initialize Storing Variables
        cos_an = np.zeros((freq_subbands, window_size), dtype=np.float32)
        # Generate Matrices
        for k in xrange(0, freq_subbands):
            for n in xrange(0, window_size):
                cos_an[k, n] = w[n] * np.cos(np.pi / freq_subbands * (k + 0.5) *
                                         (n + 0.5 + freq_subbands / 2)) * np.sqrt(2. / freq_subbands)
        return cos_an

    #@profile
    def scott_method(freq_subbands, window_size, w):
        # Generate Matrices
        kvec = np.arange(0, freq_subbands) + 0.5
        nvec = np.arange(0, window_size) + 0.5 + freq_subbands/2
        cos_an = w * np.cos(np.pi / freq_subbands * kvec[np.newaxis].T * nvec) * np.sqrt(2. / freq_subbands)
        return cos_an

    method = 'scott'
    if ('scott' == method):
        cos_an = scott_method(freq_subbands, window_size, w)
    else:
        cos_an = orig_method(freq_subbands, window_size, w)

    return cos_an.astype(np.float32)


class Analysis(nn.Module):
    """
        Class for building the analysis part
        of the Front-End ('Fe').
    """
    def __init__(self, ft_size=1024, w_size=2048, hop_size=1024, shrink=False):
        super(Analysis, self).__init__()

        # Parameters
        self.batch_size = None
        self.time_domain_samples = None
        self.sz = ft_size
        self.wsz = w_size
        self.hop = hop_size
        self.shrink = shrink

        # Activation Funtion
        self.h_tanh = torch.nn.Hardtanh()

        # Analysis 1D CNN
        self.conv_analysis = nn.Conv1d(1, self.sz, self.wsz,
                                       padding=self.sz, stride=self.hop, bias=True)

        # Custom Initialization with Fourier matrix
        self.initialize()

    def initialize(self):
        fa_matrix = core_modulation(self.sz, self.wsz)
        if torch.has_cudnn:
            self.conv_analysis.weight.data.copy_(torch.from_numpy(fa_matrix[:, None, :]).cuda())
        else:
            self.conv_analysis.weight.data.copy_(torch.from_numpy(fa_matrix[:, None, :]))

    def forward(self, wave_form):

        batch_size = wave_form.size(0)
        time_domain_samples = wave_form.size(1)
        wave_form = wave_form.view(batch_size, 1, time_domain_samples)
        x_ft = torch.transpose(self.conv_analysis(wave_form), 2, 1)

        if self.shrink:
            x_ft = self.h_tanh(x_ft)

        return x_ft


class Synthesis(nn.Module):
    """
        Class for building the synthesis part
        of the Front-End ('Fe').
    """

    def __init__(self, ft_size=1024, w_size=2048, hop_size=1024):
        super(Synthesis, self).__init__()

        # Parameters
        self.batch_size = None
        self.time_domain_samples = None
        self.sz = ft_size
        self.wsz = w_size
        self.hop = hop_size
        self.half_N = int(self.sz / 2 + 1)

        # Synthesis 1D CNN
        self.conv_synthesis = nn.ConvTranspose1d(self.sz, 1, self.wsz,
                                                 padding=0, stride=self.hop, bias=False)

        # Activation functions
        self.h_tanh = torch.nn.Hardtanh()
        self.tanh = torch.nn.Tanh()

        # Custom Initialization with DCT-TypeIV matrix
        self.initialize()

    def initialize(self):
        fs_matrix = core_modulation(self.sz, self.wsz)

        if torch.has_cudnn:
            self.conv_synthesis.weight.data.copy_(torch.from_numpy(fs_matrix[:, None, :]).cuda())
        else:
            self.conv_synthesis.weight.data.copy_(torch.from_numpy(fs_matrix[:, None, :]))

    def forward(self, x_ft):
        wave_form = self.tanh(self.conv_synthesis(torch.transpose(x_ft, 2, 1)))
        wave_form = wave_form[:, :, self.sz:]
        wave_form = wave_form[:, :, :-self.sz]

        return wave_form




class FNNAnalysis(nn.Module):
    """
        Class for building the analysis part
        of the Front-End ('Fe').
    """

    def __init__(self, ft_size=1024):
        super(FNNAnalysis, self).__init__()

        # Parameters
        self.batch_size = None
        self.time_domain_samples = None
        self.sz = ft_size
        self.half_N = int(self.sz / 2 + 1)

        # Analysis
        self.fnn_analysis_real = nn.Linear(self.sz, self.sz, bias=False)
        self.fnn_analysis_imag = nn.Linear(self.sz, self.sz, bias=False)

        # Custom Initialization with Fourier matrix
        self.initialize()

    def initialize(self):
        f_matrix = np.fft.fft(np.eye(self.sz))

        f_matrix_real = (np.real(f_matrix)).astype(np.float32)
        f_matrix_imag = (np.imag(f_matrix)).astype(np.float32)

        if torch.has_cudnn:
            self.fnn_analysis_real.weight.data.copy_(torch.from_numpy(f_matrix_real).cuda())
            self.fnn_analysis_imag.weight.data.copy_(torch.from_numpy(f_matrix_imag).cuda())
        else:
            self.fnn_analysis_real.weight.data.copy_(torch.from_numpy(f_matrix_real))
            self.fnn_analysis_imag.weight.data.copy_(torch.from_numpy(f_matrix_imag))

    def forward(self, wave_form):
        an_real = self.fnn_analysis_real(wave_form)[:, :, :self.half_N]
        an_imag = self.fnn_analysis_imag(wave_form)[:, :, :self.half_N]

        return an_real, an_imag


class FNNSynthesis(nn.Module):
    """
        Class for building the synthesis part
        of the Front-End ('Fe').
    """

    def __init__(self, ft_size=1024, random_init=False):
        super(FNNSynthesis, self).__init__()

        # Parameters
        self.batch_size = None
        self.time_domain_samples = None
        self.sz = ft_size
        self.half_N = int(self.sz / 2 + 1)

        # Synthesis
        self.fnn_synthesis_real = nn.Linear(self.sz, self.sz, bias=False)
        self.fnn_synthesis_imag = nn.Linear(self.sz, self.sz, bias=False)

        # Tanh
        self.tanh = torch.nn.Tanh()

        if random_init:
            # Random Initialization
            self.initialize_random()
        else:
            # Custom Initialization with Fourier matrix
            self.initialize()

    def initialize(self):
        print('Initializing with Fourier bases')
        f_matrix = np.fft.fft(np.eye(self.sz), norm='ortho')

        f_matrix_real = (np.real(f_matrix)).astype(np.float32)
        f_matrix_imag = (np.imag(f_matrix)).astype(np.float32)

        if torch.has_cudnn:
            self.fnn_synthesis_real.weight.data.copy_(torch.from_numpy(f_matrix_real.T).cuda())
            self.fnn_synthesis_imag.weight.data.copy_(torch.from_numpy(f_matrix_imag.T).cuda())

        else:
            self.fnn_synthesis_real.weight.data.copy_(torch.from_numpy(f_matrix_real.T))
            self.fnn_synthesis_imag.weight.data.copy_(torch.from_numpy(f_matrix_imag.T))

    def initialize_random(self):
        print('Initializing randomly')
        nn.init.xavier_uniform(self.fnn_synthesis_real.weight)
        nn.init.xavier_uniform(self.fnn_synthesis_imag.weight)

    def forward(self, real, imag):

        real = torch.cat((real, Synthesis.flip(real[:, :, 1:-1].contiguous(), 2)), 2)
        imag = torch.cat((imag, Synthesis.flip(-imag[:, :, 1:-1].contiguous(), 2)), 2)

        wave_form = self.tanh(self.fnn_synthesis_real(real) + self.fnn_synthesis_imag(imag))
        return wave_form

    @staticmethod
    def flip(x, dim):
        # https://github.com/pytorch/pytorch/issues/229
        xsize = x.size()
        dim = x.dim() + dim if dim < 0 else dim
        x = x.view(-1, *xsize[dim:])
        x = x.view(x.size(0), x.size(1), -1)[:, getattr(torch.arange(x.size(1) - 1,
                                                         -1, -1), ('cpu', 'cuda')[x.is_cuda])().long(), :]
        return x.view(xsize)

# EOF

# -*- coding: utf-8 -*-
""" Initialize the component for the CNMF

contain a list of functions to initialize the neurons and the corresponding traces with different set of methods
liek ICA PCA, greedy roi



"""
#\package Caiman/source_extraction/cnmf/
#\version   1.0
#\copyright GNU General Public License v2.0
#\date Created on Tue Jun 30 21:01:17 2015
#\author: Eftychios A. Pnevmatikakis

from __future__ import division
from __future__ import print_function
from builtins import range
from past.utils import old_div
import numpy as np
from sklearn.decomposition import NMF, FastICA
from skimage.transform import downscale_local_mean, resize
from skimage.morphology import disk
import scipy.ndimage as nd
from scipy.ndimage import label as bwlabel
from scipy.ndimage.measurements import center_of_mass
from scipy.ndimage.filters import correlate
import scipy.sparse as spr
import scipy
import caiman
from caiman.source_extraction.cnmf.deconvolution import deconvolve_ca
from caiman.source_extraction.cnmf.pre_processing import get_noise_fft as \
    get_noise_fft
import cv2
import sys
#%%

if sys.version_info >= (3, 0):
    def xrange(*args, **kwargs):
        return iter(range(*args, **kwargs))


def initialize_components(Y, K=30, gSig=[5, 5], gSiz=None, ssub=1, tsub=1, nIter=5, maxIter=5, nb=1,
                          kernel=None, use_hals=True, normalize_init=True, img=None, method='greedy_roi',
                          max_iter_snmf=500, alpha_snmf=10e2, sigma_smooth_snmf=(.5, .5, .5),
                          perc_baseline_snmf=20, options_local_NMF=None, **kwargs):
    """
    Initalize components

    This method uses a greedy approach followed by hierarchical alternative least squares (HALS) NMF.
    Optional use of spatio-temporal downsampling to boost speed.

    Parameters:
    ----------
    Y: np.ndarray
         d1 x d2 [x d3] x T movie, raw data.

    K: [optional] int
        number of neurons to extract (default value: 30). Maximal number for method 'corr_pnr'.

    tau: [optional] list,tuple
        standard deviation of neuron size along x and y [and z] (default value: (5,5).

    gSiz: [optional] list,tuple
        size of kernel (default 2*tau + 1).

    nIter: [optional] int
        number of iterations for shape tuning (default 5).

    maxIter: [optional] int
        number of iterations for HALS algorithm (default 5).

    ssub: [optional] int
        spatial downsampling factor recommended for large datasets (default 1, no downsampling).

    tsub: [optional] int
        temporal downsampling factor recommended for long datasets (default 1, no downsampling).

    kernel: [optional] np.ndarray
        User specified kernel for greedyROI (default None, greedy ROI searches for Gaussian shaped neurons)

    use_hals: [optional] bool
        Whether to refine components with the hals method

    normalize_init: [optional] bool
        Whether to normalize_init data before running the initialization

    img: optional [np 2d array]
        Image with which to normalize. If not present use the mean + offset

    method: str
        Initialization method 'greedy_roi' or 'sparse_nmf'

    max_iter_snmf: int
        Maximum number of sparse NMF iterations

    alpha_snmf: scalar
        Sparsity penalty

    Returns:
    --------

    Ain: np.ndarray
        (d1*d2[*d3]) x K , spatial filter of each neuron.

    Cin: np.ndarray
        T x K , calcium activity of each neuron.

    center: np.ndarray
        K x 2 [or 3] , inferred center of each neuron.

    bin: np.ndarray
        (d1*d2[*d3]) x nb, initialization of spatial background.

    fin: np.ndarray
        nb x T matrix, initalization of temporal background

    Raise:
    ------
        Exception("Unsupported method")

        Exception('You need to define arguments for local NMF')

    """
    if method == 'local_nmf':
        tsub_lnmf = tsub
        ssub_lnmf = ssub
        tsub = 1
        ssub = 1

    if gSiz is None:
        gSiz = 2 * np.asarray(gSig) + 1

    d, T = np.shape(Y)[:-1], np.shape(Y)[-1]
    # rescale according to downsampling factor
    gSig = np.round(np.asarray(gSig) / ssub).astype(np.int)
    gSiz = np.round(np.asarray(gSiz) / ssub).astype(np.int)

    print('Noise Normalization')
    if normalize_init is True:
        if img is None:
            img = np.mean(Y, axis=-1)
            img += np.median(img)

        Y = old_div(Y, np.reshape(img, d + (-1,), order='F'))
        alpha_snmf /= np.mean(img)

    # spatial downsampling
    mean_val = np.mean(Y)
    if ssub != 1 or tsub != 1:
        print("Spatial Downsampling ...")
        Y_ds = downscale_local_mean(Y, tuple([ssub] * len(d) + [tsub]), cval=mean_val)
    else:
        Y_ds = Y

    print('Roi Extraction...')
    if method == 'greedy_roi':
        Ain, Cin, _, b_in, f_in = greedyROI(
            Y_ds, nr=K, gSig=gSig, gSiz=gSiz, nIter=nIter, kernel=kernel, nb=nb)

        if use_hals:
            print('(Hals) Refining Components...')
            Ain, Cin, b_in, f_in = hals(Y_ds, Ain, Cin, b_in, f_in, maxIter=maxIter)
    elif method == 'corr_pnr':
        Ain, Cin, _, b_in, f_in = greedyROI_corr(Y_ds, max_number=K,
                                                 g_size=gSiz[0], g_sig=gSig[0], nb=nb, **kwargs)

    elif method == 'sparse_nmf':
        Ain, Cin, _, b_in, f_in = sparseNMF(Y_ds, nr=K, nb=nb, max_iter_snmf=max_iter_snmf, alpha=alpha_snmf,
                                            sigma_smooth=sigma_smooth_snmf, remove_baseline=True, perc_baseline=perc_baseline_snmf)

    elif method == 'pca_ica':
        Ain, Cin, _, b_in, f_in = ICA_PCA(Y_ds, nr=K, sigma_smooth=sigma_smooth_snmf,  truncate=2, fun='logcosh',
                                          max_iter=max_iter_snmf, tol=1e-10, remove_baseline=True, perc_baseline=perc_baseline_snmf, nb=nb)

    elif method == 'local_nmf':
        # todo check this unresolved reference
        from SourceExtraction.CNMF4Dendrites import CNMF4Dendrites
        from SourceExtraction.AuxilaryFunctions import GetCentersData
        # Get initialization for components center
        print(Y_ds.transpose([2, 0, 1]).shape)
        if options_local_NMF is None:
            raise Exception('You need to define arguments for local NMF')
        else:
            NumCent = options_local_NMF.pop('NumCent', None)
            # Max number of centers to import from Group Lasso intialization - if 0,
            # we don't run group lasso
            cent = GetCentersData(Y_ds.transpose([2, 0, 1]), NumCent)
            sig = Y_ds.shape[:-1]
            # estimate size of neuron - bounding box is 3 times this size. If larger
            # then data, we have no bounding box.
            cnmf_obj = CNMF4Dendrites(sig=sig, verbose=True, adaptBias=True, **options_local_NMF)

        # Define CNMF parameters
        _, _, _ = cnmf_obj.fit(np.array(Y_ds.transpose([2, 0, 1]), dtype=np.float), cent)

        Ain = cnmf_obj.A
        Cin = cnmf_obj.C
        b_in = cnmf_obj.b
        f_in = cnmf_obj.f

    else:

        print(method)
        raise Exception("Unsupported method")

    K = np.shape(Ain)[-1]
    ds = Y_ds.shape[:-1]

    Ain = np.reshape(Ain, ds + (K,), order='F')

    if len(ds) == 2:
        Ain = resize(Ain, d + (K,), order=1)

    else:  # resize only deals with 2D images, hence apply resize twice
        Ain = np.reshape([resize(a, d[1:] + (K,), order=1)
                          for a in Ain], (ds[0], d[1] * d[2], K), order='F')
        Ain = resize(Ain, (d[0], d[1] * d[2], K), order=1)

    Ain = np.reshape(Ain, (np.prod(d), K), order='F')
    b_in = np.reshape(b_in, ds + (nb,), order='F')

    if len(ds) == 2:
        b_in = resize(b_in, d + (nb,), order=1)
    else:
        b_in = np.reshape([resize(b, d[1:] + (nb,), order=1)
                           for b in b_in], (ds[0], d[1] * d[2], nb), order='F')
        b_in = resize(b_in, (d[0], d[1] * d[2], nb), order=1)

    b_in = np.reshape(b_in, (np.prod(d), nb), order='F')
    Cin = resize(Cin, [K, T])
    f_in = resize(np.atleast_2d(f_in), [nb, T])
    center = np.asarray([center_of_mass(a.reshape(d, order='F')) for a in Ain.T])

    if normalize_init is True:
        Ain = Ain * np.reshape(img, (np.prod(d), -1), order='F')
        b_in = b_in * np.reshape(img, (np.prod(d), -1), order='F')

    return Ain, Cin, b_in, f_in, center

#%%


def ICA_PCA(Y_ds, nr, sigma_smooth=(.5, .5, .5),  truncate=2, fun='logcosh', max_iter=1000, tol=1e-10, remove_baseline=True, perc_baseline=20, nb=1):
    """ Initialization using ICA and PCA. DOES NOT WORK WELL WORK IN PROGRESS"

    Parameters:
    -----------

    Returns:
    --------


    """
    print("not a function to use in the moment ICA PCA \n")
    m = scipy.ndimage.gaussian_filter(np.transpose(
        Y_ds, [2, 0, 1]), sigma=sigma_smooth, mode='nearest', truncate=truncate)
    if remove_baseline:
        bl = np.percentile(m, perc_baseline, axis=0)
        m1 = np.maximum(0, m - bl)
    else:
        bl = 0
        m1 = m
    pca_comp = nr

    T, d1, d2 = np.shape(m1)
    d = d1 * d2
    yr = np.reshape(m1, [T, d], order='F')

    [U, S, V] = scipy.sparse.linalg.svds(yr, pca_comp)
    S = np.diag(S)
    whiteningMatrix = np.dot(scipy.linalg.inv(S), U.T)
    whitesig = np.dot(whiteningMatrix, yr)
    f_ica = FastICA(whiten=False, fun=fun, max_iter=max_iter, tol=tol)
    S_ = f_ica.fit_transform(whitesig.T)
    A_in = f_ica.mixing_
    A_in = np.dot(A_in, whitesig)

    masks = np.reshape(A_in.T, (d1, d2, pca_comp), order='F').transpose([2, 0, 1])

    masks = np.array(caiman.base.rois.extractROIsFromPCAICA(masks)[0])

    if masks.size > 0:
        C_in = caiman.base.movies.movie(m1).extract_traces_from_masks(np.array(masks)).T
        A_in = np.reshape(masks, [-1, d1 * d2], order='F').T

    else:

        A_in = np.zeros([d1 * d2, pca_comp])
        C_in = np.zeros([pca_comp, T])

    m1 = yr.T - A_in.dot(C_in) + np.maximum(0, bl.flatten())[:, np.newaxis]

    model = NMF(n_components=nb, init='random', random_state=0)

    b_in = model.fit_transform(np.maximum(m1, 0))
    f_in = model.components_.squeeze()

    center = caiman.base.rois.com(A_in, d1, d2)

    return A_in, C_in, center, b_in, f_in
#%%


def sparseNMF(Y_ds, nr,  max_iter_snmf=500, alpha=10e2, sigma_smooth=(.5, .5, .5), remove_baseline=True, perc_baseline=20, nb=1, truncate=2):
    """
    Initilaization using sparse NMF

    Parameters:
    -----------

    max_iter_snm: int
        number of iterations

    alpha_snmf:
        sparsity regularizer

    sigma_smooth_snmf:
        smoothing along z,x, and y (.5,.5,.5)

    perc_baseline_snmf:
        percentile to remove frmo movie before NMF

    nb: int
        Number of background components    

    Returns:
    -------

    A: np.array
        2d array of size (# of pixels) x nr with the spatial components. Each column is
        ordered columnwise (matlab format, order='F')

    C: np.array
        2d array of size nr X T with the temporal components

    center: np.array
        2d array of size nr x 2 [ or 3] with the components centroids
    """
    m = scipy.ndimage.gaussian_filter(np.transpose(
        Y_ds, [2, 0, 1]), sigma=sigma_smooth, mode='nearest', truncate=truncate)
    if remove_baseline:
        bl = np.percentile(m, perc_baseline, axis=0)
        m1 = np.maximum(0, m - bl)
    else:
        bl = 0
        m1 = m

    mdl = NMF(n_components=nr, verbose=False, init='nndsvd', tol=1e-10,
              max_iter=max_iter_snmf, shuffle=True, alpha=alpha, l1_ratio=1)
    T, d1, d2 = np.shape(m1)
    d = d1 * d2
    yr = np.reshape(m1, [T, d], order='F')
    C = mdl.fit_transform(yr).T
    A = mdl.components_.T
    ind_good = np.where(np.logical_and((np.sum(A, 0) * np.std(C, axis=1))
                                       > 0, np.sum(A > np.mean(A), axis=0) < old_div(d, 3)))[0]

    ind_bad = np.where(np.logical_or((np.sum(A, 0) * np.std(C, axis=1))
                                     == 0, np.sum(A > np.mean(A), axis=0) > old_div(d, 3)))[0]
    A_in = np.zeros_like(A)

    C_in = np.zeros_like(C)
    A_in[:, ind_good] = A[:, ind_good]
    C_in[ind_good, :] = C[ind_good, :]
    A_in = A_in * (A_in > (.1 * np.max(A_in, axis=0))[np.newaxis, :])
    A_in[:3, ind_bad] = .0001
    C_in[ind_bad, :3] = .0001

    m1 = yr.T - A_in.dot(C_in) + np.maximum(0, bl.flatten())[:, np.newaxis]
    model = NMF(n_components=nb, init='random', random_state=0, max_iter=max_iter_snmf)
    b_in = model.fit_transform(np.maximum(m1, 0))
    f_in = model.components_.squeeze()
    center = caiman.base.rois.com(A_in, d1, d2)

    return A_in, C_in, center, b_in, f_in

#%%


def greedyROI(Y, nr=30, gSig=[5, 5], gSiz=[11, 11], nIter=5, kernel=None, nb=1):
    """
    Greedy initialization of spatial and temporal components using spatial Gaussian filtering

    Parameters:
    --------

    Y: np.array
        3d or 4d array of fluorescence data with time appearing in the last axis.

    nr: int
        number of components to be found

    gSig: scalar or list of integers
        standard deviation of Gaussian kernel along each axis

    gSiz: scalar or list of integers
        size of spatial component

    nIter: int
        number of iterations when refining estimates

    kernel: np.ndarray
        User specified kernel to be used, if present, instead of Gaussian (default None)

    nb: int
        Number of background components

    Returns:
    -------

    A: np.array
        2d array of size (# of pixels) x nr with the spatial components. Each column is
        ordered columnwise (matlab format, order='F')

    C: np.array
        2d array of size nr X T with the temporal components

    center: np.array
        2d array of size nr x 2 [ or 3] with the components centroids

    @Author: Eftychios A. Pnevmatikakis based on a matlab implementation by Yuanjun Gao
            Simons Foundation, 2015

    See Also:
    -------
    http://www.cell.com/neuron/pdf/S0896-6273(15)01084-3.pdf


    """
    print("Greedy initialization of spatial and temporal components using spatial Gaussian filtering")
    d = np.shape(Y)
    med = np.median(Y, axis=-1)
    Y = Y - med[..., np.newaxis]
    gHalf = np.array(gSiz) // 2
    gSiz = 2 * gHalf + 1
    # we initialize every values to zero
    A = np.zeros((np.prod(d[0:-1]), nr))
    C = np.zeros((nr, d[-1]))
    center = np.zeros((nr, Y.ndim - 1))

    rho = imblur(Y, sig=gSig, siz=gSiz, nDimBlur=Y.ndim - 1, kernel=kernel)
    v = np.sum(rho**2, axis=-1)

    for k in range(nr):
        # we take the highest value of the blurred total image and we define it as
        # the center of the neuron
        ind = np.argmax(v)
        ij = np.unravel_index(ind, d[0:-1])
        for c, i in enumerate(ij):
            center[k, c] = i

        # we define a squared size around it
        ijSig = [[np.maximum(ij[c] - gHalf[c], 0), np.minimum(ij[c] + gHalf[c] + 1, d[c])]
                 for c in range(len(ij))]
        # we create an array of it (fl like) and compute the trace like the pixel ij trough time
        dataTemp = np.array(Y[[slice(*a) for a in ijSig]].copy(), dtype=np.float)
        traceTemp = np.array(np.squeeze(rho[ij]), dtype=np.float)

        coef, score = finetune(dataTemp, traceTemp, nIter=nIter)
        C[k, :] = np.squeeze(score)
        dataSig = coef[..., np.newaxis] * score.reshape([1] * (Y.ndim - 1) + [-1])
        xySig = np.meshgrid(*[np.arange(s[0], s[1]) for s in ijSig], indexing='xy')
        arr = np.array([np.reshape(s, (1, np.size(s)), order='F').squeeze()
                        for s in xySig], dtype=np.int)
        indeces = np.ravel_multi_index(arr, d[0:-1], order='F')

        A[indeces, k] = np.reshape(coef, (1, np.size(coef)), order='C').squeeze()
        Y[[slice(*a) for a in ijSig]] -= dataSig.copy()
        if k < nr - 1:
            Mod = [[np.maximum(ij[c] - 2 * gHalf[c], 0),
                    np.minimum(ij[c] + 2 * gHalf[c] + 1, d[c])] for c in range(len(ij))]
            ModLen = [m[1] - m[0] for m in Mod]
            Lag = [ijSig[c] - Mod[c][0] for c in range(len(ij))]
            dataTemp = np.zeros(ModLen)
            dataTemp[[slice(*a) for a in Lag]] = coef
            dataTemp = imblur(dataTemp[..., np.newaxis], sig=gSig, siz=gSiz, kernel=kernel)
            temp = dataTemp * score.reshape([1] * (Y.ndim - 1) + [-1])
            rho[[slice(*a) for a in Mod]] -= temp.copy()
            v[[slice(*a) for a in Mod]] = np.sum(
                rho[[slice(*a) for a in Mod]]**2, axis=-1)

    res = np.reshape(Y, (np.prod(d[0:-1]), d[-1]), order='F') + med.flatten(order='F')[:, None]
    model = NMF(n_components=nb, init='random', random_state=0)
    b_in = model.fit_transform(np.maximum(res, 0))
    f_in = model.components_.squeeze()

    return A, C, center, b_in, f_in

#%%


def finetune(Y, cin, nIter=5):
    """compute a initialized version of A and C

    Parameters :
    -----------

    Y:  D1*d2*T*K patches

    c: array T*K
        the inital calcium traces

    nIter: int
        True indicates that time is listed in the last axis of Y (matlab format)
        and moves it in the front

    Returns :
    --------

    a: array (d1,D2) the computed A as l2(Y*C)/Y*C

    c: array(T) C as the sum of As on x*y axis


    See Also:
    ---------

            """
    #\bug
    #\warning
    debug_ = False
    if debug_:
        import os
        f = open('_LOG_1_' + str(os.getpid()), 'w+')
        f.write('Y:' + str(np.mean(Y)) + '\n')
        f.write('cin:' + str(np.mean(cin)) + '\n')
        f.close()

    # we compute the multiplication of patches per traces ( non negatively )
    for _ in range(nIter):
        a = np.maximum(np.dot(Y, cin), 0)
        a = old_div(a, np.sqrt(np.sum(a**2)))  # compute the l2/a
        # c as the variation of thoses patches
        cin = np.sum(Y * a[..., np.newaxis], tuple(np.arange(Y.ndim - 1)))

    return a, cin

#%%


def imblur(Y, sig=5, siz=11, nDimBlur=None, kernel=None, opencv=True):
    """
    Spatial filtering with a Gaussian or user defined kernel

    The parameters are specified in GreedyROI

    :param Y: np.ndarray
         d1 x d2 [x d3] x T movie, raw data.

    :param sig: [optional] list,tuple
        half size of neurons

    :param siz: [optional] list,tuple
        size of kernel (default 2*tau + 1).

    :param nDimBlur: [optional]
        if you want to specify the number of dimension

    :param kernel: [optional]
        if you want to specify a kernel

    :param opencv: [optional]
        if you want to process to the blur using open cv method

    :return: the blurred image
    """
    # TODO: document (jerem)
    if kernel is None:
        if nDimBlur is None:
            nDimBlur = Y.ndim - 1
        else:
            nDimBlur = np.min((Y.ndim, nDimBlur))

        if np.isscalar(sig):
            sig = sig * np.ones(nDimBlur)

        if np.isscalar(siz):
            siz = siz * np.ones(nDimBlur)

        X = Y.copy()
        if opencv and nDimBlur == 2:
            if X.ndim > 2:
                # if we are on a video we repeat for each frame
                for frame in range(X.shape[-1]):
                    X[:, :, frame] = cv2.GaussianBlur(X[:, :, frame], tuple(siz), sig[
                                                      0], sig[1], cv2.BORDER_CONSTANT, 0)
            else:
                X = cv2.GaussianBlur(X, tuple(siz), sig[0], sig[1], cv2.BORDER_CONSTANT, 0)
        else:
            for i in range(nDimBlur):
                h = np.exp(
                    old_div(-np.arange(-np.floor(old_div(siz[i], 2)), np.floor(old_div(siz[i], 2)) + 1)**2, (2 * sig[i]**2)))
                h /= np.sqrt(h.dot(h))
                shape = [1] * len(Y.shape)
                shape[i] = -1
                X = correlate(X, h.reshape(shape), mode='constant')

    else:
        X = correlate(Y, kernel[..., np.newaxis], mode='constant')
        # for t in range(np.shape(Y)[-1]):
        #    X[:,:,t] = correlate(Y[:,:,t],kernel,mode='constant', cval=0.0)

    return X

#%%


def hals(Y, A, C, b, f, bSiz=3, maxIter=5):
    """ Hierarchical alternating least square method for solving NMF problem

    Y = A*C + b*f

    input:
    ------
       Y:      d1 X d2 [X d3] X T, raw data.
        It will be reshaped to (d1*d2[*d3]) X T in this
       function

       A:      (d1*d2[*d3]) X K, initial value of spatial components

       C:      K X T, initial value of temporal components

       b:      (d1*d2[*d3]) X nb, initial value of background spatial component

       f:      nb X T, initial value of background temporal component

       bSiz:   int or tuple of int
        blur size. A box kernel (bSiz X bSiz [X bSiz]) (if int) or bSiz (if tuple) will
        be convolved with each neuron's initial spatial component, then all nonzero
       pixels will be picked as pixels to be updated, and the rest will be
       forced to be 0.

       maxIter: maximum iteration of iterating HALS.

    output:
    -------
        the updated A, C, b, f

    @Author: Johannes Friedrich, Andrea Giovannucci

    See Also:
        http://proceedings.mlr.press/v39/kimura14.pdf
    """

    # smooth the components
    dims, T = np.shape(Y)[:-1], np.shape(Y)[-1]
    K = A.shape[1]  # number of neurons
    nb = b.shape[1]  # number of background components
    if isinstance(bSiz, (int, float)):
        bSiz = [bSiz] * len(dims)
    ind_A = nd.filters.uniform_filter(np.reshape(A, dims + (K,), order='F'), size=bSiz + [0])
    ind_A = np.reshape(ind_A > 1e-10, (np.prod(dims), K), order='F')
    ind_A = spr.csc_matrix(ind_A)  # indicator of nonnero pixels

    def HALS4activity(Yr, A, C, iters=2):
        U = A.T.dot(Yr)
        V = A.T.dot(A)
        for _ in range(iters):
            for m in range(len(U)):  # neurons and background
                C[m] = np.clip(C[m] + (U[m] - V[m].dot(C)) / V[m, m], 0, np.inf)
        return C

    def HALS4shape(Yr, A, C, iters=2):
        U = C.dot(Yr.T)
        V = C.dot(C.T)
        for _ in range(iters):
            for m in range(K):  # neurons
                ind_pixels = np.squeeze(ind_A[:, m].toarray())
                A[ind_pixels, m] = np.clip(A[ind_pixels, m] +
                                           ((U[m, ind_pixels] - V[m].dot(A[ind_pixels].T)) /
                                            V[m, m]), 0, np.inf)
            for m in range(nb):  # background
                A[:, K + m] = np.clip(A[:, K + m] + ((U[K + m] - V[K + m].dot(A.T)) /
                                                     V[K + m, K + m]), 0, np.inf)
        return A

    Ab = np.c_[A, b]
    Cf = np.r_[C, f.reshape(nb, -1)]
    for _ in range(maxIter):
        Cf = HALS4activity(np.reshape(Y, (np.prod(dims), T), order='F'), Ab, Cf)
        Ab = HALS4shape(np.reshape(Y, (np.prod(dims), T), order='F'), Ab, Cf)

    return Ab[:, :-nb], Cf[:-nb], Ab[:, -nb:], Cf[-nb:].reshape(nb, -1)


def greedyROI_corr(data, max_number=None, g_size=15, g_sig=3,
                   center_psf=True, min_corr=0.8, min_pnr=10,
                   seed_method='auto', deconvolve_options=None,
                   min_pixel=3, bd=1, thresh_init=2,
                   ring_size_factor=1.5, ring_model=True, nb=1):
    """
    using greedy method to initialize neurons by selecting pixels with large
    local correlation and large peak-to-noise ratio

    Args:
        data: d1*d2*T np.ndarray, the data
        max_number: maximum number of neurons to be initialized
        g_size: size of the gaussian kernel
        g_sig:  gaussian width
        center_psf: Boolean; center the gaussian kernel or not
        min_corr: float, minimum local corr. coeff. for detecting a seed pixel
        min_pnr: float, minimum pnr for detecting a seed pixel
        seed_method: str, {'auto', 'manual'}. method for choosing seed pixels
        deconvolve_options: C-style struct variable for deconvolving traces
        min_pixel: integer; minimum number of nonzero pixels for each neuron
        bd: edge of the boundary
        thresh_init: threshold for removing small values when computing
            local correlation image.


    nb: int
        Number of background components

    Returns:

    """

    # if not deconvolve_options:
    #     deconvolve_options = {'bl': None,
    #                           'c1': None,
    #                           'g': None,
    #                           'sn': None,
    #                           'p': 1,
    #                           'approach': 'constrained foopsi',
    #                           'method': 'oasis',
    #                           'bas_nonneg': True,
    #                           'noise_range': [.25, .5],
    #                           'noise_method': 'logmexp',
    #                           'lags': 5,
    #                           'fudge_factor': 1.0,
    #                           'verbosity': None,
    #                           'solvers': None,
    #                           'optimize_g': 1,
    #                           'penalty': 1}
    # parameters
    d1, d2, total_frames = data.shape
    min_v_search = min_corr * min_pnr
    ind_bd = np.zeros(shape=(d1, d2)).astype(np.bool)  # indicate boundary pixels
    ind_bd[:bd, :] = True
    ind_bd[-bd:, :] = True
    ind_bd[:, :bd] = True
    ind_bd[:, -bd:] = True

    data_raw = data.copy().astype('float32')
    data_raw = np.transpose(data_raw, [2, 0, 1])

    # create a spatial filter for removing background
    psf = gen_filter_kernel(g_size, g_sig, center_psf)

    # spatially filter data
    data_filtered = np.zeros(shape=(total_frames, d1, d2))
    for idx in range(total_frames):
        # use spatial filtering to remove the background (options.center_psf=1)
        #  or smooth the data (options.center_psf=0)
        data_filtered[idx] = cv2.filter2D(data_raw[idx], -1, psf, borderType=1)

    # compute peak-to-noise ratio
    data_filtered -= data_filtered.mean(axis=0)
    data_max = np.max(data_filtered, axis=0)
    noise_pixel = get_noise_fft(data_filtered.transpose())[0].transpose()
    pnr = np.divide(data_max, noise_pixel)

    # remove small values and only keep pixels with large fluorescence signals
    tmp_data = np.copy(data_filtered)
    tmp_data[tmp_data < thresh_init * noise_pixel] = 0

    # compute correlation image
    cn = local_correlation(tmp_data)
    cn[np.isnan(cn)] = 0  # remove abnormal pixels

    # screen seed pixels as neuron centers
    v_search = cn * pnr
    v_search[(cn < min_corr) | (pnr < min_pnr)] = 0
    ind_search = (v_search <= 0)  # indicate whether the pixel has
    # been searched before. pixels with low correlations or low PNRs are
    # ignored directly. ind_search[i]=0 means the i-th pixel is still under
    # consideration of being a seed pixel

    # pixels near the boundaries are ignored because of artifacts
    ind_search[ind_bd] = 1

    # creating variables for storing the results
    if not max_number:
        # maximum number of neurons
        max_number = np.int32((ind_search.size - ind_search.sum()) / 5)
    Ain = np.zeros(shape=(max_number, d1, d2))  # neuron shapes
    Cin = np.zeros(shape=(max_number, total_frames))  # de-noised traces
    Sin = np.zeros(shape=(max_number, total_frames))  # spiking # activity
    Cin_raw = np.zeros(shape=(max_number, total_frames))  # raw traces
    center = np.zeros(shape=(2, max_number))  # neuron centers

    num_neurons = 0  # number of initialized neurons
    continue_searching = True

    while continue_searching:
        if seed_method.lower() == 'manual':
            pass
            # manually pick seed pixels
        else:
            # local maximum, for identifying seed pixels in following steps
            v_search[(cn < min_corr) | (pnr < min_pnr)] = 0
            tmp_kernel = np.ones(shape=(g_size // 3, g_size // 3))
            v_max = cv2.dilate(v_search, tmp_kernel)

            # automatically select seed pixels as the local maximums
            v_max[(v_search != v_max) | (v_search < min_v_search)] = 0
            v_max[ind_search] = 0
            [rsub_max, csub_max] = v_max.nonzero()  # subscript of seed pixels
            local_max = v_max[rsub_max, csub_max]
            n_seeds = len(local_max)  # number of candidates
            if n_seeds == 0:
                # no more candidates for seed pixels
                break
            else:
                # order seed pixels according to their corr * pnr values
                ind_local_max = local_max.argsort()[::-1]

        # try to initialization neurons given all seed pixels
        for ith_seed, idx in enumerate(ind_local_max):
            r = rsub_max[idx]
            c = csub_max[idx]
            ind_search[r, c] = True  # this pixel won't be searched
            if v_search[r, c] < min_v_search:
                # skip this pixel if it's not sufficient for being a seed pixel
                continue

            # roughly check whether this is a good seed pixel
            y0 = data_filtered[:, r, c]
            if np.max(y0) < thresh_init * noise_pixel[r, c]:
                continue

            # crop a small box for estimation of ai and ci
            r_min = np.max([0, r - g_size])
            r_max = np.min([d1, r + g_size + 1])
            c_min = np.max([0, c - g_size])
            c_max = np.min([d2, c + g_size + 1])
            nr = r_max - r_min
            nc = c_max - c_min
            patch_dims = (nr, nc)  # patch dimension
            data_raw_box = \
                data_raw[:, r_min:r_max, c_min:c_max].reshape(-1, nr * nc)
            data_filtered_box = \
                data_filtered[:, r_min:r_max, c_min:c_max].reshape(-1, nr * nc)
            # index of the seed pixel in the cropped box
            ind_ctr = np.ravel_multi_index((r - r_min, c - c_min),
                                           dims=(nr, nc))

            # neighbouring pixels to update after initializing one neuron
            r2_min = np.max([0, r - 2 * g_size])
            r2_max = np.min([d1, r + 2 * g_size + 1])
            c2_min = np.max([0, c - 2 * g_size])
            c2_max = np.min([d2, c + 2 * g_size + 1])

            [ai, ci_raw, ind_success] = extract_ac(data_filtered_box,
                                                   data_raw_box, ind_ctr, patch_dims)
            if (np.sum(ai > 0) < min_pixel) or (not ind_success):
                # bad initialization. discard and continue
                continue
            else:
                # cheers! good initialization.
                center[:, num_neurons] = [c, r]
                Ain[num_neurons, r_min:r_max, c_min:c_max] = ai
                Cin_raw[num_neurons] = ci_raw.squeeze()
                if deconvolve_options:
                    # deconvolution
                    ci, si, tmp_options, baseline, c1 = \
                        deconvolve_ca(ci_raw, deconvolve_options)
                    Cin[num_neurons] = ci
                    Sin[num_neurons] = si
                else:
                    # no deconvolution
                    ci = ci_raw - np.median(ci_raw)
                    ci[ci < 0] = 0
                    Cin[num_neurons] = ci.squeeze()

                # remove the spatial-temporal activity of the initialized
                # and update correlation image & PNR image
                # update the raw data
                data_raw[:, r_min:r_max, c_min:c_max] -= \
                    ai[np.newaxis, ...] * ci[..., np.newaxis, np.newaxis]
                # spatially filtered the neuron shape
                ai_filtered = cv2.filter2D(Ain[num_neurons, r2_min:r2_max,
                                               c2_min:c2_max], -1, psf, borderType=1)
                # update the filtered data
                data_filtered[:, r2_min:r2_max, c2_min:c2_max] -= \
                    ai_filtered[np.newaxis, ...] * ci[..., np.newaxis, np.newaxis]
                data_filtered_box = data_filtered[:, r2_min:r2_max, c2_min:c2_max].copy()

                # update PNR image
                data_filtered_box -= data_filtered_box.mean(axis=0)
                max_box = np.max(data_filtered_box, axis=0)
                noise_box = noise_pixel[r2_min:r2_max, c2_min:c2_max]
                pnr_box = np.divide(max_box, noise_box)
                pnr[r2_min:r2_max, c2_min:c2_max] = pnr_box

                # update correlation image
                data_filtered_box[data_filtered_box < thresh_init * noise_box] = 0
                cn_box = local_correlation(data_filtered_box)
                cn_box[np.isnan(cn_box) | (cn_box < 0)] = 0
                cn[r_min:r_max, c_min:c_max] = cn_box[(r_min - r2_min):(
                    r_max - r2_min), (c_min - c2_min):(c_max - c2_min)]
                cn_box = cn[r2_min:r2_max, c2_min:c2_max]

                # update v_search
                v_search[r2_min:r2_max, c2_min:c2_max] = cn_box * pnr_box

                # increase the number of detected neurons
                num_neurons += 1  #
                if num_neurons == max_number:
                    continue_searching = False
                    break
                else:
                    if num_neurons % 10 == 1:
                        print(num_neurons - 1, 'neurons have been initialized')

    print('In total, ', num_neurons, 'neurons were initialized.')
    # A = np.reshape(Ain[:num_neurons], (-1, d1 * d2)).transpose()
    A = np.reshape(Ain[:num_neurons], (-1, d1 * d2), order='F').transpose()
    C = Cin[:num_neurons]
    # C_raw = Cin_raw[:num_neurons]
    # S = Sin[:num_neurons]
    center = center[:, :num_neurons]

    B = data.reshape((-1, total_frames), order='F') - A.dot(C)
    if ring_model:
        W, b0 = compute_W(data.reshape((-1, total_frames), order='F'),
                          A, C, (d1, d2), int(np.round(ring_size_factor * g_size)))
        B = b0[:, None] + W.dot(B - b0[:, None])

    model = NMF(n_components=nb)  # , init='random', random_state=0)
    b_in = model.fit_transform(np.maximum(B, 0))
    f_in = model.components_.squeeze()

    return A, C, center[::-1].T, b_in, f_in
    # # return results
    # return A, C, C_raw, S, center, None, None,


def extract_ac(data_filtered, data_raw, ind_ctr, patch_dims):
    # parameters
    min_corr_neuron = 0.7
    max_corr_bg = 0.3
    data_filtered = data_filtered.copy()

    # compute the temporal correlation between each pixel and the seed pixel
    data_filtered -= data_filtered.mean(axis=0)  # data centering
    tmp_std = np.sqrt(np.sum(data_filtered ** 2, axis=0))  # data
    # normalization
    tmp_std[tmp_std == 0] = 1
    data_filtered /= tmp_std
    y0 = data_filtered[:, ind_ctr]  # fluorescence trace at the center
    tmp_corr = np.dot(y0.reshape(1, -1), data_filtered)  # corr. coeff. with y0
    ind_neuron = (tmp_corr > min_corr_neuron).squeeze()  # pixels in the central area of neuron
    ind_bg = (tmp_corr < max_corr_bg).squeeze()  # pixels outside of neuron's ROI

    # extract temporal activity
    ci = np.mean(data_filtered[:, ind_neuron], axis=1).reshape(-1, 1)
    # initialize temporal activity of the neural
    ci -= np.median(ci)

    if np.linalg.norm(ci) == 0:  # avoid empty results
        return None, None, False

    # roughly estimate the background fluctuation
    y_bg = np.median(data_raw[:, ind_bg], axis=1).reshape(-1, 1)

    # extract spatial components
    # pdb.set_trace()
    X = np.hstack([ci - ci.mean(), y_bg - y_bg.mean(), np.ones(ci.shape)])
    XX = np.dot(X.transpose(), X)
    Xy = np.dot(X.transpose(), data_raw)
    ai = scipy.linalg.lstsq(XX, Xy)[0][0]
    ai = ai.reshape(patch_dims)
    ai[ai < 0] = 0

    # post-process neuron shape
    l, _ = bwlabel(ai > np.median(ai))
    ai[l != l.ravel()[ind_ctr]] = 0

    # return results
    return ai, ci.reshape(len(ci)), True


def gen_filter_kernel(width=16, sigma=4, center=True):
    """
    create a gaussian kernel for spatially filtering the raw data

    Args:
        width: (float)
            width of the kernel
        sigma: (float)
            gaussian width of the kernel
        center:
            if True, subtract the mean of gaussian kernel

    Returns:
        psf: (2D numpy array, width x width)
            the desired kernel

    """
    rmax = (width - 1) / 2.0
    y, x = np.ogrid[-rmax:(rmax + 1), -rmax:(rmax + 1)]
    psf = np.exp(-(x * x + y * y) / (2 * sigma * sigma))
    psf = psf / psf.sum()
    if center:
        idx = (psf >= psf[0].max())
        psf[idx] -= psf[idx].mean()
        psf[~idx] = 0

    return psf


def local_correlation(video_data, sz=None, d1=None, d2=None,
                      normalized=False, chunk_size=3000):
    """
    compute location correlations of the video data
    Args:
        video_data: T*d1*d2 3d array  or T*(d1*d2) matrix
        sz: method for computing location correlation {4, 8, [dmin, dmax]}
            4: use the 4 nearest neighbors
            8: use the 8 nearest neighbors
            [dmin, dmax]: use neighboring pixels with distances in [dmin, dmax]
        d1: row number
        d2: column number
        normalized: boolean
            if True: avoid the step of normalizing data
        chunk_size: integer
            divide long data into small chunks for faster running time

    Returns:
        d1*d2 matrix, the desired correlation image

    """
    total_frames = video_data.shape[0]
    if total_frames > chunk_size:
        # too many frames, compute correlation images in chunk mode
        n_chunk = np.floor(total_frames / chunk_size)

        cn = np.zeros(shape=(n_chunk, d1, d2))
        for idx in np.arange(n_chunk):
            cn[idx, ] = local_correlation(
                video_data[chunk_size * idx + np.arange(
                    chunk_size), ], sz, d1, d2, normalized)
        return np.max(cn, axis=0)

    # reshape data
    data = video_data.copy().astype('float32')

    if data.ndim == 2:
        data = data.reshape(total_frames, d1, d2)
    else:
        _, d1, d2 = data.shape

    # normalize data
    if not normalized:
        data -= np.mean(data, axis=0)
        data_std = np.std(data, axis=0)
        data_std[data_std == 0] = np.inf
        data /= data_std

    # construct a matrix indicating the locations of neighbors
    if (not sz) or (sz == 8):
        mask = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]])
    elif sz == 4:
        mask = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]])
    elif len(sz) == 2:
        sz = np.array(sz)
        temp = np.arange(-sz.max(), sz.max() + 1).reshape(2 * sz.max() + 1, 0)
        tmp_dist = np.sqrt(temp ** 2 + temp.transpose() ** 2)
        mask = (tmp_dist >= sz.min()) & (tmp_dist < sz.max())
    else:
        mask = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]])

    # compute local correlations
    data_filter = data.copy().astype('float32')
    for idx, img in enumerate(data_filter):
        data_filter[idx] = cv2.filter2D(img, -1, mask, borderType=0)

    return np.divide(np.mean(data_filter * data, axis=0), cv2.filter2D(
        np.ones(shape=(d1, d2)), -1, mask, borderType=1))


def compute_W(Y, A, C, dims, radius):
    """compute background according to ring model

    solves the problem
        min_{W,b0} ||X-W*X|| with X = Y - A*C - b0*1'
    subject to
        W(i,j) = 0 for each pixel j that is not in ring around pixel i
    Problem parallelizes over pixels i
    Fluctuating background activity is W*X, constant baselines b0.

    Parameters:
    ----------
    Y: np.ndarray (2D or 3D)
        movie, raw data in 2D or 3D (pixels x time).

    A: np.ndarray or sparse matrix
        spatial footprint of each neuron.

    C: np.ndarray
        calcium activity of each neuron.

    dims: tuple
        x, y[, z] movie dimensions

    radius: int
        radius of ring

    Returns:
    --------
    W: scipy.sparse.csr_matrix (pixels x pixels)
        estimate of weight matrix for fluctuating background

    b0: np.ndarray (pixels,)
        estimate of constant background baselines
    """
    ring = (disk(radius + 1) -
            np.concatenate([np.zeros((1, 2 * radius + 3), dtype=bool),
                            np.concatenate([np.zeros((2 * radius + 1, 1), dtype=bool),
                                            disk(radius),
                                            np.zeros((2 * radius + 1, 1), dtype=bool)], 1),
                            np.zeros((1, 2 * radius + 3), dtype=bool)]))
    ringidx = [i - radius - 1 for i in np.nonzero(ring)]

    def get_indices_of_pixels_on_ring(pixel):
        pixel = np.unravel_index(pixel, dims, order='F')
        x = pixel[0] + ringidx[0]
        y = pixel[1] + ringidx[1]
        inside = (x >= 0) * (x < dims[0]) * (y >= 0) * (y < dims[1])
        return np.ravel_multi_index((x[inside], y[inside]), dims, order='F')

    b0 = np.array(Y.mean(1)) - A.dot(C.mean(1))

    indices = []
    data = []
    indptr = [0]
    for p in xrange(np.prod(dims)):
        index = get_indices_of_pixels_on_ring(p)
        indices += list(index)
        B = Y[index] - A[index].dot(C) - b0[index, None]
        data += list(np.linalg.inv(np.array(B.dot(B.T)) + 1e-9 * np.eye(len(index))).
                     dot(B.dot(Y[p] - A[p].dot(C).ravel() - b0[p])))
        # np.linalg.lstsq seems less robust but scipy version would be (robust but for the problem size slower) alternative
        # data += list(scipy.linalg.lstsq(B.T, Y[p] - A[p].dot(C) - b0[p], check_finite=False)[0])
        indptr.append(len(indices))
    return spr.csr_matrix((data, indices, indptr), dtype='float32'), b0

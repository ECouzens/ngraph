import os
import numpy as np
import ctypes as ct
import numpy.ctypeslib as npct


def ctc_cpu(acts, lbls, utt_lens, lbl_lens, grads, costs, n_threads=8):
    basepath = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    temp_loc = os.path.join("examples", "deepspeech", "src", "libwarpctc.so")
    libpath = os.path.join(basepath, temp_loc)
    assert os.path.isfile(libpath), ("Expected libwarpctc.so at {} but not found. "
                                     "Try running make").format(libpath)
    ctclib = npct.load_library(libpath, "")
    ctclib.compute_ctc_loss_cpu.restype = int
    ctclib.compute_ctc_loss_cpu.argtypes = [
        npct.ndpointer(dtype=np.float32, ndim=3),
        npct.ndpointer(dtype=np.float32, ndim=3),
        npct.ndpointer(dtype=np.int32, ndim=1),
        npct.ndpointer(dtype=np.int32, ndim=1),
        npct.ndpointer(dtype=np.int32, ndim=1),
        ct.c_int,
        ct.c_int,
        npct.ndpointer(dtype=np.float32, ndim=1),
        ct.c_int]
    max_t, bsz, nout = acts.shape
    utt_lens = utt_lens * max_t / 100
    utt_lens = utt_lens.astype(np.int32)
    costs.fill(0.)
    grads.fill(0.)
    status = ctclib.compute_ctc_loss_cpu(acts,
                                         grads,
                                         lbls.astype(np.int32),
                                         lbl_lens.astype(np.int32),
                                         utt_lens.astype(np.int32),
                                         nout,
                                         bsz,
                                         costs,
                                         n_threads)
    assert status is 0, "warp-ctc run failed"

from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def com_apartment():
    """Make sure COM is initialised on the calling thread (Core Audio, SAPI).

    CoUninitialize is deliberately NOT called on exit. COM pointers held by the caller are
    released when its function returns, which is after this block ends; releasing them in an
    uninitialised apartment crashes the process (access violation / segfault). Initialising a
    thread more than once is harmless, so the thread simply stays initialised.
    """
    import comtypes

    comtypes.CoInitialize()
    yield

"""Trace hook for the simulated launch: with FRONTIER_TRACE set, every Python process of the mission appends to one
JSONL file the frontier/ functions it calls, the files it opens or renames, and the subprocesses it starts."""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _d in sys.path:  # the image's own sitecustomize (zlib fix) still runs
    _f = os.path.join(_d, "sitecustomize.py")
    if _d and os.path.abspath(_d) != _here and os.path.isfile(_f):
        exec(compile(open(_f).read(), _f, "exec"), {"__file__": _f, "__name__": "_system_sitecustomize"})
        break

TRACE = os.environ.get("FRONTIER_TRACE")
PROC = os.environ.get("TRACE_PROC", "python")


def emit(**e):
    if not TRACE:
        return
    import json
    import threading
    import time

    e.update(t=time.time(), pid=os.getpid(), proc=PROC, th=threading.current_thread().name)
    fd = os.open(TRACE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(e, default=str) + "\n").encode())
    finally:
        os.close(fd)


if TRACE:
    import builtins
    import io
    import subprocess
    import threading

    ROOT = os.environ["TRACE_ROOT"].rstrip("/") + "/"
    PKG = ROOT + "frontier/"
    WATCH = [p.rstrip("/") + "/" for p in os.environ.get("TRACE_WATCH", "").split(os.pathsep) if p] + [ROOT]
    _tls = threading.local()

    def _rel(path):
        p = os.path.abspath(os.fspath(path))
        for w in WATCH:
            if p.startswith(w):
                return p
        return None

    def _busy():  # the hook's own I/O is never traced
        return getattr(_tls, "busy", False)

    def _wrap(e, fn):
        _tls.busy = True
        try:
            emit(**e)
        finally:
            _tls.busy = False
        return fn

    def _prof(frame, event, arg):
        if _busy() or event not in ("call", "return"):
            return
        co = frame.f_code
        if not co.co_filename.startswith(PKG) or co.co_name.startswith("<"):
            return
        d = getattr(_tls, "d", 0)
        if event == "call":
            _tls.d = d + 1
            mod = co.co_filename[len(ROOT):-3].replace("/", ".")
            _wrap({"k": "call", "fn": f"{mod}.{co.co_qualname}", "file": co.co_filename[len(ROOT):],
                   "line": co.co_firstlineno, "d": d}, None)
        else:
            _tls.d = max(0, d - 1)

    _open = io.open

    def _traced_open(file, mode="r", *a, **kw):
        if not _busy() and isinstance(file, (str, bytes, os.PathLike)):
            r = _rel(file)
            if r:
                _wrap({"k": "write" if any(c in mode for c in "wax+") else "read", "path": r}, None)
        return _open(file, mode, *a, **kw)

    io.open = builtins.open = _traced_open
    _replace = os.replace

    def _traced_replace(src, dst, *a, **kw):
        r = _rel(dst)
        if r and not _busy():
            _wrap({"k": "write", "path": r, "via": "replace"}, None)
        return _replace(src, dst, *a, **kw)

    os.replace = _traced_replace
    _popen_init = subprocess.Popen.__init__

    def _traced_popen(self, args, *a, **kw):
        if not _busy():
            cmd = args if isinstance(args, str) else " ".join(map(str, args))
            _wrap({"k": "spawn", "cmd": cmd[:400], "cwd": str(kw.get("cwd") or "")}, None)
        return _popen_init(self, args, *a, **kw)

    subprocess.Popen.__init__ = _traced_popen
    sys.setprofile(_prof)
    threading.setprofile(_prof)
    emit(k="process", argv=sys.argv, cwd=os.getcwd())

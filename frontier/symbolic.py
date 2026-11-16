"""Symbolic algebra and calculus for the agents, with SymPy (pip install sympy: the only use of it in the framework).

The code runs in a Python process of its own, at the head of its own process group: at the time limit the whole group
is killed, so a computation that never ends takes neither the MCP server nor the agent with it and leaves no worker
behind. Its output goes to files of a limited size, never to the memory of the server.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import tempfile

OUTPUT_MAX = 10_000_000  # bytes the code may print, or write to one file
ALARM_GRACE_S = 5  # the run ends itself this long after its limit, when the server that should have ended it is gone

# The caller's code runs in `__main__`, with what `isympy` gives a session, and the runner keeps its own names inside a
# function: a name the code rebinds (latex, print, ast) changes nothing here, and a function the code defines can be
# sent to a worker process. The value of a last expression is printed, as a console does.
RUNNER = r'''
def _run():
    import ast, os, resource, signal, sys

    signal.signal(signal.SIGALRM, lambda *_: os.killpg(0, signal.SIGKILL))
    signal.setitimer(signal.ITIMER_REAL, float(sys.argv[1]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[2]), int(sys.argv[2])))
    try:
        import sympy
    except ImportError as e:
        sys.exit(f"sympy cannot be imported by {sys.executable} ({e}): pip install sympy")

    def symbolic(value):
        if isinstance(value, dict):
            value = [*value, *value.values()]
        if isinstance(value, (list, tuple, set, frozenset)):
            return any(symbolic(v) for v in value)
        return type(value).__module__.startswith("sympy")

    def prints(node):
        return isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") in ("print", "pprint")

    show = print
    names = globals()
    del names["_run"]
    exec("from sympy import *\nx, y, z, t = symbols('x y z t')\nk, m, n = symbols('k m n', integer=True)\n"
         "f, g, h = symbols('f g h', cls=Function)", names)
    tree = ast.parse(sys.stdin.read(), "<code>")
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) and not prints(tree.body[-1]) else None
    exec(compile(tree, "<code>", "exec"), names)
    if last is not None:
        value = eval(compile(ast.Expression(last.value), "<code>", "eval"), names)
        show(value)
        if symbolic(value):
            show("latex:", sympy.latex(value))


_run()
'''


def run(code: str, cwd: str | os.PathLike, limit: float) -> tuple[str, bool]:
    """Run `code` for at most `limit` seconds. Returns the answer and whether the run failed."""
    with tempfile.TemporaryDirectory() as tmp:
        out, err = pathlib.Path(tmp, "out"), pathlib.Path(tmp, "err")
        with out.open("w") as o, err.open("w") as e:
            # -P: a workspace file named like a module (sympy.py, ast.py) is not imported in its place
            p = subprocess.Popen([sys.executable, "-P", "-c", RUNNER, str(limit + ALARM_GRACE_S), str(OUTPUT_MAX)],
                                 stdin=subprocess.PIPE, stdout=o, stderr=e, text=True, cwd=cwd, start_new_session=True)
            try:
                p.communicate(code, timeout=limit)
                late = False
            except subprocess.TimeoutExpired:
                late = True
            finally:
                try:
                    os.killpg(p.pid, signal.SIGKILL)  # the run, and the processes it started
                except ProcessLookupError:
                    pass
                p.wait()
        printed, errors = out.read_text(errors="replace").rstrip(), err.read_text(errors="replace").strip()
        if late:
            problem = f"no result after {limit:g} s (timeout_s sets the limit)"
        elif out.stat().st_size >= OUTPUT_MAX:
            problem = f"the code printed more than {OUTPUT_MAX // 10**6} MB"
        elif p.returncode:  # from the caller's own code on, without the runner's frames
            problem = errors[max(errors.find('File "<code>"'), 0):] or f"exit {p.returncode}"
        else:
            answer = printed or "no output: print a value, or end the code with an expression"
            return answer + (f"\n--- stderr\n{errors}" if errors else ""), False
    return f"error: {problem}" + (f"\n--- printed before\n{printed}" if printed else ""), True

"""Symbolic algebra and calculus for the agents, with SymPy (pip install sympy: the only use of it in the framework).

The code runs in a Python process of its own, so a computation that never ends is killed at its time limit and takes
neither the MCP server nor the agent with it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

# The names `isympy` gives a session, then the code; the value of a last expression is printed, as a console does.
RUNNER = r'''
import ast, sys
from sympy import *
x, y, z, t = symbols("x y z t")
k, m, n = symbols("k m n", integer=True)
f, g, h = symbols("f g h", cls=Function)
_tree = ast.parse(sys.stdin.read(), "<code>")
_last = _tree.body.pop() if _tree.body and isinstance(_tree.body[-1], ast.Expr) else None
exec(compile(_tree, "<code>", "exec"))
if _last is not None:
    _value = eval(compile(ast.Expression(_last.value), "<code>", "eval"))
    if _value is not None:
        print(_value)
        if isinstance(_value, (Basic, MatrixBase, list, tuple, dict)):
            print("latex:", latex(_value))
'''


def sympy_run(ctx, code: str, timeout_s: int = 0) -> str:
    if importlib.util.find_spec("sympy") is None:
        return f"error: sympy is not installed for {sys.executable}: pip install sympy"
    limit = int(timeout_s or ctx.limits.get("sympy_timeout_s", 120))
    try:  # -P: a workspace file named like a module (sympy.py, ast.py) is not imported in its place
        r = subprocess.run([sys.executable, "-P", "-c", RUNNER], input=code, cwd=ctx.workspace, capture_output=True,
                           text=True, timeout=limit, check=False)
    except subprocess.TimeoutExpired:
        return f"error: no result after {limit} s (timeout_s sets the limit)"
    if r.returncode:
        err = r.stderr.strip() or f"exit {r.returncode}"
        err = err[max(err.find('File "<code>"'), 0):]  # from the caller's own code on, without the runner's frames
        return f"error: {err}" + (f"\n--- printed before the error\n{r.stdout.rstrip()}" if r.stdout else "")
    return r.stdout.rstrip() or "no output: print a value, or end the code with an expression"

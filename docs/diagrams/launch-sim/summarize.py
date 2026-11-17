"""trace.jsonl of a simulated launch (run.sh) -> trace-summary.md beside it: one timeline in first-seen order, by
level of detail.

    python3 summarize.py OUT/trace.jsonl [max_depth]
"""

import json
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
max_depth = int(sys.argv[2]) if len(sys.argv) > 2 else 2
ev = sorted((json.loads(x) for x in src.read_text().splitlines() if x.strip()), key=lambda e: e["t"])
t0 = ev[0]["t"]
OUT = str(src.parent.resolve()) + "/"
REPO = str(pathlib.Path(__file__).resolve().parents[3]) + "/"


def short(p):
    return p.replace(OUT, "OUT/").replace(REPO, "")


def ts(e):
    return f"{e['t'] - t0:6.2f}s"


out = ["# Simulated launch, traced", "", f"{len(ev)} events from {len({e['pid'] for e in ev})} processes.", ""]
out += ["## Level 2: steps, subprocesses and files, in order", ""]
seen = set()
for e in ev:
    who = f"{e['proc']}/{e['th']}"
    if e["k"] == "step":
        extra = {k: v for k, v in e.items() if k not in ("k", "step", "t", "pid", "proc", "th")}
        out.append(f"- {ts(e)} **{who}** STEP {e['step']} {json.dumps(extra)[:260] if extra else ''}")
    elif e["k"] == "spawn":
        out.append(f"- {ts(e)} **{who}** SPAWN `{short(e['cmd'])[:220]}`")
    elif e["k"] in ("read", "write"):
        key = (e["proc"], e["k"], e["path"])
        if key in seen or "/__pycache__/" in e["path"] or e["path"].endswith(".lock"):
            continue
        seen.add(key)
        out.append(f"- {ts(e)} {who} {e['k'].upper()} {short(e['path'])}")
    elif e["k"] == "process":
        out.append(f"- {ts(e)} **{who}** PROCESS pid {e['pid']} {' '.join(e['argv'])[:160]}")
out += ["", f"## Level 3: frontier/ calls up to depth {max_depth} (repeats folded)", ""]
NOISE = ("tools._obj", "tools.register", "budget.read_json", "budget.write_json", "budget.now", "budget._head",
         "budget.locked", "budget.parse_time", "config.load_config", "budget.read_jsonl", "budget._chain", "llm.resolve")


def noise(fn):  # class bodies and the small helpers every step calls
    name = fn.rsplit(".", 1)[-1]
    return fn.split(".", 1)[1] in NOISE or ("." not in fn.split(".", 2)[-1] and name[:1].isupper())


last = None
count = 0
for e in ev:
    if e["k"] != "call" or e["d"] > max_depth or noise(e["fn"]):
        continue
    key = (e["proc"], e["th"], e["fn"], e["d"])
    if key == last:
        count += 1
        continue
    if last and count:
        out[-1] += f"  (x{count + 1})"
    last, count = key, 0
    out.append(f"- {ts(e)} {e['proc']}/{e['th']} {'  ' * e['d']}{e['fn']}  `{e['file']}:{e['line']}`")
(src.parent / "trace-summary.md").write_text("\n".join(out) + "\n")
print(f"{src.parent / 'trace-summary.md'}: {len(out)} lines")

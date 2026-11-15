"""Jury of a race: grade every team and rank them.

    python3 -m frontier jury --root DIR --subject FILE --context FILE --out DIR [--judge openai --judge anthropic ...]

Cost control and delivery time are measured from the framework's ledgers (`facts`, `measured_scores`); scientific
quality and compliance are graded by a panel of LLM judges on several providers, a judge never grading a team of its
own provider. Grading is anonymous: each team gets a random pseudonym (Team A, B, ...), and every name that tells its
author (providers, brands, model families and ids, harnesses, from config/providers.toml `names`, `tiers` and the
harness list) is replaced by [redacted] in what a judge reads; a file in which a name is still found is not sent.
Each judge reads the subject, the general race context, the incidents of that team (inside or outside its control)
and the team's file (its report, records, delivered code, repository listing and small result files, as the team
left them), and answers in JSON (`prompts/jury.md`). The pseudonyms are lifted only in the results. A criterion's grade is the median
of the judges; the final score is the weighted mean of the four criteria (the mission's `[grading]` weights, else
equal); delivered teams rank before the others. The jury's own calls are metered against `--cap-usd` in its ledger.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import random
import re
import statistics
import subprocess
import sys

from . import llm
from .budget import CLOUD_CURRENCY, Budget, parse_time
from .config import ROOT, load_config
from .harnesses import HARNESSES
from .mission import report_files

CRITERIA = ("scientific_quality", "cost_control", "delivery_time", "compliance")
LABELS = {"scientific_quality": "Scientific quality", "cost_control": "Cost control", "delivery_time": "Delivery time",
          "compliance": "Compliance"}
RECORDS = ("DELIVERY.md", "COMPLIANCE.md", "CHECKLIST.md", "DECISIONS.md")
MAX_DOC = 30_000  # characters of one record in a judge's file
MAX_RESULTS = 40_000  # characters of small result files in a judge's file
MAX_CODE = 40_000  # characters of delivered code in a judge's file
HIDDEN = (".agents/", ".claude/", ".codex/", ".gemini/", ".dsh/", ".kimi", ".qwen/", ".vibe/", ".grok/", ".zcode",
          "subject/", "AGENTS.md", "MISSION.md", "TOOLS.md", "CLAUDE.md", "GEMINI.md", "QWEN.md")


# ---------------------------------------------------------------- anonymity


def identity_names(cfg: dict) -> list[str]:
    """Every name that tells a team's author: provider keys and `names`, model ids, harness keys and names."""
    out = set()
    for key, p in cfg["providers"].items():
        out.add(key)
        out.update(p.get("names", []))
        out.update(v for v in p.get("tiers", {}).values())
        for mode in (p.get("modes") or {}).values():
            if isinstance(mode, dict) and isinstance(mode.get("model"), dict):
                out.update(mode["model"].values())
    for h in HARNESSES.values():  # a provider's own harness ("Grok Build"); not generic ones named by common words
        if h.provider in cfg["providers"]:
            out.add(h.name)
            out.add(re.sub(r"\s*\(.*\)$", "", h.title))
    return sorted((n for n in out if n), key=len, reverse=True)


def redact(text: str, names: list[str]) -> tuple[str, int]:
    """Replace each name, with what is glued to it ("claude-sonnet-5-5", "race-openai-delivery"), by [redacted]. Names
    of 4 characters or more are caught inside words too; shorter ones only as words of their own."""
    n = 0
    for name in names:
        lead = "" if len(name) >= 4 else r"(?<![A-Za-z0-9])"
        pat = re.compile(lead + re.escape(name) + r"[\w.-]*", re.I)
        text, k = pat.subn("[redacted]", text)
        n += k
    # "Claude Sonnet 5.5" -> "[redacted] [redacted] 5.5": the names and the version that follows them become one mark
    text = re.sub(r"\[redacted\](?:[\s/-]*\[redacted\])*(?:\s+v?\d+(?:\.\d+)*\b)?", "[redacted]", text)
    return text, n


def leaks(text: str, names: list[str]) -> list[str]:
    return [n for n in names if (n.lower() in text.lower() if len(n) >= 4
                                 else re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", text, re.I))]


def pseudonyms(teams: list[str]) -> dict[str, str]:
    labels = [f"Team {chr(65 + i)}" for i in range(len(teams))]
    random.SystemRandom().shuffle(labels)
    return dict(zip(teams, labels, strict=True))


def incidents_of(context: str) -> tuple[str, dict[str, str]]:
    """The context's general part, and its `### <provider or mission>` sections under `## Incidents`, one per team."""
    head, _, rest = context.partition("\n## Incidents")
    parts = {}
    for block in rest.split("\n### ")[1:]:
        key, _, body = block.partition("\n")
        parts[key.strip()] = body.strip()
    return head.strip(), parts


# ---------------------------------------------------------------- measured by the framework


def facts(state: pathlib.Path) -> dict:
    """What the ledgers say about one mission: delivery, time, spend against caps, models called, integrity."""
    s = json.loads((state / "mission.json").read_text())
    b = Budget(state)
    caps = b.caps
    start, deadline = parse_time(caps.get("start")), parse_time(caps.get("deadline"))
    dv = json.loads((state / "delivery.json").read_text()) if (state / "delivery.json").exists() else None
    models: dict[str, int] = {}
    for e in b.llm_entries():
        if e["frontier.source"] != "launcher":
            models[e["gen_ai.request.model"]] = models.get(e["gen_ai.request.model"], 0) + 1
    gpu_spent = sum(b.cloud_spent(p) for p in CLOUD_CURRENCY)
    gpu_cap = sum(float(caps[k]) for k, _, _ in CLOUD_CURRENCY.values() if caps.get(k))
    events = [json.loads(ln) for ln in (state / "events.jsonl").read_text().splitlines()] if (state / "events.jsonl").exists() else []
    return {"team": s.get("name", state.name), "provider": s["provider"], "harness": s["harness"], "tier": s["tier"],
            "reasoning": s["reasoning"], "delivered": dv is not None,
            "delivered_at": dv and dv["delivered_at"], "elapsed_hours": dv and float(dv["elapsed_hours"]),
            "duration_hours": round((deadline - start).total_seconds() / 3600, 3) if start and deadline else None,
            "llm_usd": round(b.llm_spent(), 4), "llm_cap_usd": caps.get("llm_usd"),
            "gpu_usd": round(gpu_spent, 4), "gpu_cap_usd": gpu_cap or None, "models_called": models,
            "sessions": sum(e.get("event") == "session_start" for e in events),
            "ledgers_intact": all(v is True for v in b.verify().values())}


def measured_scores(f: dict) -> dict:
    """Cost control: 10 x the share of the budgets left (LLM and cloud weighed alike); delivery time: 10 x the share
    of the allowed time left at delivery. Both 0 for a team that did not deliver: a budget not spent on a delivery is
    not a saving."""
    if not f["delivered"]:
        return {"cost_control": 0.0, "delivery_time": 0.0}
    shares = [f["llm_usd"] / f["llm_cap_usd"]] if f["llm_cap_usd"] else []
    if f["gpu_cap_usd"]:
        shares.append(f["gpu_usd"] / f["gpu_cap_usd"])
    used = min(statistics.mean(shares), 1.0) if shares else 0.0
    t = (1 - min(f["elapsed_hours"] / f["duration_hours"], 1.0)) if f["duration_hours"] else 0.0
    return {"cost_control": round(10 * (1 - used), 2), "delivery_time": round(10 * t, 2)}


# ---------------------------------------------------------------- the team's file


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f"\n[... {len(text) - n} more characters not shown]"


def team_file(ws: pathlib.Path, f: dict, label: str, incidents: str) -> str:
    """What a judge reads about one team, as the team left it, before redaction."""
    shown = {"team": label, "delivered": f["delivered"], "delivered_after_hours": f["elapsed_hours"],
             "allowed_hours": f["duration_hours"], "llm_usd": f["llm_usd"], "llm_cap_usd": f["llm_cap_usd"],
             "gpu_usd": f["gpu_usd"], "gpu_cap_usd": f["gpu_cap_usd"], "sessions": f["sessions"],
             "ledgers_intact": f["ledgers_intact"]}
    parts = [f"## {label}: facts measured by the framework", "", json.dumps(shown, indent=1), "",
             f"## Incidents of {label} during the race", "", incidents or "None recorded.", ""]
    reps = report_files(ws)
    for p in reps:
        parts += [f"## Report ({p.name})", "", p.read_text(errors="replace"), ""]
    if not reps:
        parts += ["## Report", "", "(no report in the repository)", ""]
    for name in RECORDS:
        if (ws / name).exists():
            parts += [f"## {name}", "", _clip((ws / name).read_text(errors="replace"), MAX_DOC), ""]
    files = subprocess.run(["git", "-C", str(ws), "ls-files"], capture_output=True, text=True).stdout.split("\n")
    files = [x for x in files if x and not x.startswith(HIDDEN) and (ws / x).is_file()]
    parts += ["## Repository listing (path, size)", "", "\n".join(f"{x}  {(ws / x).stat().st_size} B" for x in files[:400]), ""]
    code, used = [], 0
    for x in files:
        p = ws / x
        if p.suffix in (".py", ".sh", ".jl", ".m", ".cpp", ".c") and used < MAX_CODE:
            body = _clip(p.read_text(errors="replace"), MAX_CODE - used)
            code.append(f"### {x}\n\n```\n{body}\n```\n")
            used += len(body)
    parts += ["## Delivered code", "", "\n".join(code) or "(none)", ""]
    small, used = [], 0
    for x in files:
        p = ws / x
        if (p.suffix in (".json", ".csv", ".md", ".txt") and p.stat().st_size <= 8_000 and x not in RECORDS
                and p not in reps and used < MAX_RESULTS):
            body = p.read_text(errors="replace")
            small.append(f"### {x}\n\n{body}\n")
            used += len(body)
    parts += ["## Small result and record files", "", "\n".join(small) or "(none)", ""]
    return "\n".join(parts)


# ---------------------------------------------------------------- judges


def _json_of(text: str) -> dict:
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


def judge(spec: llm.ModelSpec, brief: str, subject: str, context: str, dossier: str) -> tuple[dict, dict]:
    msgs = [{"role": "system", "content": brief},
            {"role": "user", "content": f"# Race context\n\n{context}\n\n# The subject\n\n{subject}\n\n# The team's file\n\n{dossier}"}]
    last = None
    for _ in range(2):  # one retry when the answer is not the JSON asked for
        r = llm.chat(spec, msgs, [], max_tokens=16_000)
        try:
            out = _json_of(r.content)
            for k in ("scientific_quality", "compliance"):
                out[k]["score"] = max(0.0, min(10.0, float(out[k]["score"])))
            return out, r.usage
        except (ValueError, KeyError, TypeError) as e:
            last = e
            msgs += [r.raw_message, {"role": "user", "content": "Answer with the JSON object only, as specified."}]
    raise ValueError(f"judge {spec.model}: {last}")


def run(root: pathlib.Path, subject: str, context: str, out: pathlib.Path, judges: list[str], cap_usd: float,
        teams: list[str] | None = None) -> dict:
    cfg = load_config()
    out.mkdir(parents=True, exist_ok=True)
    b = Budget(out)
    b.set_caps(llm_usd=cap_usd)
    brief = (ROOT / "prompts" / "jury.md").read_text()
    states = sorted(p.parent for p in root.glob("*/mission.json"))
    if teams:
        states = [s for s in states if s.name in teams]
    specs = {p: llm.resolve(cfg, p, "frontier", "high") for p in judges}
    names = identity_names(cfg)
    general, incidents = incidents_of(context)
    general = redact(general, names)[0]
    labels = pseudonyms([st.name for st in states])
    (out / "identities.json").write_text(json.dumps(labels, indent=1) + "\n")  # for the operator, never a judge
    teams_out: dict[str, dict] = {}
    work = []
    for st in states:
        s = json.loads((st / "mission.json").read_text())
        f = facts(st)
        ws = pathlib.Path(s["workspace"])
        has_work = any(report_files(ws)) or any((ws / r).exists() for r in RECORDS)
        inc = incidents.get(st.name) or incidents.get(f["provider"]) or ""
        text, n = redact(team_file(ws, f, labels[st.name], inc), names) if has_work else ("", 0)
        leak = leaks(text, names)
        (out / "files").mkdir(exist_ok=True)
        (out / "files" / f"{labels[st.name]}.md").write_text(text)  # exactly what the judges read
        teams_out[st.name] = {"label": labels[st.name], "facts": f, "measured": measured_scores(f), "judges": {},
                              "errors": {}, "has_work": has_work, "redactions": n, "leaks": leak, "file": text}
        for p in judges:
            if p != f["provider"] and has_work and not leak:
                work.append((st.name, p))
            elif leak and p != f["provider"]:
                teams_out[st.name]["errors"][p] = f"not sent: names still found after redaction ({', '.join(leak[:5])})"

    def one(item):
        name, p = item
        if b.llm_spent() >= cap_usd:
            return name, p, None, "jury budget spent"
        try:
            verdict, usage = judge(specs[p], brief, subject, general, teams_out[name]["file"])
            b.charge_llm("jury", f"judge:{p}", p, specs[p].model, usage)
            return name, p, verdict, None
        except Exception as e:  # noqa: BLE001 - a failed judge is recorded, the others still count
            return name, p, None, f"{type(e).__name__}: {str(e)[:300]}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for name, p, verdict, err in ex.map(one, work):
            if verdict:
                teams_out[name]["judges"][p] = {"model": specs[p].model, **verdict}
            else:
                teams_out[name]["errors"][p] = err
    return aggregate(teams_out, b.llm_spent())


def aggregate(teams: dict, jury_usd: float, weights: dict | None = None) -> dict:
    w = weights or {c: 1.0 for c in CRITERIA}
    for t in teams.values():
        sc = dict(t["measured"])
        for c in ("scientific_quality", "compliance"):
            v = [j[c]["score"] for j in t["judges"].values()]
            sc[c] = round(statistics.median(v), 2) if v else 0.0
            t[c + "_spread"] = round(max(v) - min(v), 2) if len(v) > 1 else None
        t["scores"] = sc
        t["final"] = round(sum(w[c] * sc[c] for c in CRITERIA) / sum(w.values()), 2)
    order = sorted(teams, key=lambda n: (not teams[n]["facts"]["delivered"], -teams[n]["final"]))
    for i, n in enumerate(order, 1):
        teams[n]["rank"] = i
        teams[n].pop("file", None)
    return {"criteria": list(CRITERIA), "weights": w, "jury_llm_usd": round(jury_usd, 4), "ranking": order, "teams": teams}


# ---------------------------------------------------------------- report


def _spread(x: dict, c: str) -> str:
    """Half the range of the judges' grades, when there are several."""
    return f" (±{x[c + '_spread'] / 2:g})" if x.get(c + "_spread") else ""


def markdown(res: dict, title: str = "Jury of the race") -> str:
    t = res["teams"]
    lines = [f"# {title}", "",
             "Cost control and delivery time are measured by the framework from its ledgers; scientific quality and "
             "compliance are the median grade of LLM judges on other providers than the team's, who graded anonymous "
             "files (a pseudonym per team, every name of provider, model or harness redacted). Final score: mean of "
             "the four criteria" + (" with weights " + ", ".join(f"{LABELS[c]} {res['weights'][c]}" for c in CRITERIA)
                                    if len(set(res["weights"].values())) > 1 else ", equal weights") +
             "; delivered teams rank first. All grades out of 10.", "",
             "| Rank | Team | Graded as | Delivered | Final | Scientific quality | Compliance | Cost control | "
             "Delivery time | LLM / GPU spent (USD) | Judges |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n in res["ranking"]:
        x, f, s = t[n], t[n]["facts"], t[n]["scores"]
        lines.append(f"| {x['rank']} | {f['team']} | {x['label']} | "
                     f"{f['delivered_at'][11:16] + ' UTC' if f['delivered'] else 'no'} | "
                     f"**{x['final']:g}** | {s['scientific_quality']:g}{_spread(x, 'scientific_quality')} | "
                     f"{s['compliance']:g}{_spread(x, 'compliance')} | {s['cost_control']:g} | {s['delivery_time']:g} | "
                     f"{f['llm_usd']:.2f} / {f['gpu_usd']:.2f} | {', '.join(x['judges']) or '—'} |")
    lines += ["", f"The jury's own model calls cost {res['jury_llm_usd']:.2f} USD.", ""]
    for n in res["ranking"]:
        x, f = t[n], t[n]["facts"]
        lines += [f"## {x['rank']}. {f['team']} ({x['label']})", "",
                  f"{f['harness']} on {f['provider']} ({f['tier']} tier, {f['reasoning']} reasoning). "
                  + (f"Delivered at {f['delivered_at'][11:16]} UTC after {f['elapsed_hours']:.2f} h of "
                     f"{f['duration_hours']:g} h." if f["delivered"] else "Did not deliver.")
                  + f" Spent {f['llm_usd']:.2f} of {f['llm_cap_usd']} USD of model calls and {f['gpu_usd']:.2f} of "
                  f"{f['gpu_cap_usd']} USD of GPU.", ""]
        if not x["judges"]:
            lines += ["No work to grade: " + ("; ".join(x["errors"].values()) or "nothing in the repository.") + "", ""]
            continue
        for p, j in x["judges"].items():
            lines += [f"**Judge {p}** ({j['model']}): scientific quality {j['scientific_quality']['score']:g}, "
                      f"compliance {j['compliance']['score']:g}. {j.get('summary', '')}", ""]
            if j.get("means"):
                lines += [f"- Means: {j['means']}"]
            ctx = j.get("context") or {}
            if ctx.get("effect"):
                lines += [f"- Context: {ctx['effect']}"]
            for k, lab in (("strengths", "Strength"), ("weaknesses", "Weakness")):
                lines += [f"- {lab}: {s}" for s in (j.get(k) or [])[:3] if s]
            lines += [""]
        for p, e in x["errors"].items():
            lines += [f"Judge {p} gave no grade: {e}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m frontier jury", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, help="directory holding the missions' state directories")
    ap.add_argument("--subject", required=True, help="the subject as text (a .pdf is converted with pdftotext)")
    ap.add_argument("--context", required=True, help="Markdown file: the race's budgets, time and incidents")
    ap.add_argument("--out", required=True, help="the jury's directory: its ledger, jury.json, JURY.md")
    ap.add_argument("--judge", action="append", default=[], help="provider of a judge (repeat); default: openai, "
                    "anthropic, google")
    ap.add_argument("--team", action="append", default=[], help="grade only these missions (repeat)")
    ap.add_argument("--cap-usd", type=float, default=15.0, help="cap on the jury's own model calls")
    a = ap.parse_args(argv)
    subj = pathlib.Path(a.subject)
    subject = (subprocess.run(["pdftotext", "-layout", str(subj), "-"], capture_output=True, text=True, check=True).stdout
               if subj.suffix.lower() == ".pdf" else subj.read_text())
    res = run(pathlib.Path(a.root), subject, pathlib.Path(a.context).read_text(), pathlib.Path(a.out),
              a.judge or ["openai", "anthropic", "google"], a.cap_usd, a.team or None)
    out = pathlib.Path(a.out)
    (out / "jury.json").write_text(json.dumps(res, indent=1) + "\n")
    (out / "JURY.md").write_text(markdown(res))
    print(markdown(res).split("\n\n## ")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())

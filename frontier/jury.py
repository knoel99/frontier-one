"""Jury of a race: grade every team and rank them.

    python3 -m frontier jury --root DIR --subject FILE --context FILE --out DIR [--judge openai --judge anthropic ...]

The scores are frontier/scoring.py's: scientific quality (rubrics x adequacy to the subject) and code and data graded
by the jury, efficiency measured from the ledgers (`facts`), weighted 50, 25 and 25. They are graded by a panel of LLM
judges on several providers, a judge never grading a team of its own provider. Grading is anonymous: each team gets a
random pseudonym (Team A, B, ...), and every name that tells its author (providers, brands, model families and ids,
harnesses, from config/providers.toml `names`, `tiers` and the harness list) is replaced by [redacted] in what a judge
reads; a file in which a name is still found is not sent. Each judge reads the subject, the general race context, the
incidents of that team (inside or outside its control), an optional calibration example (`--calibration`: a study of
known means that fixes the scale, not a reference the teams are compared with) and the team's file (its report,
records, delivered code, repository listing and small result files, as the team left them), and answers in JSON
(`prompts/jury.md`). The pseudonyms are lifted only in the results. A criterion's grade is the median of the judges;
delivered teams rank before the others. The jury's own calls are metered against `--cap-usd` in its ledger.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import functools
import json
import pathlib
import random
import re
import statistics
import subprocess
import sys

from . import llm
from .budget import CLOUD_CURRENCY, Budget, parse_time, read_jsonl
from .config import ROOT, load_config
from .harnesses import HARNESSES, McpCommand, artifacts
from . import scoring
from .mission import report_files

CRITERIA = ("scientific_quality", "artifacts", "efficiency")
RECORDS = ("DELIVERY.md", "COMPLIANCE.md", "CHECKLIST.md", "DECISIONS.md")
MAX_DOC = 30_000  # characters of one record in a judge's file
MAX_RESULTS = 120_000  # characters of small result files in a judge's file
MAX_CODE = 120_000  # characters of delivered code in a judge's file
HIDDEN = (".agents/", ".claude/", ".codex/", ".gemini/", ".dsh/", ".kimi", ".qwen/", ".vibe/", ".grok/", ".zcode",
          "subject/", "AGENTS.md", "MISSION.md", "TOOLS.md", "CLAUDE.md", "GEMINI.md", "QWEN.md")


@functools.cache
def _generated() -> frozenset[str]:
    """Every file the framework writes into a workspace for any harness (.mcp.json, opencode.json, role files, ...)."""
    return frozenset(artifacts(McpCommand()))


def _tracked(ws: pathlib.Path) -> list[str]:
    """The team's own tracked files: what the framework generated is left out. -z keeps non-ASCII names unquoted."""
    out = subprocess.run(["git", "-C", str(ws), "ls-files", "-z"], capture_output=True, text=True).stdout.split("\0")
    gen = _generated()
    return [x for x in out if x and not x.startswith(HIDDEN) and x not in gen]


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


CODE_EXT = (".py", ".jl", ".m", ".r", ".R", ".cpp", ".c", ".f90", ".rs", ".go", ".ipynb", ".sh", ".js", ".ts")


def deliverables(ws: pathlib.Path, state: pathlib.Path) -> dict[str, bool]:
    """The deliverables any mission asks for, whatever the discipline: present or not."""
    files = _tracked(ws)
    reports = report_files(ws) if ws.is_dir() else []
    pubs = json.loads((state / "publications.json").read_text()) if (state / "publications.json").exists() else []
    checklist = (ws / "CHECKLIST.md").exists() or any("checklist" in p.read_text(errors="replace").lower() for p in reports)
    return {"report": bool(reports), "code": any(x.endswith(CODE_EXT) for x in files),
            "DELIVERY.md": (ws / "DELIVERY.md").exists(), "DECISIONS.md": (ws / "DECISIONS.md").exists(),
            "COMPLIANCE.md": (ws / "COMPLIANCE.md").exists(), "CITATION.cff": (ws / "CITATION.cff").exists(),
            "checklist": checklist, "publications": bool(pubs)}


def stewards(events: list[dict], dv: dict | None, intact: bool) -> tuple[list[dict], bool]:
    """Infractions found in the ledgers: (penalties with their points and reason, disqualified)."""
    out = []
    refused = sorted({e.get("host") for e in events if e.get("event") == "egress" and e.get("allowed") is False})
    for h in refused:
        out.append({"points": 5, "reason": f"tried to reach a blocked host ({h})"})
    if dv and dv.get("missing"):
        out.append({"points": 5, "reason": "forced a delivery with deliverables missing: " + ", ".join(dv["missing"])})
    if dv and dv.get("report_paths"):
        out.append({"points": 2, "reason": "delivered a report that names files of its repository"})
    if not intact:
        out.append({"points": 0, "reason": "a ledger was altered: disqualified"})
    return out, not intact


def facts(state: pathlib.Path) -> dict:
    """What the ledgers say about one mission: delivery, time (red flags neutralised), spend on model calls and on
    simulations (rented machines and local CPU), deliverables, integrity, stewards' penalties."""
    s = json.loads((state / "mission.json").read_text())
    cfg = load_config()
    b = Budget(state)
    caps = b.caps
    start, deadline = parse_time(caps.get("start")), parse_time(caps.get("deadline"))
    dv = json.loads((state / "delivery.json").read_text()) if (state / "delivery.json").exists() else None
    models: dict[str, int] = {}
    for e in b.llm_entries():
        if e["frontier.source"] != "launcher":
            models[e["gen_ai.request.model"]] = models.get(e["gen_ai.request.model"], 0) + 1
    rented = sum(b.cloud_spent(p) for p in CLOUD_CURRENCY)
    sim_cap = sum(float(caps[k]) for k, _, _ in CLOUD_CURRENCY.values() if caps.get(k))
    events = read_jsonl(state / "events.jsonl")
    core_s = max([e.get("core_seconds", 0) for e in events if e.get("event") == "cpu_usage"], default=None)
    local_usd = (core_s / 3600 * cfg["limits"].get("local_cpu_usd_per_core_hour", 0)) if core_s is not None else None
    neutral_min = sum(float(e.get("minutes", 0)) for e in events if e.get("event") == "red_flag")
    duration_h = (deadline - start).total_seconds() / 3600 if start and deadline else None
    elapsed_h = float(dv["elapsed_hours"]) if dv else duration_h
    intact = all(v is True for v in b.verify().values())
    have = deliverables(pathlib.Path(s["workspace"]), state)
    penalties, dsq = stewards(events, dv, intact)
    llm_usd = b.llm_spent()
    return {"team": s.get("name", state.name), "provider": s["provider"], "harness": s["harness"], "tier": s["tier"],
            "reasoning": s["reasoning"], "delivered": dv is not None,
            "delivered_at": dv and dv["delivered_at"], "elapsed_hours": elapsed_h and round(elapsed_h, 3),
            "duration_hours": duration_h and round(duration_h, 3), "neutralised_minutes": round(neutral_min, 1),
            "minutes": round(max((elapsed_h or 0) * 60 - neutral_min, 0), 1),
            "llm_usd": round(llm_usd, 4), "llm_cap_usd": caps.get("llm_usd"),
            "rented_usd": round(rented, 4), "local_cpu_core_hours": core_s and round(core_s / 3600, 3),
            "local_cpu_usd": local_usd and round(local_usd, 4),
            "simulation_usd": round(rented + (local_usd or 0), 4), "simulation_cap_usd": sim_cap or None,
            "total_usd": round(llm_usd + rented + (local_usd or 0), 4), "models_called": models,
            "sessions": sum(e.get("event") == "session_start" for e in events), "ledgers_intact": intact,
            "deliverables": have, "deliverables_share": round(sum(have.values()) / len(have), 3),
            "classified": dv is not None or sum(have.values()) / len(have) >= scoring.CLASSIFIED_SHARE,
            "penalties": penalties, "disqualified": dsq}


# ---------------------------------------------------------------- the team's file


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f"\n[... {len(text) - n} more characters not shown]"


def team_file(ws: pathlib.Path, f: dict, label: str, incidents: str) -> str:
    """What a judge reads about one team, as the team left it, before redaction."""
    shown = {"team": label, "delivered": f["delivered"], "minutes_counted": f["minutes"],
             "minutes_neutralised": f["neutralised_minutes"], "allowed_hours": f["duration_hours"],
             "model_calls_usd": f["llm_usd"], "model_calls_cap_usd": f["llm_cap_usd"],
             "simulations_usd": f["simulation_usd"], "rented_machines_usd": f["rented_usd"],
             "local_cpu_core_hours": f["local_cpu_core_hours"], "simulations_cap_usd": f["simulation_cap_usd"],
             "sessions": f["sessions"], "deliverables": f["deliverables"], "ledgers_intact": f["ledgers_intact"]}
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
    files = [x for x in _tracked(ws) if (ws / x).is_file()]
    parts += ["## Repository listing (path, size)", "", "\n".join(f"{x}  {(ws / x).stat().st_size} B" for x in files[:400]), ""]
    code, used = [], 0
    for x in files:
        p = ws / x
        if p.suffix in CODE_EXT and used < MAX_CODE:
            body = _clip(p.read_text(errors="replace"), MAX_CODE - used)
            code.append(f"### {x}\n\n```\n{body}\n```\n")
            used += len(body)
    parts += ["## Delivered code", "", "\n".join(code) or "(none)", ""]
    small, used = [], 0
    for x in files:
        p = ws / x
        if (p.suffix in (".json", ".csv", ".md", ".txt") and p.stat().st_size <= 30_000 and x not in RECORDS
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


def validate(v: dict) -> None:
    """A judge's answer has every rubric, scored 0 to 100, and an adequacy among scoring.ADEQUACY (raises if not)."""
    sq, art = v["scientific_quality"], v["artifacts"]
    for rubric_set, part in ((scoring.SCIENCE_RUBRICS, sq["rubrics"]), (scoring.ARTIFACT_RUBRICS, art["rubrics"])):
        for k in rubric_set:
            part[k]["score"] = scoring.clip(float(part[k]["score"]))
    if sq.get("adequacy") not in scoring.ADEQUACY:
        raise ValueError(f"adequacy must be one of {sorted(scoring.ADEQUACY)}")


def judge(spec: llm.ModelSpec, brief: str, subject: str, context: str, dossier: str,
          calibration: str = "") -> tuple[dict, dict]:
    cal = f"# Calibration example (not a competitor)\n\n{calibration}\n\n" if calibration else ""
    msgs = [{"role": "system", "content": brief},
            {"role": "user", "content": f"# Race context\n\n{context}\n\n# The subject\n\n{subject}\n\n{cal}"
                                        f"# The team's file\n\n{dossier}"}]
    last = None
    for _ in range(2):  # one retry when the answer is not the JSON asked for
        r = llm.chat(spec, msgs, [], max_tokens=16_000)
        try:
            out = _json_of(r.content)
            validate(out)
            return out, r.usage
        except (ValueError, KeyError, TypeError) as e:
            last = e
            msgs += [r.raw_message, {"role": "user", "content": "Answer with the JSON object only, as specified."}]
    raise ValueError(f"judge {spec.model}: {last}")


def run(root: pathlib.Path, subject: str, context: str, out: pathlib.Path, judges: list[str], cap_usd: float,
        teams: list[str] | None = None, calibration: str = "") -> dict:
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
    calibration = redact(calibration, names)[0] if calibration else ""
    if calibration:
        (out / "files").mkdir(parents=True, exist_ok=True)
        (out / "files" / "calibration.md").write_text(calibration)
    labels = pseudonyms([st.name for st in states])
    (out / "identities.json").write_text(json.dumps(labels, indent=1) + "\n")  # for the operator, never a judge
    teams_out: dict[str, dict] = {}
    work = []
    for st in states:
        s = json.loads((st / "mission.json").read_text())
        f = facts(st)
        ws = pathlib.Path(s["workspace"])
        has_work = ws.is_dir() and (any(report_files(ws)) or any((ws / r).exists() for r in RECORDS))
        inc = incidents.get(st.name) or incidents.get(f["provider"]) or ""
        text, n = redact(team_file(ws, f, labels[st.name], inc), names) if has_work else ("", 0)
        leak = leaks(text, names)
        (out / "files").mkdir(exist_ok=True)
        (out / "files" / f"{labels[st.name]}.md").write_text(text)  # exactly what the judges read
        teams_out[st.name] = {"label": labels[st.name], "facts": f, "judges": {},
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
            verdict, usage = judge(specs[p], brief, subject, general, teams_out[name]["file"], calibration)
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


def _median(vals: list[float]) -> float | None:
    return round(statistics.median(vals), 1) if vals else None


def aggregate(teams: dict, jury_usd: float) -> dict:
    """Each rubric: the judges' median. Scientific quality, code and data, efficiency and the final score as
    frontier/scoring.py defines them. Classified teams (delivered, or 90 % of the deliverables) rank first."""
    for t in teams.values():
        js = list(t["judges"].values())
        # work that no judge could grade (every call failed) is not graded, not worth 0; no work at all is 0
        t["graded"] = bool(js) or not t.get("has_work", bool(js))
        sr = {k: _median([j["scientific_quality"]["rubrics"][k]["score"] for j in js]) for k in scoring.SCIENCE_RUBRICS}
        ar = {k: _median([j["artifacts"]["rubrics"][k]["score"] for j in js]) for k in scoring.ARTIFACT_RUBRICS}
        adeq = [j["scientific_quality"]["adequacy"] for j in js]
        adequacy = min(adeq, key=lambda a: scoring.ADEQUACY[a]) if adeq else "on_subject"  # the strictest judge
        if js:
            q, a = scoring.science(sr, adequacy), scoring.artifacts(ar)
        else:
            q, a = (0.0, 0.0) if t["graded"] else (None, None)
        t["rubrics"] = {"science": sr, "artifacts": ar, "adequacy": adequacy}
        t["scores"] = {"scientific_quality": q if q is None else round(q, 1), "artifacts": a if a is None else round(a, 1)}
        spread = [j["scientific_quality"]["rubrics"]["soundness"]["score"] for j in js]
        t["soundness_spread"] = round(max(spread) - min(spread), 1) if len(spread) > 1 else None
    eff = scoring.efficiency({n: {"quality": t["scores"]["scientific_quality"], "usd": t["facts"]["total_usd"],
                                  "minutes": t["facts"]["minutes"], "classified": t["facts"]["classified"]}
                              for n, t in teams.items()})
    for n, t in teams.items():
        t["efficiency"] = eff[n]
        t["scores"]["efficiency"] = eff[n]["efficiency"]
        pen = sum(p["points"] for p in t["facts"]["penalties"])
        t["penalty"] = pen
        t["final"] = (scoring.final(t["scores"]["scientific_quality"], t["scores"]["artifacts"], eff[n]["efficiency"], pen,
                                    t["facts"]["disqualified"]) if t["graded"] else None)
    order = sorted(teams, key=lambda n: (not teams[n]["graded"], not teams[n]["facts"]["classified"],
                                         -(teams[n]["final"] or 0)))
    for i, n in enumerate(order, 1):
        teams[n]["rank"] = i if teams[n]["graded"] else None
        teams[n].pop("file", None)
    return {"criteria": list(CRITERIA), "weights": dict(scoring.WEIGHTS), "jury_llm_usd": round(jury_usd, 4),
            "ranking": order, "teams": teams}


# ---------------------------------------------------------------- report


def _num(v) -> str:
    return "not graded" if v is None else f"{v:g}"


def markdown(res: dict) -> str:
    t = res["teams"]
    w = res["weights"]
    lines = ["# Jury of the race", "",
             f"Final score = {w['scientific_quality']:g} % scientific quality + {w['artifacts']:g} % code and data + "
             f"{w['efficiency']:g} % efficiency, minus the stewards' penalties (frontier/scoring.py). Scientific quality "
             "and code and data are graded by the jury on anonymous files, on absolute logarithmic scales; efficiency is "
             "measured: half absolute (total spend and time, 10 points per doubling above 5 USD or 5 minutes), half "
             "relative (doublings of advance on the competitors of the same order of science). Classified teams "
             "(delivered, or 90 % of the deliverables) rank first. All out of 100.", "",
             "| Rank | Team | Graded as | Delivered | Final | Scientific quality | Code and data | Efficiency | "
             "Penalty | Spent: model calls / simulations (USD) | Time (min) | Judges |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n in res["ranking"]:
        x, f, s = t[n], t[n]["facts"], t[n]["scores"]
        lines.append(f"| {x['rank'] or '—'} | {f['team']} | {x['label']} | "
                     f"{f['delivered_at'][11:16] + ' UTC' if f['delivered'] else ('classified' if f['classified'] else 'no')} | "
                     f"**{_num(x['final'])}** | {_num(s['scientific_quality'])} | {_num(s['artifacts'])} | "
                     f"{_num(s['efficiency'])} | {x['penalty'] or '—'} | {f['llm_usd']:.2f} / {f['simulation_usd']:.2f} | "
                     f"{f['minutes']:g} | {', '.join(x['judges']) or '—'} |")
    lines += ["", f"The jury's own model calls cost {res['jury_llm_usd']:.2f} USD.", ""]
    for n in res["ranking"]:
        x, f = t[n], t[n]["facts"]
        lines += [f"## {x['rank'] or 'Not graded'}. {f['team']} ({x['label']})", "",
                  f"{f['harness']} on {f['provider']} ({f['tier']} tier, {f['reasoning']} reasoning). "
                  + (f"Delivered at {f['delivered_at'][11:16]} UTC after {f['minutes']:g} min counted." if f["delivered"]
                     else "Did not deliver.")
                  + f" Spent {f['llm_usd']:.2f} USD on model calls and {f['simulation_usd']:.2f} USD on simulations.", ""]
        r = x.get("rubrics") or {}
        if r.get("science") and any(v is not None for v in r["science"].values()):
            lines += ["Rubrics: " + ", ".join(f"{k} {_num(v)}" for k, v in r["science"].items())
                      + f"; adequacy to the subject: {r['adequacy']}; code and data: "
                      + ", ".join(f"{k} {_num(v)}" for k, v in r["artifacts"].items()) + ".", ""]
        e = x.get("efficiency") or {}
        if e:
            lines += [f"Efficiency: absolute {e['absolute']:g}, relative {e['relative']:g} against "
                      f"{', '.join(t[p]['label'] for p in e['peers']) or 'no competitor of the same order'}, "
                      f"counted at {e['guard']:g}.", ""]
        for pen in f["penalties"]:
            lines += [f"- Stewards: {pen['reason']} ({pen['points']} points)."]
        if not x["judges"]:
            lines += ["No work to grade: " + ("; ".join(x["errors"].values()) or "nothing in the repository.") + "", ""]
            continue
        for p, j in x["judges"].items():
            lines += [f"**Judge {p}** ({j['model']}). {j.get('summary', '')}", ""]
            if j.get("means"):
                lines += [f"- Means: {j['means']}"]
            ctx = j.get("context") or {}
            if ctx.get("effect"):
                lines += [f"- Context: {ctx['effect']}"]
            for k, lab in (("strengths", "Strength"), ("weaknesses", "Weakness")):
                lines += [f"- {lab}: {s}" for s in (j.get(k) or [])[:3] if s]
            lines += [""]
        for p, err in x["errors"].items():
            lines += [f"Judge {p} gave no grade: {err}", ""]
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
    ap.add_argument("--calibration", help="Markdown file: a study of known means that fixes the scale (redacted too)")
    a = ap.parse_args(argv)
    subj = pathlib.Path(a.subject)
    subject = (subprocess.run(["pdftotext", "-layout", str(subj), "-"], capture_output=True, text=True, check=True).stdout
               if subj.suffix.lower() == ".pdf" else subj.read_text())
    res = run(pathlib.Path(a.root), subject, pathlib.Path(a.context).read_text(), pathlib.Path(a.out),
              a.judge or ["openai", "anthropic", "google"], a.cap_usd, a.team or None,
              pathlib.Path(a.calibration).read_text() if a.calibration else "")
    out = pathlib.Path(a.out)
    (out / "jury.json").write_text(json.dumps(res, indent=1) + "\n")
    (out / "JURY.md").write_text(markdown(res))
    print(markdown(res).split("\n\n## ")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Missions: what the owner gives (a harness, an LLM key and budget, Scaleway and Runpod keys and budgets, a Hugging
Face token, the subject, a deadline) turned into a workspace the AI works in, and the delivery that ends it.

Workspace layout (a git repository the AI works in):
  AGENTS.md, MISSION.md, TOOLS.md   generated: how to work here, the brief, the inventory of tools
  subject/                          the internship subject
  <harness files>                   MCP config, sub-agents and skills for the chosen harness (frontier/harnesses.py)

State, outside the workspace (default ~/.frontier/missions/<name>/, or [mission] state): shared by every process
of the mission and written only by the framework: mission.json, budgets.json, llm.jsonl and events.jsonl
(hash-chained), resources.json, reservations.json, publications.json, delivery.json, sessions/.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import tomllib
import urllib.request

from . import budget as budget_mod
from . import llm
from .config import ROOT, load_config
from .harnesses import HARNESSES, McpCommand, artifacts

STATE_ROOT = pathlib.Path.home() / ".frontier" / "missions"
REQUIRED = ["DELIVERY.md", "COMPLIANCE.md", "DECISIONS.md"]
NATIVE_HARNESS = {"anthropic": "claude", "openai": "codex", "google": "antigravity", "xai": "grok", "moonshot": "kimi",
                  "alibaba": "qwen", "mistral": "vibe", "deepseek": "dsh"}
FALLBACK_HARNESS = "opencode"


def load(path: str | os.PathLike) -> dict:
    path = pathlib.Path(path).resolve()
    with open(path, "rb") as f:
        m = tomllib.load(f)
    m["_file"] = str(path)
    m.setdefault("mission", {})
    ws = pathlib.Path(m["mission"].get("workspace", "workspace"))
    m["mission"]["workspace"] = str((path.parent / ws).resolve() if not ws.is_absolute() else ws)
    st = pathlib.Path(m["mission"].get("state") or STATE_ROOT / m["mission"].get("name", "mission"))
    m["mission"]["state"] = str((path.parent / st).resolve() if not st.is_absolute() else st)
    h = m.setdefault("harness", {})
    if h.get("name", "auto") == "auto":
        h["name"] = NATIVE_HARNESS.get(m.get("llm", {}).get("provider", ""), FALLBACK_HARNESS)
    if h["name"] not in HARNESSES:
        raise ValueError(f"unknown harness {h['name']!r}; known: {', '.join(HARNESSES)}")
    return m


def _deadline(m: dict, start: dt.datetime) -> str | None:
    if m["mission"].get("deadline"):
        return budget_mod.parse_time(m["mission"]["deadline"]).isoformat(timespec="seconds")
    if m["mission"].get("duration_hours"):
        return (start + dt.timedelta(hours=float(m["mission"]["duration_hours"]))).isoformat(timespec="seconds")
    return None


def sandbox_kind(m: dict) -> str:
    kind = m.get("sandbox", {}).get("kind", "none")
    if kind not in ("none", "openshell"):
        raise ValueError(f"[sandbox] kind must be none or openshell, not {kind!r}")
    return kind


def _paths(m: dict, state) -> tuple[str, str]:
    """Where this repository and the mission state are as the harness sees them (inside a sandbox or not)."""
    if sandbox_kind(m) == "openshell":
        from .sandbox import IN_ROOT, IN_STATE

        return IN_ROOT, IN_STATE
    return str(ROOT), str(state)


def providers_of(m: dict) -> list[str]:
    llm_sec = m.get("llm", {})
    return [p for p in [llm_sec.get("provider"), *llm_sec.get("other_providers", [])] if p]


# ---------------------------------------------------------------- inventory (TOOLS.md)


def _models_section(cfg: dict, providers: list[str], pings: dict) -> str:
    prices = budget_mod.prices()
    out = []
    for prov in providers:
        p = cfg["providers"][prov]
        out.append(f"### {prov}" + (f" ({pings[prov]})" if prov in pings else ""))
        out.append("")
        out.append("| tier | model | USD / M input | USD / M cached input | USD / M output |")
        out.append("|---|---|---:|---:|---:|")
        for tier in llm.TIERS:
            spec = llm.resolve(cfg, prov, tier, "medium")
            pr = prices.get(spec.model, {})
            fallback = "" if tier in p["tiers"] else " (nearest tier)"
            extra = []
            if pr.get("offpeak_x"):
                extra.append(f"× {pr['offpeak_x']} outside {', '.join(pr.get('peak_utc', []))} UTC on weekdays")
            if pr.get("note"):
                extra.append(pr["note"])
            out.append(f"| {tier} | `{spec.model}`{fallback} | {pr.get('input', '?')} | {pr.get('cache_read', '-')} | "
                       f"{pr.get('output', '?')}" + (f" ({'; '.join(extra)})" if extra else "") + " |")
        values = ", ".join(f"{lvl} → `{llm.resolve(cfg, prov, 'frontier', lvl).effort_value}`" for lvl in llm.LEVELS)
        out.append("")
        out.append(f"Reasoning levels (value sent to the API): {values}.")
        modes = p.get("modes", {})
        if modes:
            out.append("")
            out.append("Speed and price modes (`spawn_agent(mode=...)`):")
            for name, md in modes.items():
                models = f" Models: {', '.join(md['models'])}." if md.get("models") else ""
                out.append(f"- `{name}`: price × {md.get('price_x', 1)}. {md.get('note', '')}{models}".rstrip())
        else:
            out.append("")
            out.append("Speed and price modes: none configured for this provider.")
        out.append("")
    return "\n".join(out)


def _offers_section(ctx) -> str:
    from . import infra

    lines = []
    try:
        scw = json.loads(infra.scw_offers(ctx, "all", 0, gpu_only=True))["offers"]
        lines += ["Scaleway GPU Instances in stock (EUR per hour, all zones):", "",
                  "| type | zone | GPU | GPU memory GB | vCPU | RAM GB | stock | EUR/h |", "|---|---|---|---:|---:|---:|---|---:|"]
        lines += [f"| {o['type']} | {o['zone']} | {o['gpus']}× {o['gpu']} | {o['gpu_memory_gb']} | {o['vcpus']} | "
                  f"{o['ram_gb']} | {o['availability']} | {o['eur_per_hour']} |" for o in scw[:25]]
    except Exception as e:  # noqa: BLE001
        lines.append(f"Scaleway offers unavailable at preparation time: {e}")
    lines.append("")
    for cloud in ("SECURE", "COMMUNITY"):
        try:
            rp = json.loads(infra.rp_offers(ctx, 0, cloud))["offers"]
            lines += [f"Runpod GPU pods in stock, {cloud} cloud (USD per GPU per hour):", "",
                      "| GPU type id | memory GB | stock | USD/h | max count |", "|---|---:|---|---:|---:|"]
            lines += [f"| `{o['gpu_type_id']}` | {o['memory_gb']} | {o['availability']} | {o['usd_per_gpu_hour']} | "
                      f"{o['max_count']} |" for o in rp[:25]]
        except Exception as e:  # noqa: BLE001
            lines.append(f"Runpod offers ({cloud}) unavailable at preparation time: {e}")
        lines.append("")
    return "\n".join(lines)


def _main_model(cfg: dict, m: dict) -> str:
    try:
        return llm.resolve(cfg, m["llm"]["provider"], m["harness"].get("tier", "frontier"),
                           m["harness"].get("reasoning", "high")).model
    except (KeyError, llm.LLMError):
        return "?"


def tools_inventory(ctx, m: dict, cfg: dict, pings: dict, tool_specs: list[dict], offers: bool = True) -> str:
    h = HARNESSES[m["harness"]["name"]]
    caps = ctx.run.budget.caps
    when = dt.datetime.now(dt.UTC).isoformat(timespec="minutes")
    tools = "\n".join(f"| `{t['name']}` | {t['description']} |" for t in tool_specs)
    hf = m.get("huggingface", {})
    parts = [
        f"# Tools of the mission {m['mission'].get('name', '')}", "",
        f"Generated at {when} by the Frontier launcher. Prices and stock change: the tools give the live values.", "",
        "## Your harness", "",
        f"{h.title} on `{_main_model(cfg, m)}` (provider `{m.get('llm', {}).get('provider')}`, tier "
        f"`{m['harness'].get('tier', 'frontier')}`, reasoning `{m['harness'].get('reasoning', 'high')}`); its LLM calls "
        "are charged to the LLM budget like those of the sub-agents. Its own tools (shell, files, web, native "
        "sub-agents, skills) are yours as well." + (f" Note: {h.notes}." if h.notes else ""), "",
        "## Frontier tools", "",
        "Served by the `frontier` MCP server of this workspace. Without MCP, the same tools run from the shell: "
        f"`{_paths(m, ctx.run.log_dir)[0]}/bin/frontier call <tool> '<json>' --state {_paths(m, ctx.run.log_dir)[1]}` "
        "(`call list` lists them).", "",
        "| tool | what it does |", "|---|---|", tools, "",
        "## Models (for `spawn_agent`)", "",
        "Prices: list prices in USD per million tokens (config/prices.toml: the provider's own price where read, else "
        "OpenRouter's public list). Reasoning tokens are billed as output tokens. Cached input applies to a repeated "
        "prompt prefix. A speed mode multiplies the price and is billed as the tier the response says was served. "
        "Providers' batch APIs (about half price, results within 24 h) are not available through `spawn_agent`.", "",
        _models_section(cfg, providers_of(m), pings),
        "## Compute (snapshot)", "",
        (_offers_section(ctx) if offers else "Not read at preparation time: call `scw_offers` and `rp_offers`."),
        "CPU machines: `scw_offers(gpu_only=false)`; Runpod CPU pods: `runpod_mcp` tool `list-cpu-types`.", "",
        "Billing facts: a Scaleway GPU Instance is billed per minute and a CPU one per hour; a Runpod pod per second. A "
        "stopped Instance or pod still bills its disk. Every resource gets a `max_hours` after which the framework "
        "deletes it; creation is refused when price × max_hours would overrun the budget.", "",
        "## Budgets and time", "",
        f"- LLM: {caps.get('llm_usd', 'no cap')} USD, every model call of the mission (the harness included when its "
        "traffic goes through the metering gateway, and every sub-agent).",
        f"- Scaleway: {caps.get('scaleway_eur', 'no cap')} EUR. Runpod: {caps.get('runpod_usd', 'no cap')} USD.",
        f"- Start: {caps.get('start')}. Deadline: {caps.get('deadline') or 'none'}.", "",
        "## Hugging Face", "",
        (f"`hf_publish` publishes to `{hf.get('namespace') or 'the token user'}` with visibility "
         f"`{hf.get('visibility', 'private')}`." if ctx.run.settings.get("allow_publish") else
         "No Hugging Face token was given: publishing is not available."), "",
    ]
    return "\n".join(parts)


def mission_brief(m: dict, caps: dict, subject: str) -> str:
    g = m.get("grading", {})
    crit = g.get("criteria") or ["scientific quality", "cost control", "delivery time",
                                 "compliance with the subject's instructions"]
    w = g.get("weights") or []
    criteria = "\n".join(f"{i + 1}. {c}" + (f" (weight {w[i]})" if i < len(w) else "") for i, c in enumerate(crit))
    budgets = "\n".join([
        f"- LLM: {caps.get('llm_usd', 'no cap')} USD on {', '.join(providers_of(m))}.",
        f"- Scaleway: {caps.get('scaleway_eur', 'no cap')} EUR. Runpod: {caps.get('runpod_usd', 'no cap')} USD.",
        f"- Start: {caps.get('start')}. Deadline: {caps.get('deadline') or 'none'}."])
    text = (ROOT / "prompts" / "mission.md").read_text()
    for k, v in {"name": m["mission"].get("name", "mission"), "subject": subject, "criteria": criteria,
                 "budgets": budgets}.items():
        text = text.replace("{{" + k + "}}", v)
    return text


AGENTS_MD = """# AGENTS.md

This repository is a research mission run by an AI team. Read `MISSION.md` (the brief: task, evaluation, budgets,
deliverables) and `TOOLS.md` (every tool, model, price and compute offer available), then work on the mission.

- The subject is in `subject/`. The framework keeps the mission's state (budgets, ledgers) in `{state}`; it is
  written by the framework only.
- The Frontier tools come from the `frontier` MCP server; without MCP, call them from the shell:
  `{frontier} call <tool> '<json>' --state {state}`.
- Record your strategy and decisions in `DECISIONS.md` as you go; finish with the `deliver` tool.
"""


# ---------------------------------------------------------------- preparation


def _copy_subject(src: str, dest_dir: pathlib.Path, base: pathlib.Path) -> str:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src.startswith(("http://", "https://")):
        name = src.rstrip("/").rsplit("/", 1)[-1] or "subject"
        req = urllib.request.Request(src, headers={"User-Agent": "frontier-mission"})
        with urllib.request.urlopen(req, timeout=60) as r:
            (dest_dir / name).write_bytes(r.read())
        return f"subject/{name}"
    p = pathlib.Path(src)
    p = p if p.is_absolute() else base / p
    if not p.exists():
        raise FileNotFoundError(f"subject not found: {p}")
    if p.is_dir():
        shutil.copytree(p, dest_dir, dirs_exist_ok=True)
        return "subject/"
    shutil.copy2(p, dest_dir / p.name)
    return f"subject/{p.name}"


def _ping(cfg: dict, provider: str, b: budget_mod.Budget | None = None) -> str:
    spec = llm.resolve(cfg, provider, "light", "low")
    try:
        r = llm.chat(spec, [{"role": "user", "content": "Reply with OK."}], [], max_tokens=200)
        if b is not None:
            b.charge_llm("launcher", "key-check", provider, spec.model, r.usage)
        return f"key checked with {spec.model}: {'answered' if r.content or r.usage.get('output') else 'empty answer'}"
    except llm.LLMError as e:
        return f"KEY CHECK FAILED: {str(e)[:160]}"


def prepare(mission_file: str | os.PathLike, ping: bool = True, offers: bool = True) -> dict:
    """Create or refresh the mission workspace. Returns the paths and settings the launcher needs."""
    from .agent import Ctx, Run
    from .mcp import Server

    m = load(mission_file)
    cfg = load_config()
    ws = pathlib.Path(m["mission"]["workspace"])
    ws.mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
    state = pathlib.Path(m["mission"]["state"])
    state.mkdir(parents=True, exist_ok=True)
    subject = _copy_subject(m["mission"]["subject"], ws / "subject", pathlib.Path(m["_file"]).parent)

    b = budget_mod.Budget(state)
    start = budget_mod.parse_time(b.caps.get("start")) or budget_mod.now()
    b.set_caps(start=start.isoformat(timespec="seconds"), deadline=_deadline(m, start),
               llm_usd=m.get("llm", {}).get("budget_usd"), scaleway_eur=m.get("scaleway", {}).get("budget_eur"),
               runpod_usd=m.get("runpod", {}).get("budget_usd"))
    hf = m.get("huggingface", {})
    old_settings = json.loads((state / "mission.json").read_text()) if (state / "mission.json").exists() else {}
    settings = {"gateway_port": old_settings.get("gateway_port") or _free_port(), "name": m["mission"].get("name"),
                "workspace": str(ws), "harness": m["harness"]["name"],
                "provider": m.get("llm", {}).get("provider"), "providers": providers_of(m),
                "tier": m["harness"].get("tier", "frontier"), "reasoning": m["harness"].get("reasoning", "high"),
                "allow_spend": True, "allow_publish": bool(os.environ.get(hf.get("token_env", "HF_TOKEN"))),
                "publish_visibility": hf.get("visibility", "private"), "hf_namespace": hf.get("namespace", ""),
                "mission_file": m["_file"], "subject": subject, "state": str(state),
                "sandbox": "" if sandbox_kind(m) == "none" else sandbox_kind(m),
                "conf": {k: v for k, v in m.items() if k != "_file"}}
    (state / "mission.json").write_text(json.dumps(settings, indent=1) + "\n")

    run = Run(cfg, workspace=ws, allow_spend=True, run_id=state.name, log_dir=state, settings=settings)
    pings = {p: _ping(cfg, p, b) for p in settings["providers"]} if ping else {}
    os.environ["FRONTIER_STATE"] = str(state)
    specs = Server(cfg, state=state).tool_specs()
    ctx = Ctx(run, None)
    (ws / "TOOLS.md").write_text(tools_inventory(ctx, m, cfg, pings, specs, offers=offers))
    (ws / "MISSION.md").write_text(mission_brief(m, b.caps, subject))
    root_in, state_in = _paths(m, state)
    (ws / "AGENTS.md").write_text(AGENTS_MD.format(frontier=f"{root_in}/bin/frontier", state=state_in))
    mcp = McpCommand(command=f"{root_in}/bin/frontier-mcp", args=["--state", str(state_in)])
    gateway = f"http://127.0.0.1:{settings['gateway_port']}"
    target = {"provider": settings["provider"], "model": _main_model(cfg, m), "gateway": gateway,
              "key_env": m.get("llm", {}).get("key_env") or cfg["providers"][settings["provider"]]["key_env"]}
    if m["harness"]["name"] == "hermes":
        _hermes_home(state, cfg, target, mcp)
    for rel, content in artifacts(mcp, [m["harness"]["name"]], target).items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    gi = ws / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    for pat in ["__pycache__/", ".env"]:
        if pat not in lines:
            lines.append(pat)
    gi.write_text("\n".join(lines) + "\n")
    run.log(event="mission_prepared", settings=settings, pings=pings)
    return {"mission": m, "workspace": ws, "state": state, "settings": settings, "pings": pings}


def _free_port(start: int = 8787) -> int:
    import socket

    for port in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def _hermes_home(state: pathlib.Path, cfg: dict, target: dict, mcp: McpCommand) -> None:
    """Hermes reads MCP servers and its model from HERMES_HOME/config.yaml only: a home of the mission's own."""
    from .gateway import base_url_for

    port = int(target["gateway"].rsplit(":", 1)[1])
    style = cfg["providers"][target["provider"]]["style"]
    api_mode = {"anthropic": "anthropic_messages", "openai-responses": "codex_responses"}.get(style, "chat_completions")
    home = state / "hermes"
    home.mkdir(parents=True, exist_ok=True)
    args = ", ".join(json.dumps(a) for a in mcp.args)
    (home / "config.yaml").write_text(
        "model:\n  provider: custom\n"
        f"  default: {target['model']}\n  base_url: {base_url_for(port, target['provider'], cfg)}\n  api_mode: {api_mode}\n"
        f"mcp_servers:\n  frontier:\n    command: {json.dumps(mcp.command)}\n    args: [{args}]\n    timeout: 3600\n")


# ---------------------------------------------------------------- delivery (a tool of the mission)


def deliver(ctx, summary: str = "", force: bool = False) -> str:
    """Check the deliverables, delete the cloud resources still alive, write COSTS.md, freeze clock and spend."""
    from . import infra

    ws, state = ctx.workspace, ctx.run.log_dir
    done = state / "delivery.json"
    if done.exists():
        return "already delivered: " + done.read_text()
    missing = [f for f in REQUIRED if not (ws / f).exists()]
    if missing and not force:
        return ("refused: missing " + ", ".join(missing) + ". Write them, or call deliver(force=true) to deliver "
                "without them (they are then recorded as missing).")
    cleaned = infra.cleanup(ctx.run)
    st = ctx.run.budget.status()
    pubs = json.loads((state / "publications.json").read_text()) if (state / "publications.json").exists() else []
    rec = {"delivered_at": st["time"], "elapsed_hours": st.get("elapsed_hours"), "deadline": st.get("deadline"),
           "llm_usd": st["llm"]["spent_usd"], "llm_cap_usd": st["llm"]["cap_usd"],
           "scaleway_eur": st["cloud"]["scaleway"]["spent"], "scaleway_cap_eur": st["cloud"]["scaleway"]["cap"],
           "runpod_usd": st["cloud"]["runpod"]["spent"], "runpod_cap_usd": st["cloud"]["runpod"]["cap"],
           "missing": missing, "publications": [p["url"] for p in pubs], "cleanup": cleaned, "summary": summary}
    (ws / "COSTS.md").write_text(costs_markdown(st, ctx.run.budget.resources(), pubs))
    done.write_text(json.dumps(rec, indent=1) + "\n")
    ctx.run.budget.set_caps(frozen=True, frozen_llm_usd=st["llm"]["spent_usd"])
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=False)
    subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", f"Delivery\n\n{summary}".strip()], check=False)
    subprocess.run(["git", "-C", str(ws), "tag", "-f", "delivery"], check=False, capture_output=True)
    ctx.run.log(event="delivered", **{k: v for k, v in rec.items() if k != "cleanup"})
    return json.dumps(rec, indent=1)


def costs_markdown(st: dict, resources: list[dict], pubs: list[dict]) -> str:
    lines = ["# Costs", "", f"Written by the framework from its ledgers at {st['time']} (`.frontier/`).", "",
             "## LLM", "", f"Spent {st['llm']['spent_usd']:.4f} USD of {st['llm']['cap_usd']} USD"
             + (" (some prices estimated)" if st["llm"].get("estimated") else "") + ".", "",
             "| model | calls | input tokens | output tokens | USD |", "|---|---:|---:|---:|---:|"]
    for k, v in sorted(st["llm"]["by_model"].items(), key=lambda x: -x[1]["usd"]):
        lines.append(f"| {k} | {v['calls']} | {v['input']} | {v['output']} | {v['usd']:.4f} |")
    lines += ["", "## Cloud", ""]
    for prov, c in st["cloud"].items():
        lines.append(f"- {prov}: {c['spent']:.4f} {c['currency']} of {c['cap']} {c['currency']}.")
    lines += ["", "| provider | resource | id | type | price/h | max h | created | deleted |", "|---|---|---|---|---:|---:|---|---|"]
    for r in resources:
        price = r.get("eur_per_hour") or r.get("usd_per_hour")
        lines.append(f"| {r['provider']} | {r.get('kind')} | {r['id']} | {r.get('type')} | {price} | {r.get('max_hours')} | "
                     f"{r.get('created')} | {r.get('deleted_at', '-')} |")
    lines += ["", "## Time", "", f"Elapsed: {st.get('elapsed_hours')} h. Deadline: {st.get('deadline') or 'none'}.", ""]
    lines += ["## Ledgers", "", f"Integrity of the hash-chained logs: {json.dumps(st.get('ledgers_intact'))}.",
              f"Last hash of llm.jsonl: `{st.get('llm_last_hash', '')}`.", ""]
    if pubs:
        lines += ["## Publications", ""] + [f"- {p['url']} ({p['type']}, {p['visibility']})" for p in pubs] + [""]
    return "\n".join(lines)

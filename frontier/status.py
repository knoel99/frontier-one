"""`mission_status`: what the race dashboard shows about this team, for the team itself, so that it can plan with its
spend, its rate of spend and the time left. Only its own mission: other teams are never shown."""

from __future__ import annotations

import datetime as dt
import json

from .budget import now, parse_time, read_jsonl

WINDOW_MIN = 10  # rate of spend measured over the last minutes


def mission_status(ctx) -> str:
    from . import infra
    from .dashboard import team_state

    infra.enforce(ctx.run)  # as budget_status: what overran its time or budget is deleted first
    state = ctx.run.log_dir
    if not (state / "mission.json").exists():
        return infra.budget_status(ctx)
    t = team_state(state)
    st, at = t["status"], now()
    llm = st["llm"]
    since = at - dt.timedelta(minutes=WINDOW_MIN)
    recent = sum(e["frontier.cost.usd"] for e in ctx.run.budget.llm_entries() if (parse_time(e["t"]) or at) >= since)
    rate = recent * 60 / WINDOW_MIN  # USD per hour
    left_h = st.get("hours_left")
    out = {"time": st["time"], "hours_left": left_h, "elapsed_hours": st.get("elapsed_hours"), "delivered": bool(t["delivered"]),
           "llm": {"spent_usd": llm["spent_usd"], "in_flight_usd": llm["in_flight_usd"], "cap_usd": llm["cap_usd"],
                   "left_usd": llm.get("left_usd"), f"rate_usd_per_hour_last_{WINDOW_MIN}_min": round(rate, 4),
                   "hours_until_cap_at_this_rate": (round(llm["left_usd"] / rate, 2)
                                                    if rate > 0 and llm.get("left_usd") is not None else None),
                   "by_model": llm["by_model"], "by_source": llm["by_source"]},
           "tokens": t["tokens"], "cloud": {}}
    if out["llm"]["hours_until_cap_at_this_rate"] is not None and left_h is not None:
        out["llm"]["cap_reached_before_deadline"] = out["llm"]["hours_until_cap_at_this_rate"] < left_h
    for prov, c in st["cloud"].items():
        alive = [r for r in t["runners"] if r["provider"] == prov and r["state"] != "deleted"]
        hourly = sum((r["cost"] / r["hours"]) if r["hours"] else 0 for r in alive)
        left = (c["cap"] - c["spent"]) if c["cap"] is not None else None
        out["cloud"][prov] = {"currency": c["currency"], "spent": c["spent"], "committed_until_max_hours": c["committed"],
                              "cap": c["cap"], "left": round(left, 4) if left is not None else None,
                              "spending_per_hour_now": round(hourly, 4),
                              "hours_until_cap_at_this_rate": round(left / hourly, 2) if hourly and left is not None else None}
    out["machines"] = [{k: r[k] for k in ("provider", "id", "name", "type", "state", "hours", "cost", "currency", "commands")}
                       | {"last_log_lines": r["log_tail"].splitlines()[-5:]} for r in t["runners"]]
    out["sub_agents"] = [{k: r.get(k) for k in ("agent", "model", "effort", "status", "seconds", "calls", "usd", "task")}
                         for r in t["subagents"][:20]]
    events = read_jsonl(state / "events.jsonl")
    calls = [e for e in events if e.get("event") == "mcp_call"]
    out["tool_calls"] = {"total": len(calls), "errors": sum(bool(e.get("error")) for e in calls),
                         "last_errors": [f"{e.get('tool')}: {str(e.get('output'))[:160]}" for e in calls if e.get("error")][-5:]}
    out["sessions"] = sum(e.get("event") == "session_start" for e in events)
    out["ledgers_intact"] = st["ledgers_intact"]
    return json.dumps(out, indent=1, default=str)

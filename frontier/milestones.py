"""Budget milestones told to the mission's main agent.

When the LLM spend or a cloud spend crosses 10, 25, 50, 80, 95 or 98 % of its cap, the metering gateway adds a short
notice to the next calls of the harness (the request only: the harness's own history never holds it), with the
current figures, and writes a `budget_milestone` event. The agent learns it at the moment it matters even when it
does not call `mission_status`.
"""

from __future__ import annotations

import json
import threading

from .budget import CLOUD_CURRENCY, append_chained, now, parse_time, read_jsonl

MILESTONES = (10, 25, 50, 80, 95, 98)
NOTICE_CALLS = 3  # calls that carry a new milestone's notice (an auxiliary call of the harness may take the first)


class Milestones:
    def __init__(self, budget):
        self.b, self.lock = budget, threading.Lock()
        self.seen = {(e.get("budget"), e.get("percent")) for e in read_jsonl(budget.dir / "events.jsonl")
                     if e.get("event") == "budget_milestone"}
        self.left, self.label = 0, ""

    def _fractions(self) -> dict[str, tuple[float, float, str]]:
        caps, at, out = self.b.caps, now(), {}
        if caps.get("llm_usd"):
            out["LLM"] = (self.b.llm_spent(), float(caps["llm_usd"]), "USD")
        for prov, (cap_key, cur, _) in CLOUD_CURRENCY.items():
            if caps.get(cap_key):
                out[prov.capitalize()] = (self.b.cloud_spent(prov, at), float(caps[cap_key]), cur)
        return out

    def notice(self) -> str:
        """The notice to add to this call, or "" (none pending)."""
        fr = self._fractions()
        with self.lock:
            for kind, (spent, cap, _) in fr.items():
                new = [m for m in MILESTONES if spent >= cap * m / 100 and (kind, m) not in self.seen]
                for m in new:
                    self.seen.add((kind, m))
                    append_chained(self.b.dir / "events.jsonl", {
                        "t": now().isoformat(timespec="seconds"), "event": "budget_milestone", "budget": kind,
                        "percent": m, "spent": round(spent, 4), "cap": cap})
                if new:
                    self.left, self.label = NOTICE_CALLS, f"{kind} budget: {new[-1]} % reached"
            if self.left <= 0:
                return ""
            self.left -= 1
            label = self.label
        parts = [f"{k}: {s:.2f} of {c:.2f} {cur} spent ({100 * s / c:.0f} %)" for k, (s, c, cur) in fr.items()]
        deadline = parse_time(self.b.caps.get("deadline"))
        if deadline:
            parts.append(f"time left: {max((deadline - now()).total_seconds(), 0) / 3600:.2f} h")
        return (f"[Frontier budget notice: {label}. " + "; ".join(parts) + ". Calls are refused once their worst case "
                "exceeds what is left, and the delivery needs calls too. The `mission_status` tool gives the details.]")


def inject(path: str, body: bytes, note: str) -> bytes:
    """Add `note` at the end of the request's last user turn, whatever the wire (Anthropic Messages, OpenAI Chat
    Completions or Responses, Gemini). A body it does not recognise is returned unchanged."""
    if not note:
        return body
    try:
        obj = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(obj, dict):
        return body
    p = path.split("?", 1)[0]
    if isinstance(obj.get("contents"), list) and obj["contents"]:  # Gemini
        obj["contents"].append({"role": "user", "parts": [{"text": note}]})
    elif p.rstrip("/").endswith("/responses") and "input" in obj:  # OpenAI Responses
        if isinstance(obj["input"], str):
            obj["input"] += "\n\n" + note
        elif isinstance(obj["input"], list):
            obj["input"].append({"role": "user", "content": [{"type": "input_text", "text": note}]})
        else:
            return body
    elif isinstance(obj.get("messages"), list) and obj["messages"]:
        last = obj["messages"][-1]
        anthropic = p.rstrip("/").endswith("/messages")
        if anthropic and last.get("role") != "user":  # Anthropic turns alternate: only a last user turn takes it
            return body
        if last.get("role") == "user" and isinstance(last.get("content"), str):
            last["content"] += "\n\n" + note
        elif last.get("role") == "user" and isinstance(last.get("content"), list):
            last["content"].append({"type": "text", "text": note})
        elif not anthropic:  # Chat Completions after tool results: a user message of its own
            obj["messages"].append({"role": "user", "content": note})
        else:
            return body
    else:
        return body
    return json.dumps(obj).encode()

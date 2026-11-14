"""Budgets of a run or mission: LLM spend (every model call, priced from config/prices.toml) and cloud spend
(Scaleway, Runpod: price per hour of each resource of the ledger times its lifetime).

State lives on disk in the run's state directory so that every process of a mission (MCP server, shell calls,
metering gateway) shares it:
  llm.jsonl        one line per model call: who, provider, model, mode, tokens, USD
  resources.json   the cloud ledger (written by agent.Run)
  budgets.json     the caps: {"llm_usd": .., "scaleway_eur": .., "runpod_usd": .., "deadline": ISO time, "start": ISO}
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import pathlib
import tomllib

from .config import ROOT

PRICES = ROOT / "config" / "prices.toml"
CLOUD_CURRENCY = {"scaleway": ("scaleway_eur", "EUR", "eur_per_hour"), "runpod": ("runpod_usd", "USD", "usd_per_hour")}


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


_prices: dict | None = None


def prices() -> dict:
    global _prices
    if _prices is None:
        _prices = tomllib.loads(PRICES.read_text()).get("models", {}) if PRICES.exists() else {}
    return _prices


def price_of(provider: str, model: str) -> tuple[dict, bool]:
    """(price entry, exact). Unknown model: the most expensive model of its provider, flagged as an estimate."""
    p = prices()
    if model in p:
        return p[model], True
    same = [v for v in p.values() if v.get("provider") == provider] or list(p.values())
    if not same:
        return {"input": 0.0, "output": 0.0}, False
    return max(same, key=lambda v: (v.get("output") or 0) + (v.get("input") or 0)), False


def llm_cost(provider: str, model: str, usage: dict, price_x: float = 1.0) -> tuple[float, bool]:
    """USD of one call. usage: input (all prompt tokens, cached included), cached, cache_write, output."""
    pr, exact = price_of(provider, model)
    inp = usage.get("input", 0) or 0
    cached = min(usage.get("cached", 0) or 0, inp)
    write = min(usage.get("cache_write", 0) or 0, inp - cached)
    fresh = inp - cached - write
    cost = (fresh * (pr.get("input") or 0)
            + cached * (pr.get("cache_read") if pr.get("cache_read") is not None else pr.get("input") or 0)
            + write * (pr.get("cache_write") or pr.get("input") or 0)
            + (usage.get("output", 0) or 0) * (pr.get("output") or 0)) / 1e6
    return round(cost * price_x, 6), exact


def worst_case_usd(provider: str, model: str, prompt_chars: int, max_output_tokens: int, price_x: float = 1.0) -> float:
    """Upper bound of one call: the whole prompt at the uncached input price (about 4 characters per token) and every
    allowed output token."""
    usd, _ = llm_cost(provider, model, {"input": prompt_chars // 4 + 1, "output": max_output_tokens}, price_x)
    return usd


class Budget:
    def __init__(self, state_dir: pathlib.Path):
        self.dir = pathlib.Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ caps
    @property
    def caps(self) -> dict:
        f = self.dir / "budgets.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def set_caps(self, **caps) -> None:
        merged = {**self.caps, **{k: v for k, v in caps.items() if v is not None}}
        merged.setdefault("start", now().isoformat(timespec="seconds"))
        (self.dir / "budgets.json").write_text(json.dumps(merged, indent=1) + "\n")

    # ------------------------------------------------------------ LLM
    def charge_llm(self, source: str, agent: str, provider: str, model: str, usage: dict, mode: str = "",
                   price_x: float = 1.0, usd: float | None = None) -> float:
        exact = True
        if usd is None:
            usd, exact = llm_cost(provider, model, usage, price_x)
        entry = {"t": now().isoformat(timespec="seconds"), "source": source, "agent": agent, "provider": provider,
                 "model": model, "mode": mode, "usage": usage, "usd": usd, "price_exact": exact}
        with open(self.dir / "llm.jsonl", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(entry) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        return usd

    def llm_entries(self) -> list[dict]:
        f = self.dir / "llm.jsonl"
        if not f.exists():
            return []
        return [json.loads(x) for x in f.read_text().splitlines() if x.strip()]

    def llm_spent(self) -> float:
        return round(sum(e["usd"] for e in self.llm_entries()), 6)

    def llm_allowed(self, next_call_usd: float = 0.0) -> tuple[bool, str]:
        """Hard cap: refused when what is spent plus the worst case of the next call exceeds the LLM budget."""
        caps = self.caps
        if caps.get("frozen"):
            return False, "the mission is delivered: spend is frozen"
        cap = caps.get("llm_usd")
        if cap is None:
            return True, ""
        spent = self.llm_spent()
        if spent >= cap or spent + next_call_usd > cap:
            return False, (f"LLM budget: spent {spent:.4f} + next call up to {next_call_usd:.4f} > cap {cap:.2f} USD "
                           "(a cheaper model, a lower reasoning level or fewer output tokens may still fit)")
        return True, ""

    # ------------------------------------------------------------ cloud
    def resources(self) -> list[dict]:
        f = self.dir / "resources.json"
        return json.loads(f.read_text()) if f.exists() else []

    @staticmethod
    def _hours(r: dict, at: dt.datetime) -> float:
        start = parse_time(r.get("created"))
        end = parse_time(r.get("deleted_at")) or at
        return max(0.0, (end - start).total_seconds() / 3600) if start else 0.0

    def cloud_spent(self, provider: str, at: dt.datetime | None = None) -> float:
        at = at or now()
        key = CLOUD_CURRENCY[provider][2]
        return round(sum((r.get(key) or 0) * self._hours(r, at) for r in self.resources() if r["provider"] == provider), 4)

    def cloud_committed(self, provider: str, at: dt.datetime | None = None) -> float:
        """What the alive resources will still cost if they run until their max_hours."""
        at = at or now()
        key = CLOUD_CURRENCY[provider][2]
        total = 0.0
        for r in self.resources():
            if r["provider"] != provider or r.get("state") == "deleted":
                continue
            left = max(0.0, (r.get("max_hours") or 0) - self._hours(r, at))
            total += (r.get(key) or 0) * left
        return round(total, 4)

    def cloud_allowed(self, provider: str, price_per_hour: float | None, max_hours: float) -> tuple[bool, str]:
        cap_key, cur, _ = CLOUD_CURRENCY[provider]
        if self.caps.get("frozen"):
            return False, "the mission is delivered: spend is frozen"
        cap = self.caps.get(cap_key)
        if cap is None:
            return True, ""
        if price_per_hour is None:
            return False, f"no price known for this offer: creation refused under a {provider} budget"
        spent, committed = self.cloud_spent(provider), self.cloud_committed(provider)
        new = price_per_hour * max_hours
        if spent + committed + new > cap:
            return False, (f"{provider} budget: spent {spent:.2f} + committed {committed:.2f} + this one "
                           f"{new:.2f} ({price_per_hour:.3f}/h x {max_hours} h) > {cap:.2f} {cur}")
        return True, ""

    # ------------------------------------------------------------ report
    def status(self) -> dict:
        at = now()
        caps = self.caps
        llm = self.llm_entries()
        by_model: dict[str, dict] = {}
        for e in llm:
            k = f"{e['provider']}/{e['model']}" + (f"+{e['mode']}" if e.get("mode") else "")
            m = by_model.setdefault(k, {"calls": 0, "input": 0, "output": 0, "usd": 0.0})
            m["calls"] += 1
            m["input"] += e["usage"].get("input", 0) or 0
            m["output"] += e["usage"].get("output", 0) or 0
            m["usd"] = round(m["usd"] + e["usd"], 6)
        out = {"time": at.isoformat(timespec="seconds"),
               "llm": {"spent_usd": self.llm_spent(), "cap_usd": caps.get("llm_usd"), "by_model": by_model,
                       "estimated": any(not e.get("price_exact", True) for e in llm)},
               "cloud": {}}
        for prov, (cap_key, cur, _) in CLOUD_CURRENCY.items():
            out["cloud"][prov] = {"currency": cur, "spent": self.cloud_spent(prov, at), "committed": self.cloud_committed(prov, at),
                                  "cap": caps.get(cap_key),
                                  "alive": [r["id"] for r in self.resources() if r["provider"] == prov and r.get("state") != "deleted"]}
        start, deadline = parse_time(caps.get("start")), parse_time(caps.get("deadline"))
        if start:
            out["elapsed_hours"] = round((at - start).total_seconds() / 3600, 3)
        if deadline:
            out["deadline"] = caps["deadline"]
            out["hours_left"] = round((deadline - at).total_seconds() / 3600, 3)
        if out["llm"]["cap_usd"] is not None:
            out["llm"]["left_usd"] = round(out["llm"]["cap_usd"] - out["llm"]["spent_usd"], 4)
        return out

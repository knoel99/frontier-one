"""Budgets of a run or mission: LLM spend (every model call, priced from config/prices.toml) and cloud spend
(Scaleway, Runpod: price per hour of each resource of the ledger times its lifetime).

State lives on disk in the run's state directory so that every process of a mission (MCP server, shell calls,
metering gateway) shares it:
  llm.jsonl        one line per model call: who, provider, model, mode, tokens, USD
  resources.json   the cloud ledger (written by agent.Run)
  budgets.json     the caps: {"llm_usd": .., "scaleway_eur": .., "runpod_usd": .., "deadline": ISO time, "start": ISO}
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import functools
import hashlib
import json
import pathlib
import tomllib
import uuid

from .config import ROOT

PRICES = ROOT / "config" / "prices.toml"
RESERVATION_TTL_S = 7200  # a reservation older than this belongs to a call that died
POST_DELIVERY_GRACE_USD = 0.25  # LLM spend allowed after `deliver`, so the harness can close its turn
CLOUD_CURRENCY = {"scaleway": ("scaleway_eur", "EUR", "eur_per_hour"), "runpod": ("runpod_usd", "USD", "usd_per_hour")}


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _digest(prev: str, entry: dict) -> str:
    return hashlib.sha256((prev + json.dumps(entry, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def append_chained(path: pathlib.Path, entry: dict) -> str:
    """Append a JSON line carrying `prev` (hash of the previous line) and `hash`; returns the new hash."""
    with open(path.with_name("." + path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prev = ""
        if path.exists() and path.stat().st_size:
            with open(path, "rb") as f:
                f.seek(max(0, path.stat().st_size - 65536))
                last = f.read().splitlines()[-1]
            try:
                prev = json.loads(last).get("hash", "")
            except json.JSONDecodeError:
                prev = "corrupt"
        body = {**entry, "prev": prev}
        body["hash"] = _digest(prev, body)
        with open(path, "a") as f:
            f.write(json.dumps(body, default=str) + "\n")
        fcntl.flock(lock, fcntl.LOCK_UN)
        return body["hash"]


def write_json(path: pathlib.Path, obj, indent: int | None = 1) -> None:
    """Write whole, then rename: a reader in another process never sees a half-written file."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(obj, indent=indent) + "\n")
    tmp.replace(path)


def read_json(path: pathlib.Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def verify_chain(path: pathlib.Path) -> bool | str:
    prev = ""
    for i, e in enumerate(read_jsonl(path), 1):
        h = e.pop("hash", None)
        if e.get("prev") != prev or _digest(prev, e) != h:
            return f"broken at line {i}"
        prev = h
    return True


@functools.cache
def prices() -> dict:
    return tomllib.loads(PRICES.read_text()).get("models", {}) if PRICES.exists() else {}


def price_of(provider: str, model: str) -> tuple[dict, bool]:
    """(price entry, exact). Unknown model: the most expensive model of its provider, flagged as an estimate."""
    p = prices()
    if model in p:
        return p[model], True
    same = [v for v in p.values() if v.get("provider") == provider] or list(p.values())
    if not same:
        return {"input": 0.0, "output": 0.0}, False
    return max(same, key=lambda v: (v.get("output") or 0) + (v.get("input") or 0)), False


def time_factor(pr: dict, at: dt.datetime | None = None) -> float:
    """Off-peak discount of a provider whose price depends on the hour (DeepSeek): offpeak_x outside the peak_utc
    windows of weekdays."""
    if not pr.get("offpeak_x") or not pr.get("peak_utc"):
        return 1.0
    at = (at or now()).astimezone(dt.UTC)
    if at.weekday() < 5:
        hm = at.strftime("%H:%M")
        for w in pr["peak_utc"]:
            a, b = w.split("-")
            if a <= hm < b:
                return 1.0
    return float(pr["offpeak_x"])


def llm_cost(provider: str, model: str, usage: dict, price_x: float = 1.0, at: dt.datetime | None = None) -> tuple[float, bool]:
    """USD of one call. usage: input (all prompt tokens, cached included), cached, cache_write, output."""
    pr, exact = price_of(provider, model)
    price_x = price_x * time_factor(pr, at)
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
        with open(self.dir / ".budgets.lock", "w") as lock:  # read, merge, write: two callers never lose an update
            fcntl.flock(lock, fcntl.LOCK_EX)
            merged = {**self.caps, **{k: v for k, v in caps.items() if v is not None}}
            merged.setdefault("start", now().isoformat(timespec="seconds"))
            write_json(self.dir / "budgets.json", merged)

    # ------------------------------------------------------------ LLM
    # Entries use the OpenTelemetry GenAI attribute names (gen_ai.*) plus frontier.* ones, and form a hash chain:
    # each line carries the hash of the previous one, so an edited or deleted line is detected (verify_chain).
    def charge_llm(self, source: str, agent: str, provider: str, model: str, usage: dict, mode: str = "",
                   price_x: float = 1.0, usd: float | None = None, exact: bool = True) -> float:
        """Record a call; `usd` overrides the price computed from `usage`, `exact=False` marks it an estimate."""
        if usd is None:
            usd, exact = llm_cost(provider, model, usage, price_x)
        entry = {"t": now().isoformat(timespec="seconds"), "frontier.source": source, "gen_ai.agent.name": agent,
                 "gen_ai.provider.name": provider, "gen_ai.request.model": model, "frontier.mode": mode,
                 "gen_ai.usage.input_tokens": usage.get("input", 0) or 0,
                 "gen_ai.usage.output_tokens": usage.get("output", 0) or 0,
                 "gen_ai.usage.cache_read.input_tokens": usage.get("cached", 0) or 0,
                 "gen_ai.usage.cache_write.input_tokens": usage.get("cache_write", 0) or 0,
                 "gen_ai.usage.reasoning.output_tokens": usage.get("reasoning", 0) or 0,
                 "frontier.cost.usd": usd, "frontier.price_exact": exact}
        append_chained(self.dir / "llm.jsonl", entry)
        return usd

    def llm_entries(self) -> list[dict]:
        return read_jsonl(self.dir / "llm.jsonl")

    def llm_spent(self) -> float:
        return round(sum(e["frontier.cost.usd"] for e in self.llm_entries()), 6)

    # Reservations: the worst case of every call in flight, held until the call is charged, so that parallel
    # calls (sub-agents, harness traffic through the gateway, several processes) cannot overrun the cap together.
    def _reservations(self, fn) -> object:
        f = self.dir / "reservations.json"
        with open(self.dir / ".reservations.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:  # a process killed while writing the old way left an empty file: it holds no reservation
                items = json.loads(f.read_text() or "{}") if f.exists() else {}
            except json.JSONDecodeError:
                items = {}
            at = now()
            items = {k: v for k, v in items.items() if (at - parse_time(v["t"])).total_seconds() < RESERVATION_TTL_S}
            out = fn(items)
            write_json(f, items, indent=None)
            fcntl.flock(lock, fcntl.LOCK_UN)
            return out

    def reserved(self) -> float:
        return round(self._reservations(lambda items: sum(v["usd"] for v in items.values())), 6)

    def reserve(self, usd: float, what: str = "") -> tuple[str | None, str]:
        """Hold `usd` for a call about to be made: (reservation id, "") or (None, why it does not fit)."""
        def take(items):
            ok, why = self._fits(sum(v["usd"] for v in items.values()) + usd)
            if not ok:
                return None, why
            rid = uuid.uuid4().hex[:12]
            items[rid] = {"usd": usd, "t": now().isoformat(timespec="seconds"), "what": what}
            return rid, ""
        return self._reservations(take)

    def release(self, rid: str | None) -> None:
        if rid:
            self._reservations(lambda items: items.pop(rid, None))

    def _fits(self, extra: float) -> tuple[bool, str]:
        caps = self.caps
        if caps.get("frozen"):  # after delivery: a small grace lets the harness finish its turn, spend still recorded
            after = self.llm_spent() - caps.get("frozen_llm_usd", 0)
            if after + extra > POST_DELIVERY_GRACE_USD:
                return False, "the mission is delivered: spend is frozen"
            return True, ""
        cap = caps.get("llm_usd")
        if cap is None:
            return True, ""
        spent = self.llm_spent()
        if spent >= cap or spent + extra > cap:
            return False, (f"LLM budget: spent {spent:.4f} + calls in flight up to {extra:.4f} > cap {cap:.2f} USD "
                           "(a cheaper model, a lower reasoning level or fewer output tokens may still fit)")
        return True, ""

    def llm_allowed(self, next_call_usd: float = 0.0) -> tuple[bool, str]:
        """Hard cap: refused when what is spent, plus the calls in flight, plus `next_call_usd` exceeds the budget."""
        return self._fits(self.reserved() + next_call_usd)

    def verify(self) -> dict:
        """Integrity of the chained logs: {file: True | 'broken at line n'}."""
        return {name: verify_chain(self.dir / name) for name in ("llm.jsonl", "events.jsonl") if (self.dir / name).exists()}

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

    @contextlib.contextmanager
    def cloud_lock(self):
        """Held from a creation's budget check until the resource is in the ledger, across threads and processes,
        so that two parallel creations cannot both pass the same check."""
        with open(self.dir / ".cloud.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

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
            k = f"{e['gen_ai.provider.name']}/{e['gen_ai.request.model']}" + (f"+{e['frontier.mode']}" if e.get("frontier.mode") else "")
            m = by_model.setdefault(k, {"calls": 0, "input": 0, "output": 0, "usd": 0.0})
            m["calls"] += 1
            m["input"] += e["gen_ai.usage.input_tokens"]
            m["output"] += e["gen_ai.usage.output_tokens"]
            m["usd"] = round(m["usd"] + e["frontier.cost.usd"], 6)
        by_source: dict[str, float] = {}
        for e in llm:
            by_source[e["frontier.source"]] = round(by_source.get(e["frontier.source"], 0) + e["frontier.cost.usd"], 6)
        out = {"time": at.isoformat(timespec="seconds"),
               "llm": {"spent_usd": self.llm_spent(), "in_flight_usd": self.reserved(), "cap_usd": caps.get("llm_usd"),
                       "by_model": by_model, "by_source": by_source,
                       "estimated": any(not e.get("frontier.price_exact", True) for e in llm),
                       "unmetered_sessions": [{"n": e.get("n"), "note": e.get("note", "")}
                                              for e in read_jsonl(self.dir / "events.jsonl")
                                              if e.get("event") == "session_unmetered"]},
               "cloud": {}, "ledgers_intact": self.verify(), "llm_last_hash": llm[-1].get("hash", "") if llm else ""}
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
        if caps.get("frozen"):  # the harness finishing its turn after deliver, within the grace
            out["llm"]["after_delivery_usd"] = round(out["llm"]["spent_usd"] - caps.get("frozen_llm_usd", 0), 6)
        if out["llm"]["cap_usd"] is not None:
            out["llm"]["left_usd"] = round(out["llm"]["cap_usd"] - out["llm"]["spent_usd"], 4)
        return out

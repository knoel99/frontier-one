"""Every order of the steps of parallel model calls, run on the real Budget.

The promise: model calls made in parallel (sub-agents, the harness through the gateway, several processes) never
spend more than the LLM cap together. The harm when it breaks: a mission overruns its budget, unseen until the bill.
A test with two threads shows one order; this runs them all.

How: each call runs the framework's own code (`with budget.call(...)`) in a thread that stops before each operation
that takes a file lock: `reserve`, `charge_llm`, `release`. Each of the three is one indivisible step, as in the
code: it holds its lock from its first read to its last write. A scheduler lets one thread take one step at a time
and tries every choice. After every step:
- the spend is within the cap;
- the spend plus the worst case of every call that holds a reservation and is not charged yet is within the cap
  (the reason the first holds: a call is counted at its worst case until its charge is written).
At the end no reservation is left and every call that ran has its line in the ledger.

Checked: 2 and 3 parallel calls, each charged, void or left unsettled, before and after delivery (the grace).
A failure prints the order of steps that breaks the promise. `test_a_broken_order_is_found` proves the check can
fail: it runs the two mistakes this code has had or was written against.

Assumed, not checked:
- a call costs at most its worst case. It can cost more when the gateway caps the output it reserves
  (`limits.max_reserved_output_tokens`) or when a text holds more than a token per four characters: the overrun
  is then that excess, see `test_the_cap_holds_only_while_a_call_costs_at_most_its_worst_case`;
- a call ends before its reservation expires (`RESERVATION_TTL_S`, two hours; the gateway waits one hour at most);
- spend written with `charge_llm` alone (a harness that cannot be routed reports its usage at the end of a session)
  is outside the reservations by design;
- a step inside one lock is not split further. `reserve` reads the ledger inside its lock; a charge that lands
  during that read counts as before or after it, and both are among the orders run, because the charged call
  still holds its reservation.
"""

from __future__ import annotations

import itertools
import tempfile
import threading

import pytest

from frontier import budget

STEPS = ("reserve", "charge_llm", "release")
WORDS = {"reserve": "asks to reserve {worst}", "charge_llm": "is charged", "release": "releases its reservation"}


class Unsettled(Exception):
    """A path that neither charges nor voids the call."""


class Run:
    """One run of the calls under a schedule: `prefix` gives the first choices, the lowest waiting call the rest."""

    def __init__(self, calls: list[tuple[float, str, float]], caps: dict, prefix: list[int], spent_before: float = 0.0):
        self.calls, self.prefix = calls, prefix
        self.dir = tempfile.TemporaryDirectory()
        self.b = budget.Budget(self.dir.name)
        if spent_before:
            self.b.charge_llm("t", "earlier", "openai", "gpt-6-luna", {}, usd=spent_before)
        self.b.set_caps(**caps)
        self.turn = threading.Condition()
        self.waiting: dict[int, str] = {}  # call -> the step it is about to take
        self.go: int | None = None
        self.done: set[int] = set()
        self.trace: list[int] = []
        self.choices: list[list[int]] = []
        self.path: list[str] = []

    def arrive(self, me: int, step: str) -> None:
        """A call stops here before a step that takes a lock, until the scheduler picks it."""
        with self.turn:
            self.waiting[me] = step
            self.turn.notify_all()
            self.turn.wait_for(lambda: self.go == me)
            self.go = None
            del self.waiting[me]

    def _work(self, i: int) -> None:
        worst, outcome, actual = self.calls[i]
        try:
            call, _ = self.b.call(worst, "t", f"call-{i}", "openai", "gpt-6-luna")
            if call is not None:
                with call:
                    if outcome == "charge":
                        call.charge({}, usd=actual)
                    elif outcome == "void":
                        call.void()
                    else:
                        raise Unsettled
        except Unsettled:
            pass
        finally:
            with self.turn:
                self.done.add(i)
                self.turn.notify_all()

    def run(self) -> str:
        """Run to the end; returns "" or the first broken rule."""
        threads = [threading.Thread(target=self._work, args=(i,), daemon=True) for i in range(len(self.calls))]
        for i, t in enumerate(threads):
            t.schedule, t.call = self, i
            t.start()
        broken = ""
        while True:
            with self.turn:  # every call waits before a step, or has ended: the last step is fully done
                self.turn.wait_for(lambda: len(self.waiting) + len(self.done) == len(threads))
                ready = sorted(self.waiting)
            broken = broken or self._after(self._broken())
            if not ready:
                break
            n = len(self.trace)
            pick = self.prefix[n] if n < len(self.prefix) else ready[0]
            self.trace.append(pick)
            self.choices.append(ready)
            self.path.append(f"call-{pick} " + WORDS[self.waiting[pick]].format(worst=self.calls[pick][0]))
            with self.turn:
                self.go = pick
                self.turn.notify_all()
                self.turn.wait_for(lambda: self.go is None)
        broken = broken or self._after(self._broken_at_end())
        self.dir.cleanup()
        return broken

    def _after(self, broken: str) -> str:
        """The broken rule with the steps that led to it, in the code's words."""
        return broken and f"{broken}\n  after: " + "\n         ".join(self.path)

    def _limit(self) -> tuple[float, float]:
        """(the cap that applies, what counts against it): the LLM cap, or after delivery the grace."""
        caps, spent = self.b.caps, self.b.llm_spent()
        if caps.get("frozen"):
            return budget.POST_DELIVERY_GRACE_USD, spent - caps.get("frozen_llm_usd", 0)
        return caps["llm_usd"], spent

    def _broken(self) -> str:
        cap, spent = self._limit()
        charged = {e["gen_ai.agent.name"] for e in self.b.llm_entries()}
        held = budget.read_json(self.b.dir / "reservations.json", {})
        pending = sum(v["usd"] for v in held.values() if v["what"].split()[0] not in charged)
        if spent > cap:
            return f"spent {spent} > cap {cap}"
        if spent + pending > cap:
            return f"spent {spent} + calls in flight {pending} > cap {cap}"
        return ""

    def _broken_at_end(self) -> str:
        if self.b.reserved():
            return f"a reservation of {self.b.reserved()} is left"
        charged = [e["gen_ai.agent.name"] for e in self.b.llm_entries() if e["gen_ai.agent.name"] != "earlier"]
        if len(charged) != len(set(charged)):
            return f"a call is charged twice: {charged}"
        return ""


def _gated(step: str, real):
    def op(b, *args, **kwargs):
        t = threading.current_thread()
        if hasattr(t, "schedule"):  # a call's thread; the scheduler reads the state without waiting
            t.schedule.arrive(t.call, step)
        return real(b, *args, **kwargs)
    return op


def explore(calls, caps, spent_before: float = 0.0) -> tuple[int, str]:
    """Run every order of the calls' steps: (orders run, "" or the first order that breaks a rule)."""
    todo, runs = [[]], 0
    with pytest.MonkeyPatch.context() as mp:
        for step in STEPS:
            mp.setattr(budget.Budget, step, _gated(step, getattr(budget.Budget, step)))
        while todo:
            r = Run(calls, caps, todo.pop(), spent_before)
            broken = r.run()
            runs += 1
            if broken:
                return runs, broken
            for i in range(len(r.prefix), len(r.trace)):  # the other choices at each step this run chose by default
                todo += [r.trace[:i] + [other] for other in r.choices[i] if other > r.trace[i]]
    return runs, ""


OUTCOMES = ("charge", "void", "unsettled")


def test_two_parallel_calls_stay_within_the_cap_in_every_order():
    for a, b in itertools.product(OUTCOMES, repeat=2):  # each call costs its whole worst case: no slack
        runs, broken = explore([(0.625, a, 0.625), (0.625, b, 0.625)], {"llm_usd": 1.0})
        assert not broken, broken
        assert runs > 1


def test_three_parallel_calls_stay_within_the_cap_in_every_order():
    calls = [(0.375, "charge", 0.25), (0.375, "unsettled", 0.0), (0.375, "charge", 0.375)]
    runs, broken = explore(calls, {"llm_usd": 1.0})
    assert not broken, broken
    assert runs > 100


def test_parallel_calls_after_delivery_stay_within_the_grace_in_every_order():
    grace = budget.POST_DELIVERY_GRACE_USD
    for a, b in itertools.product(OUTCOMES, repeat=2):
        runs, broken = explore([(grace * 0.75, a, grace * 0.75), (grace * 0.75, b, grace * 0.5)],
                               {"llm_usd": 5.0, "frozen": True, "frozen_llm_usd": 1.0}, spent_before=1.0)
        assert not broken, broken


def test_a_broken_order_is_found(monkeypatch):
    """The check can fail: the two mistakes this code has had, or was written against, each break the promise in an
    order the scheduler finds."""
    calls, caps = [(0.625, "charge", 0.625), (0.625, "charge", 0.625)], {"llm_usd": 1.0}
    real_charge = budget.Call.charge
    # 1. the reservation released before the charge is written (agent.py before e6eead1)
    monkeypatch.setattr(budget.Call, "charge", lambda c, *a, **k: (c.budget.release(c.rid), real_charge(c, *a, **k))[1])
    _, broken = explore(calls, caps)
    assert "calls in flight" in broken and "call-0 releases its reservation" in broken, broken
    monkeypatch.setattr(budget.Call, "charge", real_charge)
    # 2. a check without a reservation: each call sees room, none holds it
    monkeypatch.setattr(budget.Budget, "reserve", lambda b, usd, what="": ("r", "") if b._fits(usd)[0] else (None, "over"))
    monkeypatch.setattr(budget.Budget, "release", lambda b, rid: None)
    _, broken = explore(calls, caps)
    assert "spent 1.25 > cap 1.0" in broken, broken


def test_the_cap_holds_only_while_a_call_costs_at_most_its_worst_case():
    """The assumption the promise rests on, stated as a run: a call that costs more than it reserved overruns the cap
    by the excess. The gateway reserves at most `limits.max_reserved_output_tokens` of output for a call."""
    _, broken = explore([(0.625, "charge", 0.75), (0.375, "charge", 0.375)], {"llm_usd": 1.0})
    assert "spent 0.75 + calls in flight 0.375 > cap 1.0" in broken and broken.endswith("call-0 is charged"), broken

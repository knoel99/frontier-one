"""The scores of a race, out of 100, general enough for any scientific discipline (settled on 2026-11-15).

    final = 0.50 x scientific quality + 0.25 x artifacts + 0.25 x efficiency - penalties

- Scientific quality: four rubrics graded by the jury on an absolute scale (soundness 20, significance and originality
  15, positioning in the literature 5, clarity and limits 10), times the adequacy to the subject (1 on the subject,
  0.5 partly or bent, 0 off it).
- Artifacts, code and data: four rubrics graded by the jury, after the ACM artifact badges and the FAIR principles
  (reproducibility 10, code 7, data and models 5, traceability 3).
- Efficiency, measured: half absolute, half relative. Absolute: the total spend (model calls + simulations, rented and
  local) and the time, each on a logarithmic scale where every doubling above 5 USD or 5 minutes costs 10 points.
  Relative: against the competitors whose science is of the same order (within a factor 2), the number of doublings
  of advance, 50 at par, 100 for 4x cheaper or faster, 0 for 4x dearer or slower. It counts in full once the science
  reaches 10/100 (a work of less is scaled down), and is 0 for a team that is not classified.

The absolute scales are logarithmic on purpose: each level up is several times harder to reach than the one below.
Rules taken from motor racing: time lost to an incident outside the team's control is neutralised (red flag); a team
that did not call deliver is classified if its repository holds 90 % of the deliverables; stewards take points off for
infractions found in the ledgers, and disqualify for a broken ledger.
"""

from __future__ import annotations

import math
import statistics

WEIGHTS = {"scientific_quality": 50.0, "artifacts": 25.0, "efficiency": 25.0}
SCIENCE_RUBRICS = {"soundness": 20.0, "significance": 15.0, "positioning": 5.0, "clarity": 10.0}
ARTIFACT_RUBRICS = {"reproducibility": 10.0, "code": 7.0, "data": 5.0, "traceability": 3.0}
ADEQUACY = {"on_subject": 1.0, "partial": 0.5, "off_subject": 0.0}
LOG_BASE_USD = 5.0  # the first spend milestone: no point lost below it
LOG_BASE_MINUTES = 5.0  # the first time milestone
POINTS_PER_DOUBLING = 10.0
PEER_BAND = 2.0  # competitors whose science is within this factor are compared on cost and time
GUARD_QUALITY = 10.0  # efficiency counts in full from this scientific quality on
CLASSIFIED_SHARE = 0.9  # share of the deliverables that classifies a team that did not deliver


def clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def log_score(x: float, base: float) -> float:
    """100 up to `base`, then POINTS_PER_DOUBLING less for every doubling."""
    if x <= base:
        return 100.0
    return clip(100.0 - POINTS_PER_DOUBLING * math.log2(x / base))


def weighted(rubrics: dict, weights: dict) -> float | None:
    vals = {k: rubrics.get(k) for k in weights}
    if any(v is None for v in vals.values()):
        return None
    return sum(weights[k] * clip(float(vals[k])) for k in weights) / sum(weights.values())


def science(rubrics: dict, adequacy: str) -> float | None:
    s = weighted(rubrics, SCIENCE_RUBRICS)
    return None if s is None else s * ADEQUACY.get(adequacy, 1.0)


def artifacts(rubrics: dict) -> float | None:
    return weighted(rubrics, ARTIFACT_RUBRICS)


def relative(value: float, peers: list[float]) -> float:
    """Doublings of advance on the median peer: 50 at par, 25 points per doubling (nothing spent is the best case)."""
    if not peers:
        return 50.0
    median = statistics.median(peers)
    if value <= 0 or median <= 0:  # log2 of 0: at par when both are 0, else the extreme
        return 50.0 if value <= 0 and median <= 0 else 100.0 if value <= 0 else 0.0
    return clip(50.0 + 25.0 * math.log2(median / value))


def efficiency(teams: dict[str, dict]) -> dict[str, dict]:
    """teams: name -> {"quality", "usd", "minutes", "classified"}. Returns name -> the efficiency and its parts."""
    out = {}
    for name, t in teams.items():
        if not t["classified"] or t["quality"] is None:
            out[name] = {"absolute": 0.0, "relative": 0.0, "guard": 0.0, "efficiency": 0.0, "peers": []}
            continue
        absolute = (log_score(t["usd"], LOG_BASE_USD) + log_score(t["minutes"], LOG_BASE_MINUTES)) / 2
        peers = [n for n, o in teams.items() if n != name and o["classified"] and o["quality"]
                 and t["quality"] and max(o["quality"], t["quality"]) <= PEER_BAND * min(o["quality"], t["quality"])]
        rel = (relative(t["usd"], [teams[p]["usd"] for p in peers])
               + relative(t["minutes"], [teams[p]["minutes"] for p in peers])) / 2
        guard = min(1.0, (t["quality"] or 0) / GUARD_QUALITY)
        out[name] = {"absolute": round(absolute, 1), "relative": round(rel, 1), "guard": round(guard, 2),
                     "efficiency": round((absolute + rel) / 2 * guard, 1), "peers": peers}
    return out


def final(quality: float | None, arts: float | None, eff: float, penalty: float = 0.0,
          disqualified: bool = False) -> float | None:
    if disqualified:
        return 0.0
    if quality is None or arts is None:
        return None
    total = (WEIGHTS["scientific_quality"] * quality + WEIGHTS["artifacts"] * arts
             + WEIGHTS["efficiency"] * eff) / sum(WEIGHTS.values())
    return round(max(0.0, total - penalty), 1)

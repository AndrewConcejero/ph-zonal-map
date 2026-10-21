"""Staleness adjustment: project every barangay's latest BIR value to today using its RDO's own growth.

Growth is measured on like-for-like pairs only: the same street (and vicinity) in the same barangay, in two
consecutive revisions of that barangay's schedule. Each pair gives an annualised rate; an RDO's rate is the
median over its pairs. Department Order averages are never compared, because one DO often covers only part
of a city and a new DO adds streets that pull its average around.

Which pairs count: those from the transition into each barangay's current schedule. If an RDO has fewer
than MIN_PAIRS of them, earlier transitions are added, newest first. If it still has too few, the RDO gets
its province's median rate and is flagged. Rates that look wrong are flagged, never smoothed.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from .bir_values import DoValue
from .normalize import ascii_fold

MIN_PAIRS = 20
MIN_YEARS = 0.5  # revisions closer than this are corrections, not growth
_ABBREV = [(r'\bSTREET\b', 'ST'), (r'\bAVENUE\b', 'AVE'), (r'\bROAD\b', 'RD'), (r'\bEXTENSION\b', 'EXT'),
           (r'\bBOULEVARD\b', 'BLVD'), (r'\bHIGHWAY\b', 'HWY'), (r'\bSUBDIVISION\b', 'SUBD'), (r'\bVILLAGE\b', 'VILL'),
           (r'\bDRIVE\b', 'DR'), (r'\bSAINT\b|\bSTO\b|\bSTA\b', 'ST'), (r'\bGENERAL\b', 'GEN')]


def norm_street(s: str) -> str:
    t = re.sub(r'[^A-Z0-9 ]+', ' ', ascii_fold(s).upper())
    for pat, rep in _ABBREV:
        t = re.sub(pat, rep, t)
    return re.sub(r'\s+', ' ', t).strip()


def years_between(a: str, b: str) -> float:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 365.25


@dataclass
class Pair:
    rdo: str           # RDO of the newer schedule
    city: str
    psgc: str
    street: str
    do_prev: str
    do_next: str
    eff_prev: str
    eff_next: str
    v_prev: float
    v_next: float
    step: int          # 0 = transition into the barangay's current schedule, 1 = the one before, ...

    @property
    def years(self) -> float:
        return years_between(self.eff_prev, self.eff_next)

    @property
    def annual(self) -> float:
        return (self.v_next / self.v_prev) ** (1 / self.years) - 1


def _keyed(v: DoValue) -> dict[tuple[str, str], float]:
    return {(norm_street(s), norm_street(vic)): val for (s, vic), val in v.streets.items()}


def carried_over(a: DoValue, b: DoValue) -> bool:
    """True when schedule b copied a's values instead of revising them (Taguig's 2024 DO repeats Makati's 2021
    values for the EMBO barangays): at least 90% of the streets both list kept the same value."""
    pa, pb = _keyed(a), _keyed(b)
    common = [k for k in pb if k in pa and k[0]]
    if len(common) < max(3, len(pb) // 2):
        return False
    return sum(pa[k] == pb[k] for k in common) >= 0.9 * len(common)


def value_since(history: list[DoValue]) -> DoValue:
    """The schedule the barangay's current values were actually set in, skipping carry-overs."""
    hist = [v for v in history if v.effective_date]
    i = len(hist) - 1
    while i > 0 and carried_over(hist[i - 1], hist[i]):
        i -= 1
    return hist[i] if hist else history[-1]


def _listed_once(keys) -> set[str]:
    return {st for st, n in Counter(k[0] for k in keys).items() if n == 1 and st}


def matched_pairs(bir_vals: dict[str, list[DoValue]], city_of: dict[str, str]) -> list[Pair]:
    """Like-for-like street pairs across each barangay's consecutive revisions. A BIR block applied to several
    split barangays yields identical pairs; those are counted once. Carry-overs are not growth and are skipped."""
    seen, out = set(), []
    for psgc, history in bir_vals.items():
        hist = [v for v in history if v.effective_date]
        transitions = [(a, b) for a, b in zip(hist, hist[1:])
                       if years_between(a.effective_date, b.effective_date) >= MIN_YEARS and not carried_over(a, b)]
        for step, (a, b) in enumerate(reversed(transitions)):
            prev, nxt = _keyed(a), _keyed(b)
            # vicinity text is often reworded between revisions: a street listed once in both pairs on its name
            unique_prev = {k[0]: k for k in prev if k[0] in _listed_once(prev)}
            unique_next = _listed_once(nxt)
            for key, val in nxt.items():
                if key not in prev and key[0] in unique_next:
                    key = unique_prev.get(key[0], key)
                if key in prev and key[0]:
                    dedupe = (city_of.get(psgc), key, a.do_number, b.do_number, prev[key], val)
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    out.append(Pair(b.rdo, city_of.get(psgc, ''), psgc, ' / '.join(x for x in key if x),
                                    a.do_number, b.do_number, a.effective_date, b.effective_date, prev[key], val, step))
    return out


@dataclass
class RdoRate:
    rdo: str
    rate: float | None
    pairs: int
    steps_used: int        # 1 = only the latest transition
    transitions: str       # "DO 62-2016 -> DO 38-2021 (5.0 yrs)"; several joined by '; '
    share_up: float        # share of pairs that rose
    source: str            # 'rdo' or 'province median'
    flags: str


def rdo_rates(pairs: list[Pair], province_of_rdo: dict[str, str]) -> dict[str, RdoRate]:
    by_rdo = defaultdict(list)
    for p in pairs:
        by_rdo[p.rdo].append(p)
    rates: dict[str, RdoRate] = {}
    for rdo, ps in by_rdo.items():
        steps = sorted({p.step for p in ps})
        used, n_steps = [], 0
        for s in steps:
            used += [p for p in ps if p.step == s]
            n_steps += 1
            if len(used) >= MIN_PAIRS:
                break
        trans = defaultdict(list)
        for p in used:
            trans[(p.do_prev, p.do_next)].append(p.years)
        tdesc = '; '.join(f'{a} -> {b} ({statistics.median(y):.1f} yrs, {len(y)} pairs)'
                          for (a, b), y in sorted(trans.items(), key=lambda kv: -len(kv[1]))[:4])
        if len(used) >= MIN_PAIRS:
            ann = [p.annual for p in used]
            rates[rdo] = RdoRate(rdo, statistics.median(ann), len(used), n_steps, tdesc,
                                 sum(p.v_next > p.v_prev for p in used) / len(used), 'rdo', '')
        else:
            rates[rdo] = RdoRate(rdo, None, len(used), n_steps, tdesc, 0, 'rdo', f'only {len(used)} matched pairs')
    # fallback: the median rate of the RDO's province
    prov_rates = defaultdict(list)
    for rdo, r in rates.items():
        if r.rate is not None:
            prov_rates[province_of_rdo.get(rdo, '')].append(r.rate)
    for rdo in set(province_of_rdo) | set(rates):
        r = rates.get(rdo) or RdoRate(rdo, None, 0, 0, '', 0, 'rdo', 'no matched pairs')
        if r.rate is None and prov_rates.get(province_of_rdo.get(rdo, '')):
            r.rate = statistics.median(prov_rates[province_of_rdo[rdo]])
            r.source = 'province median'
        flags = [r.flags] if r.flags else []
        if r.rate is not None and r.rate < 0:
            flags.append('negative growth')
        if r.rate is not None and r.rate > 0.15:
            flags.append('above 15% a year')
        if r.source == 'rdo' and r.rate is not None and r.steps_used > 1:
            flags.append(f'latest transition had too few pairs; used {r.steps_used} transitions')
        r.flags = '; '.join(flags)
        rates[rdo] = r
    return rates


def project(value: float, eff: str, rate: float, today: date) -> float:
    return value * (1 + rate) ** max(0.0, years_between(eff, today.isoformat()))

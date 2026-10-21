"""Parse every in-scope BIR Department Order listed in the inventory into tidy rows.

Inputs:  raw/bir/inventory.csv, pipeline/review/bir_sheets.csv (from `python -m pipeline.inventory`)
Outputs: cache/bir_rows.csv.gz            all parsed value rows (regenerated; not committed)
         pipeline/review/parse_errors.csv.gz   every row the parser could not use, with the reason
         pipeline/review/parse_summary.csv     rows parsed and problems by RDO and DO (committed, small)

Only schedules effective from MIN_YEAR on are parsed: older sheets use typewriter-era layouts and are
not needed for current values or for growth between recent revisions.
"""
from __future__ import annotations

import csv
import gzip
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from .bir_parser import parse_workbook
from .inventory import BIR, Gazetteer
from .normalize import ascii_fold

ROOT = Path(__file__).resolve().parent.parent
REVIEW = Path(__file__).resolve().parent / 'review'
CACHE = ROOT / 'cache'
ROWS_OUT = CACHE / 'bir_rows.csv.gz'
MIN_YEAR = '2000'

ROW_FIELDS = ['rdo', 'do_number', 'effective_date', 'city', 'barangay', 'street_or_building', 'class_code',
              'value_per_sqm', 'vicinity', 'province', 'city_raw', 'file', 'sheet', 'row']


def resolve_city(raw: str, covered: list[str], candidates: list[str], gaz: Gazetteer) -> str | None:
    """Map a block's city text to a city. `covered` is what the RDO covers today; `candidates` adds the rest of
    its province, because DOs from before an RDO split (25 -> 25A/25B, 54 -> 54A/54B) cover the old district."""
    if not raw:
        return covered[0] if len(covered) == 1 else None
    # compare keys against this RDO's own candidates first: names like Santa Maria exist in two provinces
    k = gaz.key(raw)
    same = [c for c in candidates if gaz.key(c) == k]
    if len(same) == 1:
        return same[0]
    hit = gaz.lookup(raw)
    if hit and hit[0] in candidates:
        return hit[0]
    t = ' ' + re.sub(r'[^a-z0-9]+', ' ', ascii_fold(raw).lower()) + ' '
    for pool in (covered, candidates):
        found = [c for c in pool if f" {re.sub(r'[^a-z0-9]+', ' ', ascii_fold(c).lower()).strip()} " in t]
        if len(found) == 1:
            return found[0]
    return covered[0] if len(covered) == 1 else None


def write_gz_csv(path: Path, fields: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def read_rows(path: Path = ROWS_OUT) -> list[dict]:
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def main() -> None:
    gaz = Gazetteer()
    inv = {(r['rdo'], r['department_order']): r for r in csv.DictReader(open(BIR / 'inventory.csv', encoding='utf-8'))}
    sheets = list(csv.DictReader(open(REVIEW / 'bir_sheets.csv', encoding='utf-8')))

    by_file = defaultdict(list)
    for s in sheets:
        i = inv[(s['rdo'], s['department_order'])]
        if i['in_scope'] != 'yes' or (i['effective_date'] or '0000') < MIN_YEAR:
            continue
        by_file[(s['rdo'], s['file_name'])].append((s['sheet'], s['department_order'], i['effective_date']))

    out_rows, problems, summary = [], [], []
    unresolved = Counter()
    for (rdo, fn), sh in sorted(by_file.items()):
        covered = sorted({c for (r, _), v in inv.items() if r == rdo and v['in_scope'] == 'yes'
                          for c in v['cities_covered'].split('; ') if c})
        votes = Counter(p for c in covered for n, p in gaz.entries if n == c)
        province = votes.most_common(1)[0][0]  # Santa Maria votes for two provinces; the majority wins
        candidates = sorted({n for n, p in gaz.entries if p == province})
        results = parse_workbook(BIR / rdo / fn, sh, rdo, covered[0] if len(covered) == 1 else '')
        for sheet, res in results.items():
            do = next(d for s, d, _ in sh if s == sheet)
            kept = 0
            for r in res.rows:
                city = resolve_city(r.city, covered, candidates, gaz)
                if city is None and not r.city:
                    city = ''  # no city label in this block: build.py resolves it from the barangay name
                if city is None:
                    unresolved[(rdo, r.city)] += 1
                    problems.append({'rdo': rdo, 'do_number': do, 'file': fn, 'sheet': sheet, 'row': r.row,
                                     'reason': f'city {r.city!r} is not one this RDO covers ({"; ".join(covered)})',
                                     'cells': f'{r.barangay} | {r.street_or_building} | {r.class_code} | {r.value_per_sqm:g}'})
                    continue
                d = asdict(r)
                provs = {p for n, p in gaz.entries if n == city}
                d.update(city=city, city_raw=r.city, file=fn,
                         province=next(iter(provs)) if len(provs) == 1 else province)
                out_rows.append({k: d[k] for k in ROW_FIELDS})
                kept += 1
            for p in res.problems:
                problems.append({'rdo': rdo, 'do_number': do, 'file': fn, 'sheet': sheet, 'row': p.row,
                                 'reason': p.reason, 'cells': p.cells})
            reasons = Counter(re.sub(r"[:(].*", '', p.reason).strip() for p in res.problems)
            summary.append({'rdo': rdo, 'do_number': do, 'sheet': sheet, 'rows_parsed': kept,
                            'barangay_blocks': res.blocks,
                            'value_not_numeric': sum(n for k, n in reasons.items() if 'not numeric' in k),
                            'unrecognised_class': sum(n for k, n in reasons.items() if 'unrecognised class' in k),
                            'text_rows': sum(n for k, n in reasons.items() if k.startswith('text only')),
                            'other': sum(n for k, n in reasons.items()
                                         if 'not numeric' not in k and 'unrecognised class' not in k and not k.startswith('text only'))})

    write_gz_csv(ROWS_OUT, ROW_FIELDS, out_rows)
    write_gz_csv(REVIEW / 'parse_errors.csv.gz', ['rdo', 'do_number', 'file', 'sheet', 'row', 'reason', 'cells'], problems)
    with open(REVIEW / 'parse_summary.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    print(f'parsed {len(out_rows):,} rows from {len(summary)} sheets; logged {len(problems):,} unparsed rows')
    for (rdo, c), n in unresolved.most_common(10):
        print(f'  unresolved city {c!r} in {rdo}: {n} rows')


if __name__ == '__main__':
    main()

from datetime import date

import pytest

from pipeline.bir_values import DoValue
from pipeline.gapfill import fill
from pipeline.staleness import carried_over, matched_pairs, norm_street, project, rdo_rates, value_since


def dv(do, eff, streets, rdo='RDO-001', psgc='1'):
    vals = list(streets.values())
    return DoValue(psgc, rdo, do, eff, min(vals), max(vals), sorted(vals)[len(vals) // 2], len(vals),
                   {(s, v): x for (s, v), x in streets.items()})


def test_norm_street():
    assert norm_street('Ayala Avenue') == norm_street('AYALA AVE.')
    assert norm_street('Sto. Niño Street') == norm_street('SAINT NINO ST')


def test_pairs_are_like_for_like_and_annualised():
    a = dv('DO 1-2015', '2015-01-01', {('RIZAL ST', ''): 10000, ('MABINI ST', 'A - B'): 20000})
    b = dv('DO 2-2020', '2020-01-01', {('RIZAL STREET', ''): 20000, ('MABINI ST', 'A TO B'): 40000,
                                        ('NEW SUBD', ''): 99999})  # new street: no pair
    pairs = matched_pairs({'1': [a, b]}, {'1': 'X'})
    assert len(pairs) == 2  # Mabini pairs on its name: listed once in both, vicinity reworded
    assert all(p.annual == pytest.approx(2 ** (1 / 5) - 1, rel=1e-3) for p in pairs)


def test_carry_over_is_not_growth_and_resets_base_date():
    streets = {(f'ST {i}', ''): 1000 * i for i in range(1, 6)}
    old = dv('DO 1-2016', '2016-06-01', {k: v / 2 for k, v in streets.items()})
    set_ = dv('DO 2-2021', '2021-12-22', streets)
    copy = dv('DO 3-2024', '2024-02-15', streets, rdo='RDO-002')
    assert carried_over(set_, copy)
    assert value_since([old, set_, copy]).do_number == 'DO 2-2021'
    pairs = matched_pairs({'1': [old, set_, copy]}, {'1': 'X'})
    assert {p.do_next for p in pairs} == {'DO 2-2021'}


def test_rates_need_enough_pairs_and_fall_back_to_province():
    many = {(f'ST {i}', ''): 1000 for i in range(25)}
    good = [dv('DO 1-2015', '2015-01-01', many, 'RDO-A', str(i)) for i in range(1)] + \
           [dv('DO 2-2020', '2020-01-01', {k: 1500 for k in many}, 'RDO-A', '0')]
    thin = [dv('DO 3-2015', '2015-01-01', {('ONE', ''): 1000}, 'RDO-B', '9'),
            dv('DO 4-2020', '2020-01-01', {('ONE', ''): 3000}, 'RDO-B', '9')]
    pairs = matched_pairs({'0': good, '9': thin}, {'0': 'X', '9': 'Y'})
    rates = rdo_rates(pairs, {'RDO-A': 'P', 'RDO-B': 'P'})
    assert rates['RDO-A'].source == 'rdo' and rates['RDO-A'].rate == pytest.approx(1.5 ** 0.2 - 1, rel=1e-3)
    assert rates['RDO-B'].source == 'province median' and rates['RDO-B'].rate == rates['RDO-A'].rate
    assert 'only 1 matched pairs' in rates['RDO-B'].flags


def test_project():
    assert project(100, '2020-01-01', 0.1, date(2022, 1, 1)) == pytest.approx(121, rel=1e-2)
    assert project(100, '2030-01-01', 0.1, date(2022, 1, 1)) == 100  # never shrinks a future-dated schedule


def square(i, j, props):
    ring = [[i, j], [i + 1, j], [i + 1, j + 1], [i, j + 1], [i, j]]
    return {'type': 'Feature', 'properties': props, 'geometry': {'type': 'Polygon', 'coordinates': [ring]}}


def test_gap_fill_uses_only_real_neighbours():
    feats = [
        square(0, 0, {'id': 'a', 'n': 'A', 'q': 'barangay_data', 'lo': 10, 'hi': 30, 'typ': 20, 'typ_a': 30}),
        square(1, 0, {'id': 'b', 'n': 'B', 'q': 'city_median', 'typ': 5}),
        square(2, 0, {'id': 'c', 'n': 'C', 'q': 'barangay_data', 'lo': 30, 'hi': 50, 'typ': 40, 'typ_a': 50}),
        square(5, 5, {'id': 'd', 'n': 'D', 'q': 'city_median', 'typ': 5}),   # no neighbours
        square(1, 1, {'id': 'e', 'n': 'E', 'q': 'no_data'}),                 # touches A, B, C only diagonally/edge
    ]
    assert fill(feats) == 2
    b = feats[1]['properties']
    assert b['q'] == 'estimated' and b['typ'] == 30 and b['typ_a'] == 40 and b['nb'] == 2
    assert feats[3]['properties']['q'] == 'city_median'
    e = feats[4]['properties']
    assert e['q'] == 'estimated' and e['nb'] == 2  # B is an estimate by now, but only real values count

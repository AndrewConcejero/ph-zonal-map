from pipeline.integrity import drop_fills
from pipeline.national import Gazetteer, resolve


def _gaz():
    rows = [  # (psgc, name, province code, level)
        (1380700000, 'City of Marikina', 1307400000, 'City'), (405802000, 'City of Antipolo', 405800000, 'City'),
        (405805000, 'Cainta', 405800000, 'Mun'), (405813000, 'Taytay', 405800000, 'Mun'),
        (1381300000, 'Quezon City', 1307400000, 'City'), (405600000, 'Quezon', 1031200000, 'Mun'),
        (1263000000, 'Maasim', 1208000000, 'Mun'), (1263100000, 'Kiamba', 1208000000, 'Mun'),
        (1263200000, 'Alabel', 1208000000, 'Mun'), (603000000, 'Maasin', 603000000, 'Mun'),
        (1600302000, 'Bunawan', 1600300000, 'Mun'),
    ]
    provs = {1307400000: 'NCR, Second District (Not a Province)', 405800000: 'Rizal', 1031200000: 'Bukidnon',
             1208000000: 'Sarangani', 603000000: 'Iloilo', 1600300000: 'Agusan del Sur'}
    munis = [{'properties': {'adm3_psgc': c, 'adm3_en': n, 'adm2_psgc': p, 'adm1_psgc': 0, 'geo_level': lvl}}
             for c, n, p, lvl in rows]
    return Gazetteer(munis, provs)


def test_unique_name_wins_over_province_vote():
    out, _ = resolve(['Antipolo', 'Marikina', 'Cainta', 'Taytay'], _gaz(), set())
    assert [e['name'] if e else None for _, e, _ in out] == ['City of Antipolo', 'City of Marikina', 'Cainta', 'Taytay']


def test_shared_name_takes_rdo_province():
    out, _ = resolve(['Quezon City'], _gaz(), {'Metro Manila'})
    assert out[0][1]['name'] == 'Quezon City'


def test_typo_matched_inside_rdo_provinces_only():
    # 'Maasin' exists (Iloilo) but the RDO is in Sarangani, where BIR means Maasim
    out, _ = resolve(['Maasin', 'Kiamba', 'Alabel'], _gaz(), {'Sarangani'})
    assert out[0][1]['name'] == 'Maasim'


def test_drop_fills_removes_estimates_and_city_medians():
    feats = [{'properties': {'q': 'estimated', 'typ': 1, 'lo': 1, 'nb': 3, 'nbn': 'x', 'c_q': 'barangay_data', 'c_typ': 5}},
             {'properties': {'q': 'city_median', 'typ': 2, 'eff': '2020-01-01', 'nores': 'DO 1-2020'}},
             {'properties': {'q': 'barangay_data', 'typ': 3, 'i_q': 'estimated', 'i_typ': 4}}]
    n = drop_fills(feats)
    a, b, c = (f['properties'] for f in feats)
    assert a == {'q': 'no_data', 'c_q': 'barangay_data', 'c_typ': 5}
    assert b == {'q': 'no_data', 'nores': 'DO 1-2020'}
    assert c == {'q': 'barangay_data', 'typ': 3, 'i_q': 'no_data'}
    assert sum(n.values()) == 3

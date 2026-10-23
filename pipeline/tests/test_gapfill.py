"""Gap filling: estimates come only from touching barangays with their own values, and only inside cities that
have values of their own."""
from pipeline.gapfill import fill, fill_prefixed


def square(x: float) -> dict:
    return {'type': 'Polygon', 'coordinates': [[(x, 0), (x + 1, 0), (x + 1, 1), (x, 1), (x, 0)]]}


def feat(i: str, x: float, city: str, prov: str, **p) -> dict:
    return {'type': 'Feature', 'geometry': square(x), 'properties': {'id': i, 'n': i, 'c': city, 'p': prov, **p}}


def test_no_estimates_across_into_a_city_without_values():
    feats = [feat('a', 0, 'Calumpit', 'Bulacan', q='barangay_data', lo=100, hi=300, typ=200, c_q='barangay_data', c_typ=500),
             feat('b', 1, 'Calumpit', 'Bulacan', q='no_data'),
             feat('c', 2, 'Apalit', 'Pampanga', q='no_data'),   # touches b only; and a, via b? never
             feat('d', -1, 'Apalit', 'Pampanga', q='no_data')]  # touches a, but Apalit has no schedule loaded
    assert fill(feats) == 1
    assert fill_prefixed(feats, 'c_') == 1
    by = {f['properties']['id']: f['properties'] for f in feats}
    assert by['b']['q'] == 'estimated' and by['b']['typ'] == 200 and by['b']['c_q'] == 'estimated'
    assert by['d']['q'] == 'no_data' and 'c_q' not in by['d']
    assert by['c']['q'] == 'no_data'

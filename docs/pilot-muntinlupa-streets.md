# Pilot: street- and village-level values in Muntinlupa

**Question:** can BIR's street and subdivision rows be placed on the map well enough to show values below the
barangay level?

**Method** (`pipeline/streets.py`, OSM via `pipeline/osm.py`): one OpenStreetMap request for Muntinlupa
(6 MB, cached): 2,628 named streets and 425 named residential areas. Each residential land (RR) row of a
barangay's current schedule is placed on an OSM shape inside or touching its own barangay, in this order:
named village or subdivision, then named street (clipped between the two cross streets BIR gives, where it
gives two), with "all other streets" left as the barangay background.

## Result

| | Rows | Average value |
|---|---:|---:|
| Placed on an OSM village/subdivision outline | 66 | ₱29,394 |
| Placed on a street (whole length in the barangay, drawn dashed) | 49 | ₱25,000 |
| Placed on a street stretch between two cross streets | 1 | ₱65,000 |
| "All other streets" (barangay background, not drawn) | 9 | ₱17,556 |
| Not placed | 87 | ₱28,195 |

**116 of 203 priced rows placed (57%).** New Alabang Village (Ayala Alabang) placed fully; Cupang 19 of 28;
Putatan, which is mostly socialised housing and compounds, 17 of 58.

Example: **Alabang 400 Village Subd, Cupang: ₱60,000/sqm (₱68,501 adjusted to 2026)**, on the OSM
outline "Alabang 400". The barangay's typical value is ₱32,500; neighbours show ₱65,000 (Alabang Hills
Village) and ₱18,000–25,000 (Rizal Village).

## Why the rest did not place

| Reason | Rows |
|---|---:|
| Subdivision or street not in OSM, or named too differently | 50 |
| Sitio, informal settlement or socialised housing (rarely mapped in OSM) | 15 |
| Taguig-Muntinlupa ("T.M.") Road, which OSM names differently | 10 |
| Lots along the PNR railway (BIR prices them as a strip) | 9 |
| Family compounds | 3 |

Every row and its best OSM candidate is in `pipeline/review/streets_muntinlupa.csv`.

## What it would take to roll out

1. **More rules, same approach**: a railway-strip shape for PNR rows (a band along the OSM railway), an alias
   list for roads OSM names differently (T.M. Road), and the same alias file pattern as barangays for
   subdivisions. Expect roughly 65–70% placed in NCR; provinces lower, as OSM has fewer named subdivisions.
2. **All cities**: one cached OSM request per city (about 120 requests, slowly), or the Geofabrik Philippines
   extract (a few hundred MB, one download).
3. **Packaging**: Muntinlupa is 101 KB; the whole area would be roughly 10–20 MB, too big for one GeoJSON file,
   so it would be cut into map tiles (PMTiles via `tippecanoe`, an install).
4. **Honesty**: whole-street placements are dashed and say so; values that cannot be placed stay in the barangay
   median, not invented.

Street-level placement is approximate: a name match is not a surveyed boundary. The barangay view remains the
reference; the street layer explains the spread inside it.

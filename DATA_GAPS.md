# Data gaps

Every area or metric on the map with missing or low-confidence data, and what would fill it. Nothing on the map is
estimated, interpolated or illustrative: a gap shows grey. Figures below are as of the build of 2026-11-07.

## 1. Land value (BIR zonal values, barangay level)

| Gap | Size | What would fill it |
|---|---|---|
| Values are mapped for 9 areas only (Metro Manila, Bulacan, Pampanga, Zambales, Rizal, Cavite, Laguna, Batangas, Quezon) | the rest of the country | All 122 RDO workbooks are now downloaded by `python -m pipeline.national` (into `cache/bir_national/`). Parsing them with `pipeline/parse_bir.py` and adding barangay-name aliases province by province would extend values nationwide. |
| Barangays with no BIR value of their own, residential | 223 of 7,032: Metro Manila 94, Laguna 49, Pampanga 22, Rizal 11, Quezon 11, Cavite 10, Batangas 14, Bulacan 8, Zambales 4 | Mostly BIR names that match no PSGC barangay: `pipeline/review/unmatched.csv`. Add rows to `pipeline/aliases.csv`. Some are barangays BIR now classes commercial or industrial only (card says so). |
| Commercial / industrial values | 490 barangays without a commercial value, 1,276 without an industrial value | Same: aliases, or BIR has no CR / I row there. |
| Fills removed in this release | 199 residential neighbour estimates, 24 city medians, 490 commercial and 1,276 industrial neighbour estimates | Were interpolation; now grey (`pipeline/integrity.py`). |
| "Projected to today" values | all barangays, opt-in toggle | A projection of BIR's own revision trend, labelled as one; never shown by default. Real fill: the RA 12001 SMVs once approved. |
| Travel time | all barangays, opt-in | A model on OpenStreetMap roads, not calibrated against real trips; labelled "projection". |

## 2. Data staleness: BIR zonal value schedules (city / municipality level, nationwide)

Source: every RDO workbook linked from [BIR's zonal values page](https://www.bir.gov.ph/zonal-values), read 2026-11-07
(`data/bir_rdo_index.csv`, `data/bir_workbooks.csv` with sha256). Audit trail per LGU: `data/bir_national_schedule.csv`.

**Coverage:** 1,623 of 1,642 cities and municipalities dated; 122 of 123 RDOs.

| Unknown (grey) | Why | What would fill it |
|---|---|---|
| Subic Bay Freeport Zone (RDO 19) | BIR publishes no workbook for it | Ask RDO 19 for its schedule |
| Kapalawan, Old Kaabakan, Kadayangan, Nabalawag, Pahamuddin, Malidegao, Ligawasan, Tugunan (BARMM Special Geographic Area) | New municipalities (2024 plebiscite); no BIR order names them yet | Ask BIR which Department Order applies to their barangays |
| Sudipen (La Union); Island Garden City of Samal, Braulio E. Dujali, Sawata (Davao del Norte); Enrique B. Magalona (Negros Occidental); Sultan Dumalondong, Pagagawan (BARMM); Science City of Muñoz (Nueva Ecija); President Carlos P. Garcia (Bohol); Macrohon (Southern Leyte); Pres. Manuel A. Roxas (Zamboanga del Norte) | The workbook's NOTICE spells the name in a way the matcher does not accept, or names a former town (e.g. Samal's old towns Babak and Kaputian) | Add a row to `NATIONAL_ALIASES` in `pipeline/national.py` after checking `pipeline/review/national_unmatched.csv` (242 names, mostly barangays listed where LGUs were expected) |

**Low confidence**

- An RDO's NOTICE sheet sometimes lists only some of the LGUs a Department Order covers. Where BIR's index page names
  more LGUs for the current order, those are used too (match column says so). An LGU an order leaves out keeps its
  older date, which can overstate staleness.
- Six RDOs list barangays or districts instead of LGUs (Baguio, Cebu City North and South, Davao City West and East):
  the whole RDO is read as its city.
- Where barangay values are mapped (the 9 areas above), dates come from the barangays' own values, which is more exact
  than the NOTICE sheet.
- 197 LGU names were matched fuzzily (typos like "Zaragosa", "Pagudpod"); each is listed with its score in
  `data/bir_national_schedule.csv`. Two wrong fuzzy matches were found and blocked (`FUZZY_REJECT`).

**Context figure (on the site):** BLGF counted 46 of 120 RDOs (38%) and 137 of 227 LGUs (60%) not revised in
three years: [BLGF, "RPVAR as of June 2021, Package 3", slide 5](https://tmap.org.ph/assets/blgf-presentation-on-package-3-for-tmap-gmm.pdf)
(zonal data as of April 2021, SMV data as of May 2021). The brief cited "~40%"; the source says 38%.

## 3. Data staleness: LGU Schedules of Market Values (SMV) and RA 12001 status

**Coverage: none.** `data/lgu_smv_status.csv` has its header only, so every LGU is grey.

Columns: `area_code` (PSGC of the city or municipality, as in `public/data/national.geojson`), `area_name`,
`last_smv_ordinance`, `last_smv_year`, `rpvara_smv_status` (`approved`, `submitted`, `in_preparation`,
`not_started`), `as_of`, `source`, `source_url`. A province's row is never spread to its towns.

Where to get it:

1. **BLGF** ([blgf.gov.ph](https://blgf.gov.ph)): the Bureau monitors every LGU's SMV. Its site refused this
   pipeline's automated requests (HTTP 403), so the list has to be downloaded by hand, or requested through
   [FOI (BLGF)](https://www.foi.gov.ph/agencies/blgf/), asking for "status of SMV preparation and approval under
   RA 12001, per LGU, with the date of the last SMV ordinance".
2. **LGU websites** publish proposed SMVs for public consultation. Leads found, not yet entered:
   [Davao City proposed SMV 2026](https://davaocity.gov.ph/wp-content/uploads/2025/11/Proposed-SMV-2026.pdf),
   [Mandaluyong proposed SMV](https://mandaluyong.gov.ph/storage/2026/03/PROPOSED-SMV-RPVARA.pdf),
   [Roxas City proposed 2026 SMV](https://roxascity.gov.ph/proposed-2026-schedule-of-market-values-smvs/),
   [Eastern Samar proposed 2026 SMV](https://easternsamarprovince.com/revised-2026-schedule-of-market-values-smv-for-the-province-of-eastern-samar/).
   A proposed SMV is `in_preparation`, not `submitted`, unless the LGU says it went to BLGF.

## 4. Untitled land

**Coverage: 3 areas, from one 2004 study.** `data/untitled_land.csv`, schema
`area_code, area_name, untitled_parcels, total_parcels, as_of, source, source_url, confidence, proxy, notes`.

| Area | Untitled / all parcels | Share | As of | Confidence |
|---|---|---|---|---|
| Metro Manila (NCR) | 159,734 / 1,549,636 | 10.3% | Sept 2004 | low |
| Cebu province, including Cebu City | 804,622 / 1,281,533 | 62.8% | Sept 2004 | low |
| Misamis Oriental, including Cagayan de Oro | 157,053 / 288,115 | 54.5% | Sept 2004 | low |

Source: PA-LAMP *Land Tenure Status Report*, Book 1 (Sept 2004), as reported in
[World Bank, Philippines Urbanization Review Policy Notes: Improving Land Administration and Management (2017), Table 3](https://documents1.worldbank.org/curated/en/334961495809114943/pdf/115311-PN-P156898-PUBLIC-Policy-Notes-Land-Administration-and-Management-FINAL2.pdf).
The table prints NCR as 11.33%, but its own counts give 10.31%; the map uses the counts.
Low confidence: 22 years old, and the study calls them estimates. The table also splits NCR into Districts 1 to 4;
not used, because it does not say which cities each district holds. For Cebu, whether Lapu-Lapu and Mandaue are
inside the study's area is not stated; the figure is shown on all of Cebu province and each card says so.

The national figure in the same note ("an estimated 11 million untitled parcels ... some 46 percent", from a 2004
study) is not mapped: it has no area to draw.

Candidate sources, what they hold and why they are not on the map yet:

| Source | Holds | Granularity | Status |
|---|---|---|---|
| DENR Land Management Bureau ([lmb.gov.ph](https://lmb.gov.ph)), LAMS | Cadastral lots, patents issued | lot / municipality | Site refused automated requests (403). Request a per-municipality count of surveyed lots with no patent or title. Patents issued are a flow, not the untitled stock: a **proxy** at best |
| Land Registration Authority | Titles (OCT/TCT) by Registry of Deeds | Registry of Deeds (province / city) | Titles registered ÷ lots surveyed would give the share titled. Needs a data request |
| LGU assessors' tax-declaration rolls | Every declared parcel, with or without a title number | parcel | The best direct source: share of tax declarations with no title reference. Needs LGU-by-LGU requests |
| PSA Census of Agriculture and Fisheries 2012 / 2022 | Farm parcels by tenure (owned, rented, other) | province / municipality | Farm parcels only, and "owned" is not "titled": a **proxy**; if added, set `proxy=yes` |
| DENR residential free patent statistics (RA 10023) | Free patents issued per year | region / province | A flow of new titles, not untitled land: a **proxy** |
| PIDS / World Bank LAMP studies | Titled / untitled estimates for pilot provinces | province | The source used above; nothing newer found |

## 5. Boundaries

- Barangays: PSGC December 2023 release (Manila from 2019). National cities and municipalities: PSGC 3Q 2026
  release (`pipeline/national.py`). Both from [faeldon/philippines-json-maps](https://github.com/faeldon/philippines-json-maps).
- `pipeline/fetch_boundaries.py` points at the repository's old `2023/` folder, which moved to the `2023-4q` release:
  it now returns 404, so `make boundaries` (and a full `make data`) fails on a fresh checkout until it is changed to
  download that release.

# Travel time view: design memo

Status: design agreed, nothing built yet. Written 2026-10-24; the user's decisions from the same day are folded in (see "Decisions" at the end). Terms and prices below were read from the providers' own pages on that date (sources at the end). Check them again before any money or account is involved.

## The idea

Distance is a poor guide to commute time in Metro Manila. A barangay 40 km out on an expressway can be as close in minutes as one 8 km away across EDSA. The user picks a target (their office, say). The map shades every barangay by its estimated travel time to that target, so a cheaper place further out can be compared with a dearer one closer in.

## Recommendation in one paragraph

Build it on **free, self-hosted routing (OSRM on OpenStreetMap)**, run on the Mac as part of the pipeline. Add a **congestion model with three time-of-day periods**, calibrated against **trips the user logs themself** and published traffic statistics. Precompute the result into static files so the site **stays a static Vercel site with no server, no API keys and no per-query cost**. Start with about 20 preset destinations, then add "any target" through precomputed all-pairs files. Show **ranges, not single numbers**, and stamp every view with the date of the road network and of the calibration. Do **not** use Google, Mapbox, TomTom or HERE for this, and not because of cost: each one's terms forbid storing results, publishing them on a MapLibre/OSM map, or using them to calibrate another dataset (§1.3). Total cash cost on this path is US$0.

---

## 1. Data and engines

### 1.1 Free and open

| Option | What it gives | Fit here |
|---|---|---|
| **OSRM** (self-hosted) on OSM | Very fast many-to-many tables (a 4,000 × 4,000 table takes seconds to minutes on a laptop). Speeds come from a Lua profile per road class, or from per-segment speed CSVs (`--segment-speed-file`); with the MLD algorithm, `osrm-customize` re-applies new speeds in minutes. **Not time-dependent**: one speed per road segment per dataset, so each time-of-day period is its own dataset. | **Recommended engine.** Three datasets (AM peak, PM peak, off-peak) are simple and fast. |
| **Valhalla** (self-hosted) on OSM | Historical traffic built in: per road edge, a free-flow speed, a daytime speed, and optionally 2,016 five-minute speeds across the week. Time-dependent matrices and isochrones, including "arrive by" (`date_time.type = 2`). Time-dependence in matrices is only allowed in some shapes; many origins → one target with an arrival time is one of them. Traffic is not supported for multimodal. | Better fidelity **if** per-edge speed data existed. It doesn't for Manila, and we would make it up from road class and zone anyway. Slower matrices and heavier tooling. Keep as an upgrade path. |
| **OSRM public demo server** | Same engine, hosted. Non-commercial use only, at most 1 request/second, no guarantees. | Development checks only. Not for bulk work. |
| **OSM speed tags** (`maxspeed`, `highway=*`, `lanes`, `toll`) | Free-flow speeds. `maxspeed` is patchy in PH; road-class defaults fill the gaps. | The starting point. **They encode no congestion at all**, which is the whole problem. |
| **GTFS** | Timetables for transit routing (OpenTripPlanner, r5). | I found **no current, maintained official GTFS for Metro Manila**. The last notable one is the 2013 DOTC/World Bank feed (stale). Rail is few enough lines to hand-build a small timetable (§2.4). |
| **OSM extracts** | Geofabrik's Philippines extract (582 MB `.osm.pbf`) is rebuilt about daily, with change files. On 2026-10-24 it held data up to 2026-10-22 20:21 UTC. | The road network source. Clip it to the five provinces before building the routing graph. |

Licence: times computed on OSM are a derivative of ODbL data. Credit "© OpenStreetMap contributors" (the site already does). If the matrix is published as a downloadable database, offer it under ODbL.

### 1.2 Traffic-aware commercial services

Costs below are for one full refresh of the preset matrix: 3,936 barangays × 20 destinations × 3 periods = **236,160 origin–destination pairs**.

| Service | Price (official page) | Limits | Approx. cost per refresh |
|---|---|---|---|
| **Google Routes API, Compute Route Matrix** | Traffic-aware (`TRAFFIC_AWARE`/`_OPTIMAL`) bills as **Pro**: 5,000 free per month, then US$10 per 1,000 elements. Essentials (no traffic) is US$5 per 1,000 after 10,000 free. Legacy Distance Matrix is priced the same. | 625 elements per request (100 for `TRAFFIC_AWARE_OPTIMAL`); 3,000 elements/minute | ≈ US$2,000–2,300 (traffic-aware; the rate drops above 100,000/month) |
| **Mapbox Matrix API** | 100,000 elements free per month, then US$2.00 per 1,000. Isochrone: 100,000 requests free, then US$2.00 per 1,000. | `driving-traffic`: **10 coordinates per request, 30 requests/minute**. `depart_at` is a beta you must apply for and cannot be in the past. | ≈ US$270, but at 10 coordinates per request it would take days of rate-limited calls |
| **TomTom Matrix Routing v2** | 2,500 free requests per month on the free tier. Discounted billing: when both sides exceed 5, a request bills as 5 × the larger side (so 3,936 × 20 bills as 19,680 transactions). Third-party summaries put it at about 2.50 per 1,000; the official pricing page did not show a per-1,000 rate. | — | ≈ US$150 (unconfirmed rate) |
| **HERE Matrix Routing** | The public pricing page showed no Matrix Routing rate. Plans list Matrix at 1 request/second, and 1,000 requests/day on the Limited plan. | — | Unknown without a sales quote |

### 1.3 What their terms allow (the deciding factor)

What this design needs: (a) store results for weeks, (b) show them on our MapLibre + OSM map, (c) possibly use a few paid samples to tune our own speeds.

| | Store results | Show on a non-own map | Use to calibrate our own data |
|---|---|---|---|
| **Google** | **No.** Service Specific Terms §19.3 (Routes API) and §4.3 (Directions) allow caching only lat/lng, for up to 30 days. Distance Matrix (§5) grants nothing. The Platform Terms §3.2.3(a)–(b) ban pre-fetching, storing and bulk-downloading "distance matrix results". | **No.** §19.2 says Routes API content must not be used with a non-Google map; §5.2 says the same for Distance Matrix, and Platform Terms §3.2.3(e) bans use near a non-Google map. The Routes policies page says results shown on a map must be on a Google Map. | **No.** §3.2.3(c), no creating content from Google Maps Content. |
| **Mapbox** (Product Terms, 20 Oct 2026) | **No.** §1.9(v): don't cache or store results. §2.10.1 says the same for Navigation APIs (Matrix, Isochrone, Directions). §1.9(ii): no bulk or automated queries. | Not banned outright for these APIs, but see the next column and the licence note. | **No.** §1.5(iv): don't use them to test, validate, benchmark or improve a substitute or competing dataset. §1.6: no deriving or extracting. |
| **TomTom** (developer licence) | **No.** §11.4 allows only client-side caching as the HTTP cache headers permit, and never to serve results to multiple users. | — | **No.** §11.6.1: no derived works or derived databases. |
| **HERE** (Platform Terms, Sept 2023, still the linked version) | **No** beyond 30 days (§6.3(j)). §6.3(l) bans scaling one request to serve multiple end users. Exhibit 1.3: only what the HTTP cache headers allow. | — | §6.3(i): no using HERE materials to create, enhance or evaluate another product outside your application. |

Two more points:
- **Mapbox §1.2.3** requires a paid **commercial licence for production use** of any application "related to … real estate". A zonal-value map arguably falls under that even though it is free. Get Mapbox's view in writing before relying on even the free tier.
- **TomTom's free tier is "Evaluation Use" only** (licence §2.2).

**Conclusion.** A precomputed, cached matrix shown on this map is out for all four. The only compliant commercial pattern is a live call per user query, with results shown without storage and, for Google, on a Google basemap. That means either rewriting the map on the Google Maps JavaScript API (with its own map-load fees), or Mapbox under a commercial licence. Mapbox on-demand would be one Isochrone call per query (US$0.002 after 100,000 free per month) intersected with barangay centroids in the browser, provided the licence question is settled. Both put a credit card behind every visitor's click, and neither can keep a dated, reproducible snapshot. Not recommended.

The user asked for "calibrating a free engine's speeds against a small number of paid samples". All four terms forbid that, so this memo calibrates against **the user's own trips plus published aggregate statistics** instead (§2.3). If they still want paid samples, the only clean route is written permission from the provider.

---

## 2. Modelling congestion

### 2.1 Periods

Three weekday periods, each its own OSRM dataset:

| Period | Meaning | Default? |
|---|---|---|
| **AM peak** | Arrive at the target by about 9:00 (driving roughly 6:30–9:00) | Yes, this is the commute question |
| **PM peak** | Leave the target about 18:00 | |
| **Off-peak** | Midday, or late evening | |

Weekends are left out of v1.

TomTom's 2025 Traffic Index for Manila (city) shows how far apart these are. It gives 31 min 45 s per 10 km averaged over the day, **34 min 53 s (17.2 km/h) in the morning rush and 43 min 29 s (13.8 km/h) in the evening rush**, and 56.2 km/h average on highways. The evening is clearly the worst, so the PM view matters as much as the AM one.

### 2.2 Speed model (per period)

speed(edge, period) = free-flow(edge) × multiplier(road class, zone, period), then intersection and turn penalties.

- **Free-flow**: OSM `maxspeed` where tagged, otherwise defaults per road class (motorway, trunk, primary, …), lowered for narrow or unpaved roads.
- **Road class**: expressway, national trunk/arterial (EDSA, C-5, Aguinaldo Hwy, Governor's Drive), other primary/secondary, local.
- **Zone**, three bands: central Metro Manila (inside C-5 plus the CBDs), outer Metro Manila, provinces. The barangay layer already gives each road segment a zone.
- **Bottleneck penalties** where the model is known to be too kind: expressway toll plazas and exits that queue (for example Skyway/SLEX at Alabang, EDSA ramps), and CBD street grids. Start with a handful, named in a reviewed CSV like `pipeline/aliases.csv`.

Multipliers are a small table, roughly 4 classes × 3 zones × 3 periods = 36 numbers. That is few enough to calibrate from tens of trips and to explain in a footnote.

### 2.3 Calibration without paid APIs

1. **Starting values** from published aggregates: TomTom Index city figures (above), and MMDA's EDSA speed reports (about 25 km/h average in May 2023). We use the numbers as reference statistics and do not touch TomTom's API.
2. **Trip log (the real calibration).** About **40–60 real trips**, covering each period and each zone pair, including the motivating case: Makati → Santa Rosa/Laguna versus Makati → Pasig at 18:00. No trips need to be driven just for this. Use trips that have already happened:
   - **Your own location history.** Google Maps Timeline export (now stored on the phone, exported from the app's settings) and Waze's account data download both hold your own past drives with start and end times. That is your personal data, not Google's or Waze's map content, so using it is fine.
   - **Ride-hailing receipts** (Grab and others): pickup time, drop-off time and both addresses for every past ride.
   - **Everyday trips going forward**, plus friends', family's and colleagues' commutes. Each person adds one line per trip.

   **Why not copy ETAs from Waze or Google Maps instead.** The Waze Terms of Service (last modified 8 July 2026, "Use restrictions") license the app for personal, non-commercial use only. They forbid copying content, building a database from it, and "creating a new product or service based on the Service or its Content". The Google Maps End User Additional Terms (§2.4, last modified 27 Jan 2026) forbid using Google Maps to create or augment a mapping or navigation dataset. A calibration table copied from their ETAs, then published as this map's travel times, is that kind of derived product. A few dozen manual lookups would probably never be noticed, but they break the letter of the terms, and the map would then quietly rest on Google's data. Looking at Waze yourself to sanity-check a result is fine. Typing its numbers into the calibration file is not. Logged trips have a second advantage: they are real outcomes, including the bad days, which is exactly what the ranges need.

   **Template**: [`data/trips_template.csv`](../data/trips_template.csv). One row per trip:

   | Column | Example | Notes |
   |---|---|---|
   | `date` | 2026-10-21 | Weekdays matter most; mark holidays in `conditions` |
   | `depart` / `arrive` | 07:05 / 08:20 | Local time, door to door |
   | `origin` / `dest` | Ayala Alabang, Muntinlupa / Bel-Air, Makati | Barangay and city (or a well-known landmark). No house addresses; the pipeline snaps each to its barangay |
   | `mode` | car | car, motorcycle, ride-hail, rail, bus |
   | `route` | Skyway | Main road or expressway, if known |
   | `conditions` | normal | normal, rain, flood, holiday, accident, event |
   | `source` | timeline | timeline, waze_history, grab_receipt, logged |
   | `who` | A | Initials only, so one person's habits can be checked |

   Keep the filled-in file (`data/trips.csv`) **out of the public repo**, since even barangay-level commute patterns are personal. The pipeline reads it locally and publishes only the fitted multipliers and the error figures.
3. **Fit** the 36 multipliers by least squares on log time, with sensible bounds (no multiplier above free-flow; expressway ≥ arterial in the same zone and period).
4. **Hold out 20%** of trips. Report the median and 80th-percentile error on the page, along with the trip count.
5. **Flag, don't smooth.** Trips the model misses by more than 40% get listed in a review file, just as unmatched barangays are today. A bad miss usually means a missing OSM road, a wrong one-way, or an unmodelled bottleneck. Fix the cause, never the trip.

Later option: a "log my commute" form on the site to grow the trip set. That needs a backend, moderation and a privacy note, so it is phase 5 or later.

### 2.4 Modes

- **Car first.** Assume tolls are paid. Show toll roads in the route explanation, and maybe toll cost later.
- **Rail + walk** (later phase). Model the rail lines as a small graph in Python, not through OTP/r5, since there is no usable GTFS. A trip is walk (up to 1.2 km at 4.5 km/h) or short road access at peak speed, then wait (half the headway), in-vehicle time from published timetables or line speeds, transfer penalties, and the egress walk. The EDSA Carousel busway belongs in the same model because it has its own lane. **Jeepney, bus and UV Express routes are not modelled.** Say so plainly on the map, because for many commuters they are the real mode.
- **Blocker found while writing this, needs fixing first.** `public/data/transport.geojson` has only **65 stations**, and many major ones are missing: Ayala, Buendia, Guadalupe and Taft (MRT-3), Baclaran and most of LRT-1, and most of LRT-2. `transport.py` keeps only nodes tagged `railway=station`; other stations are probably mapped as areas, or as `public_transport=station` or `railway=halt`. This already distorts the existing "distance to rail" factor. Baclaran, Parañaque shows **2.9 km** to rail although LRT-1 Baclaran station is in the barangay. San Lorenzo and Bel-Air, Makati show 1.3 km and 2.3 km. `docs/insights.md` uses that factor too. The fix is underway in a separate session: wider station extraction, deduplication, a line name per station, and flags for bad opening dates. The next full build will then refresh `f_rail`. Rail + walk is confirmed in scope.

---

## 3. Interaction and performance

### 3.1 Presets first, any target second

**Preset destinations (phase 1).** About 24 points:

- **Business districts**: Makati CBD (Ayala), BGC, Ortigas, Alabang (Filinvest), Binondo/Manila City Hall, Eastwood, Cubao/Araneta, Quezon City Hall/Triangle, the Bay Area (MOA/Aseana). The six already in `pipeline/insights.py` (`CBDS`) are the core.
- **Universities** (confirmed): UP Diliman, Ateneo de Manila (Loyola Heights), De La Salle University (Taft), University of Santo Tomas (España, Sampaloc).
- **Airports**: NAIA Terminals 1 and 3.
- **Southern and northern job centres**: Nuvali/Santa Rosa, Calamba, Malolos, and the Clark direction if it is inside the network.

Each preset is one coordinate at the main gate or a central point, listed in a reviewed CSV in `pipeline/` so it can be changed without code.

- Size: 3,936 × 24 × 3 periods × 2 bounds (low and high), as whole minutes in uint16, is about 1.1 MB raw, roughly 400–500 KB gzipped. Better still, one small file per destination (about 24 KB raw), fetched only when chosen. The same pattern as `public/data/streets/`.
- Compute: three OSRM tables of 3,936 × 20. Seconds.

**Any target (phase 3), static version: precomputed all-pairs.** Compute the full barangay × barangay table: 3,936² ≈ 15.5 million pairs per period. Write **one file per target barangay**: 3,936 origins × 3 periods as uint8 minutes, capped at 254 ("4 h+"), with 255 meaning unreachable. That is about 12 KB raw and 5–8 KB gzipped per file, about 46 MB for all of them.

- In the browser: the user clicks a point or searches an address. The point snaps to its barangay, which loads that barangay's file. Add the local leg inside the target barangay (straight-line distance × 1.4 at the local street speed) and widen the range to cover the snapping error.
- Compute: an OSRM CH/MLD full table is minutes per period on a laptop.
- Hosting: 46 MB per rebuild would bloat git over time. Recommendation: attach the files to a **GitHub Release** of this repo (no new account) and have the Vercel build download them, or regenerate them only quarterly.

**Any target, server version (not recommended).** A small VPS running OSRM on the clipped graph (a few GB of RAM) behind a tiny API. That means about US$5–20 a month plus upkeep, a new account, and a server to keep alive. Vercel serverless functions can't host OSRM or Valhalla (native binaries plus a multi-GB graph), so it would not stay "just Vercel". The only gain over the static version is exact points instead of barangay snapping, which is false precision given a ±25% model.

### 3.2 What stays static

| Piece | Where it runs |
|---|---|
| OSRM build, the three period datasets, matrices, calibration | Pipeline on the Mac (`python -m pipeline.traveltime`, new) |
| Preset and all-pairs files | Static, served from `public/data/` or a release asset |
| Shading, legend, ranges, ₱-vs-minutes chart | Browser (`src/main.ts`, as another "Colour by" option) |
| Server, serverless function, API key | **None** |

Cost per query: **zero.** One cached static file fetch.

---

## 4. Keeping it current

- **New roads.** OSM is edited continuously and Geofabrik rebuilds about daily. A new expressway reaches the map once mappers change it from `highway=construction` to `highway=motorway`. In Metro Manila that is usually within days of an opening, but not guaranteed, so check by hand when a road opens. The pipeline should **print the diff** between builds: ways that changed from construction/proposed to open, and new motorway/trunk segments. That works the way `transport.py` already prints the future named lines. Rebuild monthly, and also on news of a major opening (NLEX–SLEX Connector sections, C-5 South Link, CALAX segments, SEMME, LRT-1 extension stations, MRT-7).
- **Speeds.** Re-check the multipliers yearly when the TomTom Index publishes (January), and whenever the trip log grows by about 20 trips. Road openings change speeds near them (induced demand, new bottlenecks at exits), which a class × zone model can't see. That is one more reason for ranges.
- **"What if" scenarios (deferred by the user on 2026-10-24; kept here for later).** `transport.geojson` already holds construction and proposed expressways and rail. A scenario build temporarily treats chosen ones as open, at the speed of a comparable open road, and the map shows **minutes saved** per barangay. Caveats to show:
  - It assumes **today's congestion**. Real openings attract traffic, so savings are optimistic.
  - Proposed geometry in OSM is often rough and lacks ramps. Check that each scenario road connects to the network (no routing islands) before trusting the result.
  - Opening dates in OSM are unreliable. The current file has `opening_date` values like "11-0" for CALAX, which `transport.py` truncates to `open: "11-0"`. Duplicate names also appear ("NLEX-SLEX Connector" and "… Road"; "PNR North-South Commuter Railway" and "North-South Commuter Railway"). Show "expected" dates only where a reviewed source confirms them.
  - Start with expressways, where the car model applies. Rail scenarios wait for the rail mode.
- **Dates on screen.** Add `travel_time` to `meta.json`, and show it under the legend and in the card:
  > Road network: OpenStreetMap, 2026-10-22 · Congestion model: calibrated 2026-11-15 on 52 trips (median error 14%) · Weekday, arrive by 9:00 · Car

---

## 5. Honesty and UX

- **Ranges, not single numbers.** Card: "Makati CBD: **45–70 min** (AM peak, car)". Low end: the calibrated model on a good day, as the multiplier fit minus its residual spread. High end: a bad day, the 85th percentile of residuals, plus snapping error for custom targets. The map colours by the midpoint, and the legend says so.
- **Assumptions always visible**: mode, period, arrive-by or depart-at, weekday, "jeepneys and buses not modelled", "tolls assumed paid".
- **Legend**: fixed minute bands (≤15, 15–30, 30–45, 45–60, 60–75, 75–90, 90–120, 120–150, 150–180, **180+**), the same for every target so maps compare. Bands go well past two hours because evening trips from Bulacan or southern Laguna into the core can exceed them. Stored values run to 254 minutes; anything longer shows as "4 h+". Use a sequential palette distinct from the ₱ ramp so the two are never confused. Unreachable or out-of-network areas are grey.
- **Combining with land value:**
  1. **₱ vs minutes chart**: x = minutes to target (midpoint, with range whiskers), y = typical adjusted ₱/sqm (log scale), one dot per barangay, hatched or faded for `city_median`/`estimated` quality just as on the map. Draw the **Pareto frontier** (no barangay is both cheaper and closer than these). That frontier answers "a cheaper place with a similar commute" directly. Hovering a dot highlights the barangay on the map.
  2. **Bivariate map** (3 × 3: value tercile × time tercile), with "cheap and close" in a distinct colour.
  3. **Shortlist**: "within N minutes, lowest value", reusing the table style of `docs/insights.md`. The same caveats apply: zonal values are tax floors, not prices.
- **Wording**: "estimated", never "will take". Link to a short methods note (this memo, condensed) from the legend.

---

## 6. Phased plan

| Phase | Scope | Rough effort |
|---|---|---|
| 0 | Decisions (done). Fix the station extraction (in progress in a separate session; also corrects the `f_rail` factor). Install OSRM. | ½–1 day |
| 1 | OSRM with free-flow speeds and first-guess multipliers. Three periods. ~24 presets, including the four universities. "Colour by: travel time to…" with period toggle, legend, card line, date stamp, labelled *experimental*. | 3–5 days |
| 2 | Trip-log calibration from existing trips (Timeline/Waze history exports, ride-hail receipts, plus new everyday trips), holdout error, ranges, ₱-vs-minutes chart with frontier, bivariate view. | 3–5 days of work |
| 3 | Any target through all-pairs per-barangay files (GitHub Release), click-to-pick and address search snapping. | 3–4 days |
| 4 | Rail + walk (and the EDSA Carousel), hand-built rail timetables, on the corrected station list. Optional crowdsourced commute form. | 5–8 days |
| Later | What-if scenarios for under-construction expressways, with a minutes-saved map (deferred). | 2–3 days |
| Ongoing | Monthly OSM rebuild (mostly unattended, ~1 h), yearly recalibration | — |

## Decisions

Taken by the user on 2026-10-24:

1. **Budget: US$0** for now. No paid APIs, no VPS.
2. **Engine install approved**: Homebrew (`brew install osrm-backend`, version 26.10.0 with Apple Silicon bottles) or Docker. Homebrew is the lighter choice. Its installer needs the user's admin password, so the user runs that one step; everything after is scripted.
3. **Accounts**: none.
4. **Calibration** from trips that have already happened plus everyday trips, not from copied Waze or Google ETAs (§2.3). Template: `data/trips_template.csv`.
5. **Presets**: the business districts plus UP Diliman, Ateneo, DLSU and UST (§3.1).
6. **Ranges**: legend extends to 150–180 and 180+ (§5).
7. **What-if lines**: not now.
8. **Rail + walk**: yes. The station data fix runs in its own session.

Still open: the default period (proposed: weekday AM peak, arrive by 9:00).

## Sources (read 2026-10-24)

- Google Maps Platform Service Specific Terms (last modified 15 Oct 2026), §4 Directions, §5 Distance Matrix, §19 Routes API: https://cloud.google.com/maps-platform/terms/maps-service-terms
- Google Maps Platform Terms of Service (last modified 26 Aug 2026), §3.2.3: https://cloud.google.com/maps-platform/terms
- Google Routes API policies: https://developers.google.com/maps/documentation/routes/policies
- Google pricing: https://developers.google.com/maps/billing-and-pricing/pricing · Routes usage and limits: https://developers.google.com/maps/documentation/routes/usage-and-billing
- Mapbox pricing: https://www.mapbox.com/pricing · Matrix API docs: https://docs.mapbox.com/api/navigation/matrix/ · Mapbox Product Terms (20 Oct 2026, PDF linked from) https://www.mapbox.com/legal/product-terms
- TomTom pricing: https://docs.tomtom.com/pricing · Matrix v2 discounted billing: https://docs.tomtom.com/matrix-routing-v2-api/documentation/discounted-transaction-billing · Developer licence: https://developer.tomtom.com/terms-and-conditions
- HERE Platform Terms (effective 18 Sept 2023): https://www.here.com/en-gb/terms/here-platform-terms-september-2023 · Plan limits: https://www.here.com/get-started/pricing/rps-limits-excluded-use-cases
- OSRM demo server policy: https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server · OSRM traffic updates: https://github.com/Project-OSRM/osrm-backend/wiki/Traffic
- Valhalla historical traffic: https://github.com/valhalla/valhalla/blob/master/docs/docs/concepts/historical-traffic.md · Matrix API (time-dependent matrices): https://github.com/valhalla/valhalla/blob/master/docs/docs/api/matrix.md · Isochrone API: https://github.com/valhalla/valhalla/blob/master/docs/docs/api/isochrone.md
- Geofabrik Philippines extract: https://download.geofabrik.de/asia/philippines.html
- TomTom Traffic Index, Manila 2025: https://www.tomtom.com/traffic-index/city/manila/
- MMDA EDSA speeds, May 2023 (GMA News): https://gmanetwork.com/news/topstories/metro/872263/mmda-travel-speed-on-edsa-faster-in-may-2023-compared-to-2020/story
- 2013 Metro Manila GTFS (World Bank note): https://www.worldbank.org/content/dam/Worldbank/Brief/TAI/1511012-TransportICT-Newsletter-Note02-FINAL%20%282%29.pdf

Unverified: TomTom's per-1,000 Matrix price and any HERE Matrix price (neither official page showed one), and whether Mapbox's Isochrone API offers a traffic profile.

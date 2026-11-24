# Parcel Network Optimisation: Central & Western District

Where to put parcel lockers in Hong Kong's Central & Western District (SCLP / MCLP, simulation, sensitivity).
The research plan is in [Parcel Network Optimisation.md](Parcel%20Network%20Optimisation.md). The study started with Kennedy Town only and was widened to the whole district. To rerun the Kennedy Town-only version, set `STUDY_TPUS = ["111"]` in `pipeline/common.py`.

**Status:** Section 1, *Spatial Scoping & Data Collection*, is done. The inputs for the ILP
models (Section 2) and starting parameters for the simulation (Section 3) are ready.

![Study area map](figures/study_area_map.png)

For an interactive version, open [figures/study_area_map.html](figures/study_area_map.html) locally in a browser. Hover over any point to see its details.

## Headline numbers

| | |
|---|---|
| Study area | Central & Western District (official 2021-Census District Council District boundary, mainland part, 12.4 km²) |
| Demand nodes *i ∈ I* | **256** residential clusters (3,735 official Lands Department residential towers in 160 census Small Subunit Groups across 14 TPUs), holding **88,922 households / 235,953 residents**. These totals equal the official district totals exactly. |
| Existing sites | **157** within the district + 500 m buffer: SF Express (24 lockers + 39 staffed points), Cainiao (2 lockers + 28 stations), iPostal (4), JD (60, reference only). **87** of them form the public baseline. |
| Candidate sites | **450**: MTR exits (49), convenience stores (45), supermarkets (43), community facilities (39), malls (27), bus termini (12), wet markets (10), post offices (2), and in-building/estate-lobby sites (223) |
| Distance/time matrix *d_ij* | 256 × 607 pedestrian-network walking distances, **slope-aware** walking times, and climb (all pairs within 2 km) |
| Terrain | Demand nodes sit between 4 m and 467 m above sea level (The Peak). 25% of path segments are steeper than 10%. Slope adds 7% on average to walking times within 500 m. |
| Walking vs straight line | Median detour ratio **1.58** (90th percentile 2.64). Straight-line distance would put 178% more node–site pairs within 300 m than walking distance does. |

### Baseline coverage of the existing network (% of households)

| Walk to nearest site | 200 m | 300 m | 400 m | 500 m |
|---|---|---|---|---|
| **All public pick-up points** (SF, Cainiao, iPostal; 87 sites) | 50.0 | **69.9** | 79.5 | 86.7 |
| Lockers only (20) | 14.0 | **37.9** | 56.1 | 74.3 |
| Staffed points only (67) | 45.8 | 66.0 | 76.8 | 83.6 |
| SF Express, all (55) | 44.6 | 66.4 | 78.2 | 85.9 |
| SF Express lockers (16) | 13.8 | 33.0 | 53.5 | 71.4 |
| Cainiao (28) | 27.6 | 56.1 | 72.3 | 80.5 |
| iPostal (4) | 0.6 | 5.5 | 9.7 | 18.9 |

By slope-aware walking time, all public points cover 50.4% / 60.5% / 67.8% / 77.9% of households within 3 / 4 / 5 / 7 min.

**Where the gaps are** (households within 300 m of a public point, by TPU; area names are inferred from each TPU's largest buildings):

| TPU | Area (examples) | Households | All public points | Lockers only |
|---|---|---|---|---|
| 111 | Kennedy Town & Mount Davis (Smithfield Court, Kwun Lung Lau) | 22,668 | 79.7% | 45.1% |
| 112 | Shek Tong Tsui / Sai Ying Pun (Standard Mansion) | 20,858 | 94.6% | 51.9% |
| 113 | Sai Ying Pun / Sheung Wan (Lop Po Building, Centre Hollywood) | 10,012 | 79.2% | 25.9% |
| 114 | Sheung Wan (Centre Point) | 3,639 | 96.5% | 93.9% |
| 115 | Sheung Wan waterfront | 829 | 88.4% | 29.1% |
| 116 | Western waterfront (Belcher Court) | 4,998 | 97.6% | 91.2% |
| 121 | Central core | 269 | 26.7% | 0.0% |
| 122 | Central / Soho (Hopson Mansion) | 2,403 | 80.4% | 31.8% |
| 124 | Admiralty / Garden Road (Regent On The Park) | 238 | 25.1% | 25.1% |
| 141 | Mid-Levels West (2 Park Road) | 6,168 | **30.1%** | 8.0% |
| 142 | Mid-Levels (Carlos Court, Yee Lin Mansion) | 12,870 | **26.5%** | 4.0% |
| 143 | Mid-Levels East (Hoover Court, St. Louis Mansion) | 3,125 | **0.0%** | 0.0% |
| 181–182 | The Peak (Magazine Heights, Cloud Nine) | 844 | **0.0%** | 0.0% |

The low-lying west (Kennedy Town to Sheung Wan) is well served, mostly by staffed points. **The Mid-Levels and The Peak are poorly served**: about 22,000 households in TPUs 141–143 alone, where hillside walking makes every 100 m count. That is where the location models are likely to place new lockers.

Excluded from the baseline: residents-only lockers (e.g. SF's Cayman Rise and Cainiao's 只限住戶 lockers), JD (out of scope), and one JD row with a non-Hong Kong address.

## Repository layout

```
pipeline/                    run in order; each step reads the previous step's outputs
  common.py                  paths + ALL study & simulation parameters (study area, buffer, ...)
  01_fetch_raw.py            download raw data            -> data/raw/
  02_demand_nodes.py         boundary + demand nodes      -> data/processed/
  03_supply_sites.py         existing + candidate sites   -> data/processed/
  04_distance_matrix.py      walking distance/time matrix -> data/processed/
  05_maps.py                 overview maps                -> figures/
data/
  raw/                       untouched downloads, DTM clip, ALS geocoder cache
  raw/operators/             SF / JD / Cainiao pick-up point lists, SF store list
  manual/existing_lockers.csv  hand-logged extra sites (only used if not in the operator lists)
  processed/                 model inputs (see below)
figures/                     study_area_map.png / .html
image.png                    source screenshot of SF Locker list (Kennedy Town)
```

### Reproduce

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd pipeline
../.venv/bin/python 01_fetch_raw.py      # skips files that already exist; --force to refresh
../.venv/bin/python 02_demand_nodes.py
../.venv/bin/python 03_supply_sites.py
../.venv/bin/python 04_distance_matrix.py   # ~1 min for the district
../.venv/bin/python 05_maps.py
```

The 29 MB whole-territory DTM zip is git-ignored. Step 01 re-downloads it if `data/raw/dtm/dtm_5m_study_bbox.tif` is missing.

## Processed outputs (what the models use)

| File | Contents |
|---|---|
| `demand_nodes.csv` | `node_id`, `lat`, `lon`, `households` (use as **h_i**), `population`, `daily_parcels` (λ_i, see below), source SSG (its first 3 digits are the TPU), buildings |
| `supply_sites.csv` | `site_id`, `site_class` (existing/candidate), `operator`, `category` (locker / staffed_station / mtr_exit / …), `name`, `lat`, `lon`, `in_study_area`, `public_access`, `include_baseline` |
| `distance_matrix_m.csv` | Wide matrix of walking metres (rows = `node_id`, cols = `site_id`; blank = more than 2 km). Use **a_ij = 1 if d_ij ≤ D_max**. |
| `walk_time_matrix_min.csv` | Same layout, slope-aware walking minutes |
| `distance_long.csv` | Long format, one row per pair within 2 km: `walk_m`, `walk_min` (slope-aware), `walk_min_flat`, `climb_m` (ascent home→site), `euclid_m`, `detour_ratio` |
| `geocode_report.csv`, `snapping_report.csv` | QA: geocoder match score and street check; distance from each point to the network; ground elevation |
| `qa_network_gaps_kwun_lung_lau.csv` | OSM features near Kwun Lung Lau that may be missing pedestrian links (with links to openstreetmap.org) |
| `*.gpkg` | Same data as GIS layers (EPSG:2326, HK1980 Grid), for QGIS |

Useful filters for the models:
- **Existing baseline network:** `site_class == "existing" and include_baseline == "yes"`. Add `and category == "locker"` for lockers only.
- **Public candidates only:** drop `category == "residential_building"` (in-building lockers, residents only)
- **Sites outside the boundary** (`in_study_area == False`) are in J so that edge residents can be served across the boundary. Their demand is *not* counted.
- **Model size:** 256 × 450 candidates is small for PuLP/CBC. If you want a smaller instance, group nodes by TPU or drop in-building candidates.

## Methodology (draft text for the report)

**Study boundary.** The study area is Central & Western District, using the official 2021 Population Census District Council District boundary (C&SD). The uninhabited Green Island and Little Green Island are dropped. Demand comes from all 160 census Small Subunit Groups (SSGs) inside the district. Their households and population sum exactly to the published district totals (88,922 / 235,953). Supply sites are searched up to 500 m beyond the boundary, which equals the largest coverage radius in the sensitivity analysis. Residents near the edge can therefore be served by lockers just across it (e.g. in Wan Chai), which avoids edge-effect bias. Their demand is not counted.

**Demand nodes.** We use residential building clusters within census SSGs rather than coarse TPUs. This keeps the 300 m walking threshold accurate and avoids aggregation bias, while anchoring total demand to official 2021 Census figures. The steps are:
1. Take the official Lands Department building footprints (tower blocks only; podiums excluded) and assign them to their SSG. A tower is dropped as non-residential if it lies inside an OSM building tagged as a school, warehouse, office or similar, or if its name says so (e.g. depot, consulate, refuse chamber).
2. Estimate each building's gross floor area as footprint × official storeys: 3,145 towers have an official storey count, and 590 are estimated from building height ÷ 3.43 m (the median storey height of towers that have both).

   *Why not OSM storeys?* In the Kennedy Town pilot, OSM `building:levels` differed from the official count by 6.3 storeys on average. Only 30% matched exactly (Manhattan Heights is tagged 3 storeys but has 51), and OSM was missing 128 towers.
3. Group buildings within each SSG by complete-linkage clustering (maximum diameter 150 m). Merge clusters with fewer than 50 households into the nearest cluster.
4. Split each SSG's Census households and population across its clusters in proportion to floor area. Census households count *domestic* households only, so institutional quarters (student halls, hostels, elderly homes) receive residents but no households.
5. Place each node on the building nearest its floor-area-weighted centroid.

**Supply sites.**
- **SF Express, Cainiao and JD:** machine-readable lists compiled from each operator's own website, last verified 2026-10-05, with coordinates. They are published by hillmalaya.com.hk at `data/raw/operators/pickup-points-*.js`; the header of each file documents its provenance. The coordinates agree with the government Address Lookup Service (ALS) to 5 decimal places on spot checks. Site type comes from the Chinese name: 櫃 (自助櫃/智能櫃) means locker, and everything else (驛站, 服務站, 順豐站, OK便利店 counters) is a staffed point. Lockers marked 只限住戶 or "Only for residents" are flagged as not public.
- **SF stores:** from SF's store list PDF, geocoded with ALS. A match must score at least 60 *and* share a street or building name with the query. A high score alone is not enough: "7 North Street Kennedy Town" scored 84 but matched "7 Praya".
- **iPostal Stations:** from Hongkong Post's official CSDI dataset.
- **Candidate sites:** OpenStreetMap points of interest where a locker could realistically go, de-duplicated within 25 m. Every demand node is also a candidate in-building (estate-lobby) locker, as in SF's residents-only lockers. This guarantees that the 100%-coverage SCLP is feasible (J ⊇ I).

**Walking distance and time.**
- **Distance:** shortest paths on the OpenStreetMap pedestrian network (OSMnx `network_type="walk"`, undirected), which includes stairs, footbridges and mapped lifts. Each distance also adds the straight-line access legs from each point to its nearest network node. Pairs ≤ 25 m apart use the straight-line distance. Pairs more than 2 km apart are not stored.
- **Elevation:** each network node takes its height from the Lands Department 5 m DTM.
- **Speed:** each edge's speed follows Tobler's hiking function, *v = v₀·e^(−3.5|s+0.05|)*, rescaled so that flat ground = 4.5 km/h (75 m/min, so 300 m ≈ 4 min).
- **Direction:** residents walk to the locker and back, so each edge's time is the mean of its uphill and downhill times (half the round trip). Tunnel edges are treated as flat, and grades are capped at ±50%.

## Simulation parameters (Section 3), starting values in `pipeline/common.py`

| Parameter | Value | Basis |
|---|---|---|
| Parcels per household per day | **0.35** (sensitivity range 0.20–0.50) | About 350 M e-commerce parcels in HK in 2025, ÷ about 2.7 M households ÷ 365. The volume is a Statista figure cited second-hand ([zhbfwl.com](https://www.zhbfwl.com/en/case/hk-local-ecommerce-delivery)); we could not open Statista to verify it. **Replace with a primary source if possible.** |
| Share of parcels sent to lockers or pick-up points | 0.30 | **Assumption**, to be varied in the sensitivity analysis. Unsourced market reports put HK locker/pick-up adoption at 25–35% ([Mordor Intelligence](https://www.mordorintelligence.com/industry-reports/hong-kong-ecommerce-market), [CLEARgo](https://www.cleargo.com/insights/hong-kong-e-commerce-market-2026-trends-size-growth-opportunities)). In a KPMG/YouGov survey of 2,065 HK consumers (2022), 69% expected retailers to offer smart-locker delivery ([KPMG](https://assets.kpmg.com/content/dam/kpmg/cn/pdf/en/2022/11/retail-recharged.pdf)). |
| Dwell time (plan) | 60% collected within 24 h, 30% within 48 h, 10% within 72 h | Research plan |
| Dwell time (empirical benchmark) | median 4.2 h, mean 12.5 h, 84% within 24 h, 99% within 7 days | Urban Freight Lab residential-locker field study ([PDF](https://urbanfreightlab.com/wp-content/uploads/2023/05/Do-Lockers-Reduce-Delivery-Dwell-Time-Evidence-from-Field_UrbanFreightLab.pdf)). An Australia Post study of 51 lockers found dwell stabilising around 15–16 h ([arXiv 2409.05061](https://arxiv.org/html/2409.05061v2)). |
| Locker size (compartments) | 40 / 80 / 120 | Research plan |
| Peak surge | ×1.0 / ×1.5 / ×2.0 | Research plan |

Context: C&SD reports that 43.1% of people aged 15+ shopped online in 2020, up from 35.8% in 2018 ([THS Report 73 press release](https://www.info.gov.hk/gia/general/202104/15/P2021041500293.htm)).

## Data sources

| Data | Source |
|---|---|
| Census SSG boundaries, households, population | C&SD 2021 Population Census, Small Subunit Groups ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-censtatd-census_geo-2021-population-census-by-ssg), CSDI WFS `censtatd_rcd_1635933365202_19171`) |
| District boundary and official totals | C&SD 2021 Population Census, District Council Districts ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-censtatd-census_geo-2021-population-census-by-dcd), CSDI WFS `censtatd_rcd_1635932856094_19982`) |
| Building footprints and storeys | Lands Department *Building* layer ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-landsd-openmap-landsd-building), CSDI WFS `landsd_rcd_1637211194312_35158`) |
| Terrain | Lands Department 5 m Digital Terrain Model ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-landsd-openmap-5m-grid-dtm)), ±5 m accuracy. It includes elevated roads and bridges. |
| SF / Cainiao / JD pick-up points | [hillmalaya.com.hk pick-up points finder](https://hillmalaya.com.hk/pages/pick-up-points-finder-for-jd-logistics-cainiao-sf-express) data files (compiled from the operators' sites, last verified 2026-10-05). SF Kennedy Town lockers are also cross-checked against `image.png`. |
| SF stores | SF store list PDF, Hong Kong Island (2026-10-29) |
| iPostal Stations | Hongkong Post ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-hkpo-hkpo_ds01-hkpo-ipostal-station), CSDI WFS `hkpo_rcd_1639010988627_23643`) |
| Geocoding | HKSAR Address Lookup Service, [als.gov.hk](https://www.als.gov.hk) |
| Building use (non-residential mask), POIs, pedestrian network | © OpenStreetMap contributors (ODbL), via Overpass / OSMnx |
| Cross-check | [Centamap census by building group](https://census.centamap.com/Region/List?type=hma&code=HMA111&detailcode=CW0071) (Kennedy Town) |

## Open items / limitations

- [ ] **Operator lists are third-party compilations** (hillmalaya.com.hk), though they are sourced from the operators' sites and verified 2026-10-05. Cainiao's English addresses in them were written by hand; the Chinese addresses and coordinates are the reliable fields. Spot-check a sample in the SF and Cainiao apps. Note that Cainiao's Z22 (Luen On Apartments, 51 Smithfield), which an older list showed, is no longer in Cainiao's list; the nearest current station is Z50 at 47 Smithfield.
- [ ] **Cainiao lockers may still be undercounted.** Cainiao HK reports 1,000+ self-pickup lockers territory-wide ([unwire.hk](https://unwire.hk/2025/04/29/cainiaohongkong/life-tech/)), but the list has 278 lockers, only 2 of them in this district (both residents-only).
- [ ] **Kwun Lung Lau walking distances may be overstated** because of disconnected OSM footways and a dead-end lift near the estate. See `data/processed/qa_network_gaps_kwun_lung_lau.csv`. The same kind of check is worth doing for Mid-Levels estates, where stairs and lifts matter most.
- [ ] **Central core (TPU 121, 269 households):** no residential building is identifiable among the office towers, so its few census households are spread over the towers there. This is negligible at district level (0.3%).
- [ ] **Parcel rate:** the 350 M parcels/year figure is secondary. A primary source was not found: the C&SD Thematic Household Survey reports only the share of people shopping online, not how often.
- [ ] **Locker share (0.30)** is an assumption, consistent with unsourced industry estimates of 25–35%. Vary it in the sensitivity analysis.
- [ ] **Alfred24:** skipped (no public list).
- [ ] **DTM caveats:** ±5 m accuracy, and it includes elevated structures. We damp the noise by capping grades and treating edges shorter than 5 m as flat.
- [ ] **Residential classification:** towers with no OSM footprint are assumed residential unless their name says otherwise. Census totals per SSG are fixed, so a misclassified tower only shifts demand *within* its SSG.

# Parcel Network Optimisation: Kennedy Town

Where to put parcel lockers in Kennedy Town, Hong Kong (SCLP / MCLP, simulation, sensitivity).
The research plan is in [Parcel Network Optimisation.md](Parcel%20Network%20Optimisation.md).

**Status:** Section 1, *Spatial Scoping & Data Collection*, is done. The inputs for the ILP
models (Section 2) and starting parameters for the simulation (Section 3) are ready.

![Study area map](figures/study_area_map.png)

For an interactive version, open [figures/study_area_map.html](figures/study_area_map.html) locally in a browser. Hover over any point to see its details.

## Headline numbers

| | |
|---|---|
| Study area | 2021 Census TPU 111, *Kennedy Town & Mount Davis* (mainland part, 1.60 km²) |
| Demand nodes *i ∈ I* | **41** residential clusters, holding **22,668 households / 60,366 residents**. These totals match the 2021 Census exactly. |
| Existing sites | **16** within the study area + 500 m buffer: SF Express (5 lockers + 1 store), Cainiao (7 staffed stations), iPostal (1), JD (2, reference only) |
| Candidate sites | **94**: MTR exits, wet markets, malls, community facilities, supermarkets, convenience stores, bus termini, and in-building/estate-lobby sites |
| Distance/time matrix *d_ij* | 41 × 110 pedestrian-network walking distances, **slope-aware** walking times, and climb |
| Terrain | Demand nodes sit between 3 m and 235 m above sea level. 28% of path segments are steeper than 10%. |
| Walking vs straight line | Median detour ratio **1.62** (90th percentile 2.83). Straight-line distance would put 166% more node–site pairs within 300 m than walking distance does. |

### Baseline coverage of the existing network (% of households)

| Walk to nearest site | 200 m | 300 m | 400 m | 500 m |
|---|---|---|---|---|
| **All public pick-up points** (SF lockers, Cainiao stations, iPostal) | 51.0 | **75.9** | 78.8 | 81.5 |
| Lockers only (SF + iPostal) | 31.0 | **47.0** | 58.9 | 79.2 |
| Cainiao stations only | 37.1 | 54.4 | 58.5 | 72.0 |
| SF Express lockers only | 31.0 | 47.0 | 58.9 | 79.2 |

By slope-aware walking time, all public points cover 39.8% / 65.1% / 69.9% / 77.3% of households within 3 / 4 / 5 / 7 min.

Excluded from the baseline: SF's Cayman Rise locker (residents only), the SF Store (staffed, not a locker), JD (out of scope), and a possible duplicate Cainiao entry.

## Repository layout

```
pipeline/                    run in order; each step reads the previous step's outputs
  common.py                  paths + ALL study & simulation parameters
  01_fetch_raw.py            download raw data            -> data/raw/
  02_demand_nodes.py         boundary + demand nodes      -> data/processed/
  03_supply_sites.py         existing + candidate sites   -> data/processed/
  04_distance_matrix.py      walking distance/time matrix -> data/processed/
  05_maps.py                 overview maps                -> figures/
data/
  raw/                       untouched downloads, DTM clip, ALS geocoder cache
  manual/existing_lockers.csv  hand-logged operator sites (SF, Cainiao, JD)
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
../.venv/bin/python 04_distance_matrix.py
../.venv/bin/python 05_maps.py
```

The 29 MB whole-territory DTM zip is git-ignored. Step 01 re-downloads it if `data/raw/dtm/dtm_5m_kennedy_town.tif` is missing.

## Processed outputs (what the models use)

| File | Contents |
|---|---|
| `demand_nodes.csv` | `node_id`, `lat`, `lon`, `households` (use as **h_i**), `population`, `daily_parcels` (λ_i, see below), source SSG, buildings |
| `supply_sites.csv` | `site_id`, `site_class` (existing/candidate), `operator`, `category` (locker / staffed_station / mtr_exit / …), `name`, `lat`, `lon`, `in_study_area`, `public_access`, `include_baseline` |
| `distance_matrix_m.csv` | Wide matrix of walking metres (rows = `node_id`, cols = `site_id`). Use **a_ij = 1 if d_ij ≤ D_max**. |
| `walk_time_matrix_min.csv` | Same layout, slope-aware walking minutes |
| `distance_long.csv` | Long format per pair: `walk_m`, `walk_min` (slope-aware), `walk_min_flat`, `climb_m` (ascent home→site), `euclid_m`, `detour_ratio` |
| `geocode_report.csv`, `snapping_report.csv` | QA: geocoder match score and street check; distance from each point to the network; ground elevation |
| `*.gpkg` | Same data as GIS layers (EPSG:2326, HK1980 Grid), for QGIS |

Useful filters for the models:
- **Existing baseline network:** `site_class == "existing" and include_baseline == "yes"`. Add `and category == "locker"` for lockers only.
- **Public candidates only:** drop `category == "residential_building"` (in-building lockers, residents only)
- **Sites outside the boundary** (`in_study_area == False`) are in J so that edge residents can be served across the boundary. Their demand is *not* counted.

## Methodology (draft text for the report)

**Study boundary.** The study area is 2021 Population Census Tertiary Planning Unit 111 (Kennedy Town & Mount Davis). It contains The Merton, The Belcher's, Kwun Lung Lau, Sai Wan Estate and Kennedy Town MTR station; we confirmed this by geocoding each with the HKSAR Address Lookup Service. We drop the uninhabited Green Island and Little Green Island. Supply sites are searched up to 500 m beyond the boundary, which equals the largest coverage radius in the sensitivity analysis. Residents near the edge can therefore be served by lockers just across it, which avoids edge-effect bias. Their demand is not counted.

**Demand nodes.** We use residential building clusters within census Small Subunit Groups (SSGs) rather than coarse TPUs. This keeps the 300 m walking threshold accurate and avoids aggregation bias, while anchoring total demand to official 2021 Census figures. The steps are:
1. Assign residential OSM building footprints to their SSG. Named non-residential buildings (offices, depots, community complexes) are excluded.
2. Estimate each building's gross floor area as footprint × storeys. Missing storeys (15%) are imputed with the SSG median.
3. Group buildings within each SSG by complete-linkage clustering (maximum diameter 150 m). Merge clusters with fewer than 50 households into the nearest cluster.
4. Split each SSG's Census households and population across its clusters in proportion to floor area. Census households count *domestic* households only, so student halls and hostels receive residents but no households.
5. Place each node on the building nearest its floor-area-weighted centroid.

One SSG (111/28) has no mapped residential footprint, so its node sits at the SSG's interior point.

**Supply sites.**
- **Existing sites:** we geocoded operator addresses with the government ALS. Every match had to score at least 60 *and* share a street or building name with the query. A high score alone is not enough: "7 North Street Kennedy Town" scored 84 but matched "7 Praya". See `geocode_report.csv`. iPostal Stations come from Hongkong Post's official CSDI dataset.
- **Candidate sites:** OpenStreetMap points of interest where a locker could realistically go, de-duplicated within 25 m. Every demand node is also a candidate in-building (estate-lobby) locker, as in SF's residents-only locker at Cayman Rise. This guarantees that the 100%-coverage SCLP is feasible (J ⊇ I).

**Walking distance and time.**
- **Distance:** shortest paths on the OpenStreetMap pedestrian network (OSMnx `network_type="walk"`, undirected), which includes stairs and footbridges. Each distance also adds the straight-line access legs from each point to its nearest network node. Pairs ≤ 25 m apart use the straight-line distance.
- **Elevation:** each network node takes its height from the Lands Department 5 m DTM.
- **Speed:** each edge's speed follows Tobler's hiking function, *v = v₀·e^(−3.5|s+0.05|)*, rescaled so that flat ground = 4.5 km/h (75 m/min, so 300 m ≈ 4 min).
- **Direction:** residents walk to the locker and back, so each edge's time is the mean of its uphill and downhill times (half the round trip). Tunnel edges are treated as flat, and grades are capped at ±50%.
- **Effect:** slope adds 9% on average to walking times within 500 m.

## Simulation parameters (Section 3), starting values in `pipeline/common.py`

| Parameter | Value | Basis |
|---|---|---|
| Parcels per household per day | **0.35** (sensitivity range 0.20–0.50) | About 350 M e-commerce parcels in HK in 2025, ÷ about 2.7 M households ÷ 365. The volume is a Statista figure cited second-hand ([zhbfwl.com](https://www.zhbfwl.com/en/case/hk-local-ecommerce-delivery)); we could not open Statista to verify it. **Replace with a primary source if possible.** |
| Share of parcels sent to lockers or pick-up points | 0.30 | **Assumption**, to be varied in the sensitivity analysis |
| Dwell time (plan) | 60% collected within 24 h, 30% within 48 h, 10% within 72 h | Research plan |
| Dwell time (empirical benchmark) | median 4.2 h, mean 12.5 h, 84% within 24 h, 99% within 7 days | Urban Freight Lab residential-locker field study ([PDF](https://urbanfreightlab.com/wp-content/uploads/2023/05/Do-Lockers-Reduce-Delivery-Dwell-Time-Evidence-from-Field_UrbanFreightLab.pdf)). An Australia Post study of 51 lockers found dwell stabilising around 15–16 h ([arXiv 2409.05061](https://arxiv.org/html/2409.05061v2)). |
| Locker size (compartments) | 40 / 80 / 120 | Research plan |
| Peak surge | ×1.0 / ×1.5 / ×2.0 | Research plan |

Context: C&SD reports that 43.1% of people aged 15+ shopped online in 2020, up from 35.8% in 2018 ([THS Report 73 press release](https://www.info.gov.hk/gia/general/202104/15/P2021041500293.htm)). Cainiao reports over 1,000 self-pickup points in HK ([PR Newswire](https://prnewswire.com/apac/news-releases/cainiao-expand-express-infrastructure-with-over-1-000-self-pickup-points-in-hong-kong-302278888.html)).

## Data sources

| Data | Source |
|---|---|
| Census SSG boundaries, households, population | C&SD 2021 Population Census, Small Subunit Groups ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-censtatd-census_geo-2021-population-census-by-ssg), CSDI WFS `censtatd_rcd_1635933365202_19171`) |
| Terrain | Lands Department 5 m Digital Terrain Model ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-landsd-openmap-5m-grid-dtm)), ±5 m accuracy. It includes elevated roads and bridges. |
| iPostal Stations | Hongkong Post ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-hkpo-hkpo_ds01-hkpo-ipostal-station), CSDI WFS `hkpo_rcd_1639010988627_23643`) |
| SF Lockers (Kennedy Town) | SF Express locker list, Nov 2026 (`image.png`). SF Store: SF store list (Oct 2026). |
| Cainiao stations | [hillmalaya.com.hk pick-up points finder](https://hillmalaya.com.hk/pages/pick-up-points-finder-for-jd-logistics-cainiao-sf-express), plus the [pickup852.com 西環 list](https://pickup852.com/district_list.php?id=15) as indexed by search. pickup852 blocks automated access, so its entries come from search snippets. Both are third-party lists. |
| Geocoding | HKSAR Address Lookup Service, [als.gov.hk](https://www.als.gov.hk) |
| Buildings, POIs, pedestrian network | © OpenStreetMap contributors (ODbL), via Overpass / OSMnx |
| Cross-check | [Centamap census by building group](https://census.centamap.com/Region/List?type=hma&code=HMA111&detailcode=CW0071). Centaline's "Kennedy Town" has 53,153 residents vs 60,366 in TPU 111, which also covers Mount Davis and Sai Wan Estate. Average household size of 2.5 is consistent with our 2.66. |

## Open items / limitations

- [ ] **Verify the Cainiao stations** in the Cainiao app: whether each is open, its exact location, and whether any are lockers rather than staffed 驛站. In particular, pickup852 labels **Z21** at 133 Belcher's Street while hillmalaya puts Z21 at 147. We kept 147 in the baseline and 133 as reference only. 7 North Street is also listed as a JD point (a laundry), so check it.
- [ ] **Parcel rate:** replace the secondary 350 M parcels/year figure with a primary source if one is available. Candidates: the C&SD Thematic Household Survey (IT usage) online-purchase frequency, or Hongkong Post annual report volumes.
- [ ] **Locker share (0.30)** is an assumption. Vary it in the sensitivity analysis.
- [ ] **Alfred24:** skipped (no public list).
- [ ] **DTM caveats:** ±5 m accuracy, and it includes elevated structures. We damp the noise by capping grades and treating edges shorter than 5 m as flat. Footpaths directly under flyovers may get the deck height.
- [ ] **Kwun Lung Lau area (around D028–D030):** straight-line distances are short but walking distances are long because of the hillside. Check that OSM has every stair and lift link here; an unmapped shortcut would understate coverage.
- [ ] **Not ground-truthed:** OSM building storeys and candidate POIs.

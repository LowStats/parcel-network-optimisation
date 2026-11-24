# Parcel Network Optimisation: Kennedy Town

Where to put parcel lockers in Kennedy Town, Hong Kong (SCLP / MCLP, simulation, sensitivity).
The research plan is in [Parcel Network Optimisation.md](Parcel%20Network%20Optimisation.md).

**Status:** Section 1, *Spatial Scoping & Data Collection*, is done. The inputs for the ILP
models (Section 2) are in `data/processed/`.

![Study area map](figures/study_area_map.png)

For an interactive version, open [figures/study_area_map.html](figures/study_area_map.html) locally in a browser. Hover over any point to see its details.

## Headline numbers

| | |
|---|---|
| Study area | 2021 Census TPU 111, *Kennedy Town & Mount Davis* (mainland part, 1.60 km²) |
| Demand nodes *i ∈ I* | **42** residential clusters, holding **22,668 households / 60,366 residents**. These totals match the 2021 Census exactly. |
| Existing sites | 10 within the study area + 500 m buffer: SF Express ×5, Cainiao ×2, iPostal ×1, JD ×2 (JD for reference only) |
| Candidate sites | 98: MTR exits, wet markets, malls, community facilities, supermarkets, convenience stores, bus termini, and in-building/estate-lobby sites |
| Distance matrix *d_ij* | 42 × 108 pedestrian-network walking distances and times (OSM + OSMnx) |
| Walking vs straight line | Median detour ratio **1.64** (90th percentile 2.90). Straight-line distance would put 176% more node–site pairs within 300 m than walking distance does. |
| **Baseline coverage** | The existing public network (SF, Cainiao, iPostal; Cayman Rise residents-only locker excluded) covers **34% / 53% / 65% / 80%** of households within a **200 / 300 / 400 / 500 m** walk |

## Repository layout

```
pipeline/                    run in order; each step reads the previous step's outputs
  common.py                  paths + ALL study parameters (boundary TPUs, buffer, speeds, parcel rate)
  01_fetch_raw.py            download raw data            -> data/raw/
  02_demand_nodes.py         boundary + demand nodes      -> data/processed/
  03_supply_sites.py         existing + candidate sites   -> data/processed/
  04_distance_matrix.py      walking distance/time matrix -> data/processed/
  05_maps.py                 overview maps                -> figures/
data/
  raw/                       untouched downloads (+ ALS geocoder cache)
  manual/existing_lockers.csv  hand-logged operator sites (SF, Cainiao, JD; Alfred24 TODO)
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

## Processed outputs (what the models use)

| File | Contents |
|---|---|
| `demand_nodes.csv` | `node_id`, `lat`, `lon`, `households` (use as **h_i**), `population`, `daily_parcels` (λ_i, see assumption), source SSG, buildings |
| `supply_sites.csv` | `site_id`, `site_class` (existing/candidate), `operator`, `category`, `name`, `lat`, `lon`, `in_study_area`, `public_access`, `include_baseline` |
| `distance_matrix_m.csv` | Wide matrix of walking metres (rows = `node_id`, cols = `site_id`). Use **a_ij = 1 if d_ij ≤ D_max**. |
| `walk_time_matrix_min.csv` | Same layout, walking minutes |
| `distance_long.csv` | Long format: `walk_m`, `walk_min`, `euclid_m`, `detour_ratio` per pair |
| `geocode_report.csv`, `snapping_report.csv` | QA: geocoder match scores; distance from each point to the network |
| `*.gpkg` | Same data as GIS layers (EPSG:2326, HK1980 Grid), for QGIS |

Useful filters for the models:
- **Existing baseline network:** `site_class == "existing" and include_baseline == "yes"`
- **Public candidates only:** drop `category == "residential_building"` (in-building lockers, residents only)
- **Sites outside the boundary** (`in_study_area == False`) are in J so that edge residents can be served across the boundary. Their demand is *not* counted.

## Methodology (draft text for the report)

**Study boundary.** The study area is 2021 Population Census Tertiary Planning Unit 111 (Kennedy Town & Mount Davis). It contains The Merton, The Belcher's, Kwun Lung Lau, Sai Wan Estate and Kennedy Town MTR station; we confirmed this by geocoding each with the HKSAR Address Lookup Service. We drop the uninhabited Green Island and Little Green Island. Supply sites are searched up to 500 m beyond the boundary, which equals the largest coverage radius in the sensitivity analysis. Residents near the edge can therefore be served by lockers just across it, which avoids edge-effect bias. Their demand is not counted.

**Demand nodes.** We use residential building clusters within census Small Subunit Groups (SSGs) rather than coarse TPUs. This keeps the 300 m walking threshold accurate and avoids aggregation bias, while anchoring total demand to official 2021 Census figures. The steps are:
1. Assign residential OSM building footprints to their SSG.
2. Estimate each building's gross floor area as footprint × storeys. Missing storeys (15%) are imputed with the SSG median.
3. Group buildings within each SSG by complete-linkage clustering (maximum diameter 150 m). Merge clusters with fewer than 50 households into the nearest cluster.
4. Split each SSG's Census households and population across its clusters in proportion to floor area.
5. Place each node on the building nearest its floor-area-weighted centroid.

One SSG (111/28) has no mapped residential footprint, so its node sits at the SSG's interior point.

**Supply sites.**
- **Existing sites:** we geocoded operator addresses with the government ALS. Every match scored at least 60, and each was checked by eye; see `geocode_report.csv`. iPostal Stations come from Hongkong Post's official CSDI dataset.
- **Candidate sites:** OpenStreetMap points of interest where a locker could realistically go, de-duplicated within 25 m. Every demand node is also a candidate in-building (estate-lobby) locker, as in SF's residents-only locker at Cayman Rise. This guarantees that the 100%-coverage SCLP is feasible (J ⊇ I).

**Walking distance.** Distances are shortest paths on the OpenStreetMap pedestrian network (OSMnx `network_type="walk"`, undirected). The network includes stairs and footbridges. Each distance also adds the straight-line access legs from each point to its nearest network node. Pairs ≤ 25 m apart (same building or next door) use the straight-line distance. Walking time assumes 75 m/min (4.5 km/h, so 300 m ≈ 4 min), with stair segments taking ×2.

## Data sources

| Data | Source |
|---|---|
| Census SSG boundaries, households, population | C&SD 2021 Population Census, Small Subunit Groups ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-censtatd-census_geo-2021-population-census-by-ssg), CSDI WFS `censtatd_rcd_1635933365202_19171`) |
| iPostal Stations | Hongkong Post ([data.gov.hk](https://data.gov.hk/en-data/dataset/hk-hkpo-hkpo_ds01-hkpo-ipostal-station), CSDI WFS `hkpo_rcd_1639010988627_23643`) |
| SF Lockers (Kennedy Town) | SF Express locker list, Nov 2026 (`image.png`) |
| Cainiao / JD pick-up points | [hillmalaya.com.hk pick-up points finder](https://hillmalaya.com.hk/pages/pick-up-points-finder-for-jd-logistics-cainiao-sf-express) (third-party list; verify in the apps) |
| Geocoding | HKSAR Address Lookup Service, [als.gov.hk](https://www.als.gov.hk) |
| Buildings, POIs, pedestrian network | © OpenStreetMap contributors (ODbL), via Overpass / OSMnx |
| Cross-check | [Centamap census by building group](https://census.centamap.com/Region/List?type=hma&code=HMA111&detailcode=CW0071). Centaline's "Kennedy Town" has 53,153 residents vs 60,366 in TPU 111, which also covers Mount Davis and Sai Wan Estate. Average household size of 2.5 is consistent with our 2.66. |

## Open items / limitations

- [ ] **Alfred24 lockers are missing.** We found no public list. Log them from the alfred24 app into `data/manual/existing_lockers.csv`, leaving `lat`/`lon` blank to auto-geocode, then rerun steps 3–5.
- [ ] **Verify the Cainiao and JD points** in their apps. They come from a third-party list, and Cainiao "驛站" are staffed stations, not lockers (`category = staffed_station`).
- [ ] **Parcel rate assumption:** `PARCELS_PER_HOUSEHOLD_PER_DAY = 0.30` in `common.py` is a placeholder and needs a sourced figure before the Section 3 simulation. It does not affect the ILP coverage weights.
- [ ] **Slope is not yet in walking time.** Network distance captures detours around hills, stairs and footbridges, but not uphill effort. Possible fix: add a LandsD DTM and apply Tobler's hiking function.
- [ ] **Kwun Lung Lau area (around D029–D031):** straight-line distances are short but walking distances are long because of the hillside. Check that OSM has every stair and lift link here; an unmapped shortcut would understate coverage.
- [ ] **HKU halls in SSG 111/35 & 111/43** receive households in proportion to floor area, but hall residents are not domestic households. This is a minor misallocation.
- [ ] **Not ground-truthed:** OSM building storeys and candidate POIs.

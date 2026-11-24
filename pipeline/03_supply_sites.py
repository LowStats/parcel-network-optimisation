"""Step 3 - supply nodes (j in J): existing pick-up sites + candidate locker sites.

Existing sites
  * data/manual/existing_lockers.csv (SF Express, Cainiao, ... logged by hand) - geocoded with the
    HKSAR Address Lookup Service (ALS, www.als.gov.hk). Rows with lat/lon already filled are kept
    as-is, so a wrong geocode can be fixed by typing coordinates into the CSV.
  * Hongkong Post iPostal Stations (official CSDI dataset)
Candidate sites (OpenStreetMap)
  MTR exits, markets, malls, community/sports facilities, supermarkets, convenience stores,
  bus termini, post offices. Plus one in-building / estate-lobby candidate at every demand node.
  Near-duplicates (< CANDIDATE_DEDUP_M) are collapsed, keeping the
  higher-priority category.

All sites inside the study boundary OR within SUPPLY_BUFFER_M of it are kept (flag in_study_area).

Outputs: data/processed/supply_sites.csv / .gpkg, data/processed/geocode_report.csv
"""
from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
import requests

from common import (CANDIDATE_DEDUP_M, HK1980, HTTP_HEADERS, MANUAL, PROCESSED, RAW, WGS84,
                    save_json)

ALS_URL = "https://www.als.gov.hk/lookup"
ALS_CACHE = RAW / "als_geocode_cache.json"
MIN_ALS_SCORE = 60  # below this the match is flagged for manual checking

# (category, priority) - lower number wins when two candidates are within CANDIDATE_DEDUP_M
CATEGORY_RULES = [
    ("mtr_exit", 1, lambda r: r.get("railway") == "subway_entrance"),
    ("wet_market", 2, lambda r: r.get("amenity") == "marketplace"),
    ("shopping_mall", 3, lambda r: r.get("shop") in ("mall", "department_store")),
    ("community_facility", 4, lambda r: r.get("amenity") in ("community_centre", "social_centre",
                                                             "library", "townhall")
                                         or r.get("leisure") == "sports_centre"),
    ("post_office", 5, lambda r: r.get("amenity") == "post_office"),
    ("supermarket", 6, lambda r: r.get("shop") == "supermarket"),
    ("convenience_store", 7, lambda r: r.get("shop") == "convenience"),
    ("bus_terminus", 8, lambda r: r.get("amenity") == "bus_station"),
]


# ------------------------------------------------------------------------------------ geocoding
def als_lookup(query: str, cache: dict) -> dict | None:
    if query not in cache:
        r = requests.get(ALS_URL, params={"q": query, "n": 1},
                         headers={**HTTP_HEADERS, "Accept": "application/json",
                                  "Accept-Language": "en"}, timeout=30)
        r.raise_for_status()
        cache[query] = r.json()
    hits = cache[query].get("SuggestedAddress") or []
    if not hits:
        return None
    a = hits[0]["Address"]["PremisesAddress"]
    gi = a["GeospatialInformation"]
    eng = a.get("EngPremisesAddress", {})
    street = eng.get("EngStreet", {})
    matched = " ".join(str(x) for x in [eng.get("BuildingName", ""), street.get("BuildingNoFrom", ""),
                                        street.get("StreetName", ""),
                                        eng.get("EngEstate", {}).get("EstateName", "")] if x)
    return dict(lat=float(gi["Latitude"]), lon=float(gi["Longitude"]),
                score=hits[0]["ValidationInformation"]["Score"], matched=matched.strip())


GENERIC_WORDS = {"KENNEDY", "TOWN", "SAI", "WAN", "YING", "PUN", "SHEK", "TONG", "TSUI", "STREET",
                 "ROAD", "BUILDING", "PHASE", "HONG", "KONG", "G/F", "SHOP"}


def street_agrees(query: str, matched: str) -> bool:
    """ALS sometimes returns a high-score match on the wrong street (e.g. '7 North Street Kennedy
    Town' -> '7 Praya, Kennedy Town'). Require at least one distinctive query word in the match."""
    words = {w.strip(".,'") for w in query.upper().replace("'S", "").split()}
    words = {w for w in words if w and not w.replace("-", "").isdigit() and w not in GENERIC_WORDS}
    m = matched.upper().replace("'S", "")
    return not words or any(w in m for w in words)


def load_manual_sites() -> gpd.GeoDataFrame:
    df = pd.read_csv(MANUAL / "existing_lockers.csv", dtype=str)
    df = df[df["site_code"].fillna("") != "TODO"].copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce").astype(float)
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce").astype(float)
    cache = json.loads(ALS_CACHE.read_text()) if ALS_CACHE.exists() else {}
    report = []
    for i, r in df.iterrows():
        if pd.notna(r["lat"]) and pd.notna(r["lon"]):
            report.append(dict(name=r["name"], query="(manual coordinates)", score=None, matched=""))
            continue
        g = als_lookup(r["geocode_query"], cache)
        if g is None:
            print(f"   !! no ALS match for {r['name']!r} - fill lat/lon manually")
            continue
        df.loc[i, ["lat", "lon"]] = [g["lat"], g["lon"]]
        report.append(dict(name=r["name"], query=r["geocode_query"], score=g["score"],
                           matched=g["matched"],
                           check=g["score"] < MIN_ALS_SCORE or not street_agrees(r["geocode_query"],
                                                                                g["matched"])))
    save_json(cache, ALS_CACHE)
    bad = [x["name"] for x in report if x.get("check")]
    if bad:
        print("   !! geocodes to check (low score or street mismatch):", bad)
    pd.DataFrame(report).to_csv(PROCESSED / "geocode_report.csv", index=False)
    df = df.dropna(subset=["lat", "lon"])
    df["lat"], df["lon"] = df["lat"].astype(float), df["lon"].astype(float)
    return gpd.GeoDataFrame(
        pd.DataFrame(dict(
            site_class="existing", operator=df["operator"], category=df["site_type"],
            name=df["name"], address=df["address_en"], ref=df["site_code"],
            public_access=df["public_access"], include_baseline=df["include_baseline"],
            source=df["source"], notes=df["notes"])),
        geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=WGS84)


def load_ipostal() -> gpd.GeoDataFrame:
    g = gpd.read_file(RAW / "ipostal_stations.geojson").to_crs(WGS84)
    return gpd.GeoDataFrame(pd.DataFrame(dict(
        site_class="existing", operator="Hongkong Post (iPostal)", category="locker",
        name=g["NameEN"], address=g["AddressEN"], ref=g["iPostalStationCodeEN"],
        public_access="yes", include_baseline="yes", source="CSDI hkpo iPostal Station dataset",
        notes="Opening hours (weekday): " + g["OpenHourWeekDayEN"].fillna("?"))),
        geometry=g.geometry.values, crs=WGS84)


# ----------------------------------------------------------------------------------- candidates
def load_candidates() -> gpd.GeoDataFrame:
    p = gpd.read_file(RAW / "osm_pois.gpkg").to_crs(HK1980)
    rec = []
    for _, r in p.iterrows():
        rd = {k: (v if isinstance(v, str) else None) for k, v in r.items() if k != "geometry"}
        for cat, prio, rule in CATEGORY_RULES:
            if rule(rd):
                name = rd.get("name:en") or rd.get("name") or rd.get("brand:en") or rd.get("brand")
                rec.append(dict(site_class="candidate", operator="", category=cat, priority=prio,
                                name=name or cat.replace("_", " "),
                                address=" ".join(x for x in [rd.get("addr:housenumber"),
                                                             rd.get("addr:street")] if x),
                                ref=f"osm:{rd.get('element')}/{rd.get('id')}",
                                public_access="yes", include_baseline="no",
                                source="OpenStreetMap", notes="",
                                geometry=r.geometry.representative_point()))
                break
    return gpd.GeoDataFrame(rec, geometry="geometry", crs=HK1980)


def load_building_candidates() -> gpd.GeoDataFrame:
    """Every demand node is also a candidate site: an in-building / estate-lobby locker (cf. SF's
    residents-only locker at Cayman Rise). Guarantees the 100%-coverage SCLP is feasible
    (J contains I). Filter category == 'residential_building' out to exclude them."""
    d = gpd.read_file(PROCESSED / "demand_nodes.gpkg").to_crs(HK1980)
    names = d["example_buildings"].fillna("").str.split(";").str[0].str.strip()
    return gpd.GeoDataFrame(pd.DataFrame(dict(
        site_class="candidate", operator="", category="residential_building", priority=9,
        name=[n if n else f"Residential cluster {i}" for n, i in zip(names, d["node_id"])],
        address="", ref="demand:" + d["node_id"], public_access="residents",
        include_baseline="no", source="demand node (step 2)",
        notes="in-building locker at demand node " + d["node_id"])),
        geometry=d.geometry.values, crs=HK1980)


def dedupe(sites: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Greedy: keep sites in priority order, drop any later one within CANDIDATE_DEDUP_M."""
    sites = sites.sort_values(["priority", "name"]).reset_index(drop=True)
    kept = []
    for i, s in sites.iterrows():
        if all(s.geometry.distance(sites.geometry[k]) >= CANDIDATE_DEDUP_M for k in kept):
            kept.append(i)
    return sites.loc[kept]


if __name__ == "__main__":
    boundary = gpd.read_file(PROCESSED / "study_area.gpkg", layer="boundary")
    buffer = gpd.read_file(PROCESSED / "study_area.gpkg", layer="supply_buffer")

    existing = pd.concat([load_manual_sites(), load_ipostal()], ignore_index=True).to_crs(HK1980)
    existing["priority"] = 0  # existing sites always win de-duplication
    candidates = pd.concat([load_candidates(), load_building_candidates()], ignore_index=True)

    sites = gpd.GeoDataFrame(pd.concat([existing, candidates], ignore_index=True), crs=HK1980)
    sites = sites[sites.within(buffer.geometry.iloc[0])]
    n_before = (sites.site_class == "candidate").sum()
    # existing sites are never dropped; candidates are de-duplicated against them and each other
    sites = pd.concat([sites[sites.site_class == "existing"], dedupe(sites)
                       .query("site_class == 'candidate'")], ignore_index=True)
    sites = gpd.GeoDataFrame(sites, geometry="geometry", crs=HK1980)
    sites["in_study_area"] = sites.within(boundary.geometry.iloc[0])
    sites["dist_to_boundary_m"] = sites.distance(boundary.geometry.iloc[0].boundary).round(0)
    sites.loc[sites.in_study_area, "dist_to_boundary_m"] = 0
    sites = sites.sort_values(["site_class", "priority", "name"], ascending=[False, True, True])
    sites.insert(0, "site_id", [f"S{k:03d}" for k in range(1, len(sites) + 1)])
    ll = sites.to_crs(WGS84)
    sites["lat"], sites["lon"] = ll.geometry.y.round(6), ll.geometry.x.round(6)

    (PROCESSED / "supply_sites.gpkg").unlink(missing_ok=True)
    sites.drop(columns="priority").to_file(PROCESSED / "supply_sites.gpkg")
    cols = ["site_id", "site_class", "operator", "category", "name", "address", "ref", "lat", "lon",
            "in_study_area", "dist_to_boundary_m", "public_access", "include_baseline", "source",
            "notes"]
    sites[cols].to_csv(PROCESSED / "supply_sites.csv", index=False)

    print(f"Existing sites in study area+buffer: {(sites.site_class == 'existing').sum()}")
    print(sites[sites.site_class == "existing"][["site_id", "operator", "name", "in_study_area",
                                                  "dist_to_boundary_m"]].to_string(index=False))
    print(f"Candidates: {n_before} OSM POIs -> {(sites.site_class == 'candidate').sum()} after "
          f"de-duplication ({CANDIDATE_DEDUP_M} m)")
    print(sites[sites.site_class == "candidate"].groupby(["category", "in_study_area"]).size()
          .unstack(fill_value=0).to_string())
    print("\nGeocode report:")
    print(pd.read_csv(PROCESSED / "geocode_report.csv").to_string(index=False))

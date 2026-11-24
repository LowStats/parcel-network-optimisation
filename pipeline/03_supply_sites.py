"""Step 3 - supply nodes (j in J): existing pick-up sites + candidate locker sites.

Existing sites
  * Operator lists for SF Express, Cainiao and JD Logistics (data/raw/operators/pickup-points-*.js,
    compiled by hillmalaya.com.hk from the operators' own sites, with coordinates; last_verified
    in each file header). Classified from the Chinese point name:
        locker           name contains 櫃 (自助櫃 / 智能櫃 / 快遞櫃)
        staffed_station  everything else (驛站, 服務站, 順豐站, OK便利店 counters ...)
        public_access=no name/address says residents / tenants only (只限住戶, 僅限 ...)
  * SF Express stores (staffed) - SF's own store list PDF, geocoded with the HKSAR Address Lookup
    Service (ALS).
  * Hongkong Post iPostal Stations (official CSDI dataset).
  * data/manual/existing_lockers.csv - hand-logged sites; a row is only used if the operator
    lists have no site of the same operator within MANUAL_DUP_M (i.e. it adds something new).
Candidate sites (OpenStreetMap)
  MTR exits, markets, malls, community/sports facilities, supermarkets, convenience stores,
  bus termini, post offices. Plus one in-building / estate-lobby candidate at every demand node.
  Near-duplicates (< CANDIDATE_DEDUP_M) are collapsed, keeping the higher-priority category.

All sites inside the study boundary OR within SUPPLY_BUFFER_M of it are kept (flag in_study_area).
include_baseline = public site of an operator in BASELINE_OPERATORS.

Outputs: data/processed/supply_sites.csv / .gpkg, data/processed/geocode_report.csv
"""
from __future__ import annotations

import json
import re

import geopandas as gpd
import pandas as pd
import requests
from pypdf import PdfReader

from common import (BASELINE_OPERATORS, CANDIDATE_DEDUP_M, HK1980, HTTP_HEADERS, MANUAL,
                    PROCESSED, RAW, WGS84, save_json)

ALS_URL = "https://www.als.gov.hk/lookup"
ALS_CACHE = RAW / "als_geocode_cache.json"
MIN_ALS_SCORE = 60  # below this the match is flagged for manual checking
MANUAL_DUP_M = 75   # a manual row this close to a listed site of the same operator is a duplicate
OPERATOR_JS = {"SF Express": "pickup-points-sf.js", "JD Logistics": "pickup-points-jd.js",
               "Cainiao": "pickup-points-cainiao.js"}
RESTRICTED = re.compile(r"只限住戶|住戶專用|僅限|只限|residents? only|only for (?:the )?residents?|tenants? only|only for tenants?", re.I)
# Rows whose address is not a real Hong Kong location (verified by hand) - excluded
EXCLUDE_CODES = {"6463647": "JD '智能貨架-XY-S兔喜快遞櫃': address 華志路1685號 is not a HK street"}
# JD rows for mainland ZTO 'Tuxi' test lockers on 華志路 (not a Hong Kong street)
EXCLUDE_ADDRESS = re.compile(r"華志路|兔喜")

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
                 "ROAD", "BUILDING", "PHASE", "HONG", "KONG", "G/F", "SHOP", "CENTRAL", "SHEUNG",
                 "WESTERN", "DISTRICT", "ISLAND", "AND", "NO.", "NOS.", "FLOOR", "UNIT"}


def street_agrees(query: str, matched: str) -> bool:
    """ALS sometimes returns a high-score match on the wrong street (e.g. '7 North Street Kennedy
    Town' -> '7 Praya, Kennedy Town'). Require at least one distinctive query word in the match."""
    words = {w.strip(".,'()") for w in query.upper().replace("'S", "").split()}
    words = {w for w in words if len(w) > 2 and not re.sub(r"[-/&]", "", w).isdigit()
             and w not in GENERIC_WORDS}
    m = matched.upper().replace("'S", "")
    return not words or any(w in m for w in words)


def geocode_rows(df: pd.DataFrame, query_col: str, label: str, cache: dict, report: list):
    lat, lon = [], []
    for _, r in df.iterrows():
        if pd.notna(r.get("lat")) and pd.notna(r.get("lon")):
            lat.append(float(r["lat"]))
            lon.append(float(r["lon"]))
            report.append(dict(source=label, name=r["name"], query="(given coordinates)"))
            continue
        g = als_lookup(r[query_col], cache)
        if g is None:
            print(f"   !! no ALS match for {r['name']!r}")
            lat.append(None)
            lon.append(None)
            continue
        lat.append(g["lat"])
        lon.append(g["lon"])
        report.append(dict(source=label, name=r["name"], query=r[query_col], score=g["score"],
                           matched=g["matched"],
                           check=g["score"] < MIN_ALS_SCORE or not street_agrees(r[query_col],
                                                                                g["matched"])))
    return lat, lon


# ------------------------------------------------------------------------------- existing sites
def site_frame(**cols) -> pd.DataFrame:
    return pd.DataFrame(dict(site_class="existing", **cols))


def load_operator_lists() -> gpd.GeoDataFrame:
    parts = []
    for operator, fname in OPERATOR_JS.items():
        text = (RAW / "operators" / fname).read_text(encoding="utf-8")
        verified = re.search(r"last_verified:\s*(\S+)", text)
        rows = pd.DataFrame(json.loads(text[text.index("export default") + 14:].strip()
                                       .rstrip(";")))
        rows = rows[~rows["code"].isin(EXCLUDE_CODES)
                    & ~rows["addr_zh"].fillna("").str.contains(EXCLUDE_ADDRESS)]
        name = rows["name_zh"].fillna("")
        addr = rows["addr_en"].where(rows["addr_en"].fillna("") != "", rows["addr_zh"])
        restricted = (name + " " + rows["addr_zh"].fillna("") + " " + rows["addr_en"].fillna("")
                      ).str.contains(RESTRICTED)
        df = site_frame(
            operator=operator,
            category=name.str.contains("櫃|locker", case=False).map(
                {True: "locker", False: "staffed_station"}),
            name=name, address=addr, ref=rows["code"],
            public_access=restricted.map({True: "no", False: "yes"}),
            source=f"operators/{fname} (last_verified {verified.group(1) if verified else '?'})",
            notes="hours: " + rows["hours"].fillna("?"))
        parts.append(gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(rows["lng"], rows["lat"]),
                                      crs=WGS84))
    return pd.concat(parts, ignore_index=True)


def parse_sf_store_pdf() -> pd.DataFrame:
    """SF store PDF: '<area heading>' / address lines / 'Code: X' / 'Operating hours' / 'Sun ...'."""
    lines = []
    for p in PdfReader(RAW / "operators" / "sf_store_hk_island_20261029.pdf").pages:
        lines += [l.strip() for l in (p.extract_text() or "").splitlines() if l.strip()]
    rows, buf, hours = [], [], ""
    for l in lines:
        if re.match(r"^Page \d+$|^SF Store Locations|^Hong Kong · ", l):
            continue
        if l.startswith("Code:"):
            # an area heading has no comma/digit and sits at the start of the buffer
            while buf and "," not in buf[0] and not re.search(r"\d", buf[0]) and len(buf) > 1:
                buf.pop(0)
            rows.append(dict(address=" ".join(buf), code=l.split(":", 1)[1].strip().split()[0]))
            buf = []
        elif l.startswith("Operating hours:"):
            hours = l.split(":", 1)[1].strip()
            if rows:
                rows[-1]["hours"] = hours
        elif l.startswith("Sun & public holidays"):
            continue
        else:
            buf.append(l)
    df = pd.DataFrame(rows)
    # geocode on the premises part only (drop district / island / territory suffixes)
    df["query"] = (df["address"].str.replace(r",?\s*(Central and Western|Wan Chai|Eastern|Southern)"
                                             r" District.*$", "", regex=True)
                   .str.replace(r"^(Shop|Unit|Workshop|Portion)[^,]*,\s*", "", regex=True)
                   .str.replace(r"^[GL]/F,?\s*", "", regex=True))
    return df


def load_sf_stores(cache: dict, report: list) -> gpd.GeoDataFrame:
    df = parse_sf_store_pdf()
    df["name"] = "SF Store " + df["code"]
    lat, lon = geocode_rows(df, "query", "SF store PDF", cache, report)
    df["lat"], df["lon"] = lat, lon
    df = df.dropna(subset=["lat", "lon"])
    out = site_frame(operator="SF Express", category="staffed_station", name=df["name"],
                     address=df["address"], ref=df["code"], public_access="yes",
                     source="operators/sf_store_hk_island_20261029.pdf (SF store list)",
                     notes="SF store; hours: " + df.get("hours", pd.Series("?", index=df.index))
                     .fillna("?"))
    return gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=WGS84)


def load_manual_sites(cache: dict, report: list) -> gpd.GeoDataFrame:
    df = pd.read_csv(MANUAL / "existing_lockers.csv", dtype=str)
    df = df[df["site_code"].fillna("") != "TODO"].copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    lat, lon = geocode_rows(df, "geocode_query", "manual CSV", cache, report)
    df["lat"], df["lon"] = lat, lon
    df = df.dropna(subset=["lat", "lon"])
    out = site_frame(operator=df["operator"], category=df["site_type"], name=df["name"],
                     address=df["address_en"], ref=df["site_code"],
                     public_access=df["public_access"], source=df["source"], notes=df["notes"])
    out["manual_include"] = df["include_baseline"].values
    return gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=WGS84)


def load_ipostal() -> gpd.GeoDataFrame:
    g = gpd.read_file(RAW / "ipostal_stations.geojson").to_crs(WGS84)
    return gpd.GeoDataFrame(site_frame(
        operator="Hongkong Post (iPostal)", category="locker", name=g["NameEN"],
        address=g["AddressEN"], ref=g["iPostalStationCodeEN"], public_access="yes",
        source="CSDI hkpo iPostal Station dataset",
        notes="Opening hours (weekday): " + g["OpenHourWeekDayEN"].fillna("?")),
        geometry=g.geometry.values, crs=WGS84)


def load_existing() -> gpd.GeoDataFrame:
    cache = json.loads(ALS_CACHE.read_text()) if ALS_CACHE.exists() else {}
    report: list = []
    listed = pd.concat([load_operator_lists(), load_sf_stores(cache, report), load_ipostal()],
                       ignore_index=True).to_crs(HK1980)
    manual = load_manual_sites(cache, report).to_crs(HK1980)
    save_json(cache, ALS_CACHE)
    rep = pd.DataFrame(report)
    rep.to_csv(PROCESSED / "geocode_report.csv", index=False)
    if "check" in rep and rep["check"].fillna(False).any():
        print("   !! geocodes to check (low score or street mismatch):",
              rep.loc[rep["check"].fillna(False), "name"].tolist())

    # manual rows only add sites the lists do not already have (same operator within MANUAL_DUP_M)
    keep = []
    for i, m in manual.iterrows():
        same = listed[listed["operator"] == m["operator"]]
        keep.append(same.empty or same.distance(m.geometry).min() > MANUAL_DUP_M)
    manual = manual[keep]
    print(f"Manual CSV rows adding sites not in the operator lists: {len(manual)}"
          + (f" ({', '.join(manual['name'])})" if len(manual) else ""))

    ex = gpd.GeoDataFrame(pd.concat([listed, manual], ignore_index=True), crs=HK1980)
    in_scope = ex["operator"].isin(BASELINE_OPERATORS) & (ex["public_access"] == "yes")
    ex["include_baseline"] = in_scope.map({True: "yes", False: "no"})
    if "manual_include" in ex:  # a hand-logged row can opt itself out (e.g. suspected duplicate)
        ex.loc[ex["manual_include"] == "no", "include_baseline"] = "no"
        ex = ex.drop(columns="manual_include")
    return ex


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
    tree_pts = []
    kept = []
    for i, s in sites.iterrows():
        if all(s.geometry.distance(p) >= CANDIDATE_DEDUP_M for p in tree_pts):
            kept.append(i)
            tree_pts.append(s.geometry)
    return sites.loc[kept]


if __name__ == "__main__":
    boundary = gpd.read_file(PROCESSED / "study_area.gpkg", layer="boundary")
    buffer = gpd.read_file(PROCESSED / "study_area.gpkg", layer="supply_buffer")

    existing = load_existing()
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
    sites = sites.sort_values(["site_class", "priority", "operator", "name"],
                              ascending=[False, True, True, True])
    sites.insert(0, "site_id", [f"S{k:04d}" for k in range(1, len(sites) + 1)])
    ll = sites.to_crs(WGS84)
    sites["lat"], sites["lon"] = ll.geometry.y.round(6), ll.geometry.x.round(6)

    (PROCESSED / "supply_sites.gpkg").unlink(missing_ok=True)
    sites.drop(columns="priority").to_file(PROCESSED / "supply_sites.gpkg")
    cols = ["site_id", "site_class", "operator", "category", "name", "address", "ref", "lat", "lon",
            "in_study_area", "dist_to_boundary_m", "public_access", "include_baseline", "source",
            "notes"]
    sites[cols].to_csv(PROCESSED / "supply_sites.csv", index=False)

    ex = sites[sites.site_class == "existing"]
    print(f"Existing sites in study area + buffer: {len(ex)} "
          f"({ex.in_study_area.sum()} inside the study area)")
    print(ex.groupby(["operator", "category", "public_access"]).size().to_string())
    print(f"Baseline (public, in-scope operators): {(ex.include_baseline == 'yes').sum()}")
    print(f"Candidates: {n_before} -> {(sites.site_class == 'candidate').sum()} after "
          f"de-duplication ({CANDIDATE_DEDUP_M} m)")
    print(sites[sites.site_class == "candidate"].groupby(["category", "in_study_area"]).size()
          .unstack(fill_value=0).to_string())

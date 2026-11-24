"""Step 6 - quality checks on the prepared data (run after steps 1-5).

A. Operator coordinates: the SF / Cainiao / JD coordinates come from a third-party compilation.
   For every existing site in the study area + buffer, the Chinese address is geocoded
   independently with the HKSAR Address Lookup Service (ALS) and the gap is measured.
   -> data/processed/qa_operator_coordinates.csv  (flag = gap > COORD_FLAG_M)

B. Pedestrian network gaps (OSM). Mapping errors here make walking distances too long:
   1. dead-end lifts: highway=elevator nodes attached to only one path (other level unmapped)
   2. path fragments not connected to the main network (OSMnx drops them), near homes
   3. covered escalators / travelators (conveying=*) present in the network (e.g. the
      Central-Mid-Levels escalator) - listed so their inclusion can be confirmed
   4. demand nodes whose nearest sites are close in a straight line but far on foot
   -> data/processed/qa_network_gaps.csv (one row per issue, with openstreetmap.org links)

Run:  .venv/bin/python pipeline/06_qa_checks.py
"""
from __future__ import annotations

import json
import re

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely.geometry import Point

from common import FETCH_BBOX, HK1980, HTTP_HEADERS, PROCESSED, RAW, WGS84, save_json

COORD_FLAG_M = 100     # operator coordinate vs ALS geocode gap that needs checking
NEAR_HOME_M = 150      # report network problems this close to a demand node
DETOUR_FLAG = 3.0      # walk / straight-line ratio to the nearest sites that looks suspicious

S, W, N, E = FETCH_BBOX


# --------------------------------------------------------------------- A. operator coordinates
def clean_zh(addr: str) -> str:
    """Strip territory/district prefixes and operator suffixes so ALS sees the premises."""
    a = re.sub(r"^(港澳)?(中國)?(香港特別行政區)?香港(香港島)?", "", addr or "")
    a = re.sub(r"^(中西區|灣仔區|南區|東區)", "", a)
    a = re.sub(r"[（(][^）)]*[）)]", "", a)  # (菜鳥), (近海旁) ...
    return a.strip(" ,，")


def als_zh(query: str, cache: dict) -> dict | None:
    """ALS lookup returning the Chinese premises address (cached under 'zh:' + query)."""
    import requests
    key = "zh:" + query
    if key not in cache:
        r = requests.get("https://www.als.gov.hk/lookup", params={"q": query, "n": 1},
                         headers={**HTTP_HEADERS, "Accept": "application/json",
                                  "Accept-Language": "zh-Hant"}, timeout=30)
        r.raise_for_status()
        cache[key] = r.json()
    hits = cache[key].get("SuggestedAddress") or []
    if not hits:
        return None
    a = hits[0]["Address"]["PremisesAddress"]
    chi = a.get("ChiPremisesAddress", {})
    street = chi.get("ChiStreet", {}).get("StreetName", "")
    names = [chi.get("BuildingName", ""), chi.get("ChiEstate", {}).get("EstateName", ""), street]
    gi = a["GeospatialInformation"]
    return dict(lat=float(gi["Latitude"]), lon=float(gi["Longitude"]),
                score=hits[0]["ValidationInformation"]["Score"],
                matched=" ".join(n for n in names if n), street=street,
                names=[n for n in names if n])


def check_operator_coordinates() -> pd.DataFrame:
    import importlib.util
    spec = importlib.util.spec_from_file_location("s3", PROCESSED.parent.parent / "pipeline"
                                                  / "03_supply_sites.py")
    s3 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s3)

    sites = pd.read_csv(PROCESSED / "supply_sites.csv")
    ex = sites[(sites.site_class == "existing") & sites.source.str.contains("pickup-points")]
    zh = {}
    for op, f in s3.OPERATOR_JS.items():
        t = (RAW / "operators" / f).read_text(encoding="utf-8")
        for r in json.loads(t[t.index("export default") + 14:].strip().rstrip(";")):
            zh[(op, str(r["code"]))] = r["addr_zh"]
    cache = json.loads(s3.ALS_CACHE.read_text()) if s3.ALS_CACHE.exists() else {}
    rows = []
    for _, r in ex.iterrows():
        q = clean_zh(zh.get((r.operator, str(r.ref)), ""))
        g = als_zh(q, cache) if q else None
        gap = np.nan
        if g:
            a = gpd.GeoSeries([Point(r.lon, r.lat), Point(g["lon"], g["lat"])], crs=WGS84) \
                .to_crs(HK1980)
            gap = a.iloc[0].distance(a.iloc[1])
        agrees = bool(g) and any(n and n in q for n in g["names"])
        rows.append(dict(site_id=r.site_id, operator=r.operator, category=r.category,
                         name=r["name"], als_query=q, als_score=g["score"] if g else None,
                         als_matched=g["matched"] if g else None, als_same_place=agrees,
                         gap_m=round(gap, 1)))
    save_json(cache, s3.ALS_CACHE)
    df = pd.DataFrame(rows)
    # verdict: only a gap on the SAME street/building is evidence against the operator coordinate;
    # if ALS matched a different place the test is inconclusive.
    df["verdict"] = np.select(
        [~df.als_same_place, df.gap_m <= COORD_FLAG_M],
        ["inconclusive (ALS matched a different place)", "ok"],
        default=f"check: same street but > {COORD_FLAG_M} m apart")
    df["flag"] = df.verdict.str.startswith("check")
    df.to_csv(PROCESSED / "qa_operator_coordinates.csv", index=False)
    v = df[df.als_same_place]
    print(f"A. Operator coordinates: ALS matched the same street/building for {len(v)}/{len(df)} "
          f"sites; on those, gap median {v.gap_m.median():.0f} m, 90th pct "
          f"{v.gap_m.quantile(.9):.0f} m, {int(df.flag.sum())} > {COORD_FLAG_M} m")
    print(df.groupby(["operator", "verdict"]).size().to_string())
    return df


# ------------------------------------------------------------------------ B. network gaps
def osm_url(kind: str, oid) -> str:
    return f"https://www.openstreetmap.org/{kind}/{oid}"


def check_network() -> pd.DataFrame:
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW / ".osmnx_cache")
    ox.settings.http_user_agent = HTTP_HEADERS["User-Agent"]
    ox.settings.useful_tags_way = list(ox.settings.useful_tags_way) + ["conveying", "level"]
    # unsimplified, all components. Same Overpass query as step 1, so it is served from the
    # OSMnx cache - which is keyed by server, hence step 1's mirror-fallback helper is reused.
    import importlib.util
    spec = importlib.util.spec_from_file_location("s1", PROCESSED.parent.parent / "pipeline"
                                                  / "01_fetch_raw.py")
    s1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s1)
    Gf = s1.with_overpass_fallback(ox.graph_from_bbox, (W, S, E, N), network_type="walk",
                                   simplify=False, retain_all=True)
    Gf = ox.project_graph(Gf, to_crs=HK1980)
    U = ox.convert.to_undirected(Gf)
    comps = sorted(nx.connected_components(U), key=len, reverse=True)
    main = comps[0]

    demand = gpd.read_file(PROCESSED / "demand_nodes.gpkg").to_crs(HK1980)
    dxy = np.column_stack([demand.geometry.x, demand.geometry.y])

    def nearest_home(x, y):
        d = np.hypot(dxy[:, 0] - x, dxy[:, 1] - y)
        k = int(d.argmin())
        return demand.node_id.iat[k], float(d[k])

    def ll(x, y):
        p = gpd.GeoSeries([Point(x, y)], crs=HK1980).to_crs(WGS84).iloc[0]
        return round(p.y, 6), round(p.x, 6)

    issues = []
    # 1. dead-end lifts
    for n, d in U.nodes(data=True):
        if d.get("highway") == "elevator" and U.degree(n) <= 1:
            hid, hd = nearest_home(d["x"], d["y"])
            lat, lon = ll(d["x"], d["y"])
            issues.append(dict(issue="dead_end_lift", detail="lift attached to only one path - "
                               "its other level is not connected", osm=osm_url("node", n),
                               lat=lat, lon=lon, nearest_node=hid, dist_to_home_m=round(hd)))
    # 2. disconnected fragments near homes
    for c in comps[1:]:
        xs = np.array([U.nodes[n]["x"] for n in c])
        ys = np.array([U.nodes[n]["y"] for n in c])
        cx, cy = xs.mean(), ys.mean()
        hid, hd = nearest_home(cx, cy)
        if hd > NEAR_HOME_M:
            continue
        sub = U.subgraph(c)
        length = sum(d["length"] for *_, d in sub.edges(data=True))
        ways = {d.get("osmid") for *_, d in sub.edges(data=True)}
        hw = {str(d.get("highway")) for *_, d in sub.edges(data=True)}
        w0 = next(iter(ways))
        lat, lon = ll(cx, cy)
        issues.append(dict(issue="disconnected_fragment",
                           detail=f"{len(ways)} way(s), {length:.0f} m of {'/'.join(sorted(hw))} "
                                  f"not joined to the main network",
                           osm=osm_url("way", w0[0] if isinstance(w0, list) else w0),
                           lat=lat, lon=lon, nearest_node=hid, dist_to_home_m=round(hd)))
    # 3. escalators / travelators in the main network
    esc = {}
    for u, v, d in U.edges(data=True):
        if d.get("conveying") and u in main:
            oid = d.get("osmid")
            oid = oid[0] if isinstance(oid, list) else oid
            e = esc.setdefault(oid, dict(len=0.0, x=U.nodes[u]["x"], y=U.nodes[u]["y"],
                                         conv=d.get("conveying"), name=d.get("name")))
            e["len"] += d["length"]
    named = {}
    for oid, e in esc.items():
        key = e["name"] if isinstance(e["name"], str) else "(unnamed: mall / station escalators)"
        g = named.setdefault(key, dict(n=0, len=0.0, oid=oid, x=e["x"], y=e["y"]))
        g["n"] += 1
        g["len"] += e["len"]
    for key, g in named.items():
        lat, lon = ll(g["x"], g["y"])
        hid, hd = nearest_home(g["x"], g["y"])
        issues.append(dict(issue="escalator_in_network",
                           detail=f"{key}: {g['n']} way(s), {g['len']:.0f} m - included in the "
                                  f"main network, treated as walkable both ways",
                           osm=osm_url("way", g["oid"]), lat=lat, lon=lon, nearest_node=hid,
                           dist_to_home_m=round(hd)))
    # 4. detour outliers
    L = pd.read_csv(PROCESSED / "distance_long.csv")
    near = L.sort_values("walk_m").groupby("node_id").head(3)
    agg = near.groupby("node_id").agg(walk=("walk_m", "median"), euclid=("euclid_m", "median"))
    agg["ratio"] = agg.walk / agg.euclid.clip(lower=1)
    bad = agg[(agg.ratio > DETOUR_FLAG) & (agg.euclid < 200)]
    for nid, r in bad.iterrows():
        dn = demand[demand.node_id == nid].iloc[0]
        issues.append(dict(issue="detour_outlier",
                           detail=f"3 nearest sites: {r.euclid:.0f} m straight-line but "
                                  f"{r.walk:.0f} m on foot (x{r.ratio:.1f}) - check for a "
                                  f"missing stair/lift/passage; buildings: "
                                  f"{dn.example_buildings or ''}",
                           osm=f"https://www.openstreetmap.org/#map=19/{dn.lat}/{dn.lon}",
                           lat=dn.lat, lon=dn.lon, nearest_node=nid, dist_to_home_m=0))
    df = pd.DataFrame(issues)
    df.to_csv(PROCESSED / "qa_network_gaps.csv", index=False)
    print(f"B. Network: main component {len(main)} of {U.number_of_nodes()} nodes; "
          f"{len(comps) - 1} detached fragments")
    print(df.issue.value_counts().to_string())
    return df


if __name__ == "__main__":
    check_operator_coordinates()
    check_network()

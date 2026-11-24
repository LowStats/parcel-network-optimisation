"""Step 1 - download raw data into data/raw/ (idempotent: skips files that already exist).

Sources
  * C&SD 2021 Population Census, Small Subunit Group (SSG) boundaries + population/households (CSDI WFS)
  * Hongkong Post iPostal Station locations (CSDI WFS)
  * Lands Department building footprints with official storeys / heights (CSDI WFS)
  * OpenStreetMap buildings (footprints, names, levels, building type)
  * OpenStreetMap POIs that can host a locker (MTR exits, markets, malls, convenience stores, ...)
  * OpenStreetMap pedestrian network (OSMnx, network_type="walk": includes steps, footbridges, lifts)
  * Lands Department 5 m Digital Terrain Model (clipped to the study bbox)
  * C&SD 2021 Census District Council District boundaries (study boundary + official totals)
  * Operator pick-up point lists (SF / JD / Cainiao, with coordinates) and SF's store list

Run:  .venv/bin/python pipeline/01_fetch_raw.py [--force]
"""
from __future__ import annotations

import sys

import geopandas as gpd
import osmnx as ox
import requests

from common import (CSDI_DATASETS, save_json, CSDI_WFS, FETCH_BBOX, HK1980, HTTP_HEADERS, OVERPASS_ENDPOINTS,
                    RAW, WGS84)

FORCE = "--force" in sys.argv
KEEP_TAGS = {
    "element", "id", "name", "name:en", "name:zh", "brand", "brand:en", "operator", "operator:en",
    "building", "building:levels", "building:min_level", "height", "building:use", "residential",
    "amenity", "shop", "railway", "public_transport", "station", "leisure", "landuse", "ref",
    "level", "opening_hours", "wheelchair",
}
S, W, N, E = FETCH_BBOX


def fetch_csdi(name: str, bbox: bool, page_size: int = 1000) -> None:
    out = RAW / f"{name}.geojson"
    if out.exists() and not FORCE:
        print(f"[skip] {out.name}")
        return
    ds_id, layer = CSDI_DATASETS[name]
    features, start = [], 0
    while True:  # WFS 2.0 paging (the ArcGIS server caps features per request)
        params = {
            "service": "wfs", "version": "2.0.0", "request": "GetFeature",
            "typeNames": layer, "outputFormat": "GEOJSON", "srsName": WGS84,
            "count": page_size, "startIndex": start,
        }
        if bbox:
            params["bbox"] = f"{S},{W},{N},{E},{WGS84}"
        r = requests.get(CSDI_WFS.format(id=ds_id), params=params, headers=HTTP_HEADERS,
                         timeout=300)
        r.raise_for_status()
        page = r.json().get("features", [])
        features += page
        if len(page) < page_size:
            break
        start += page_size
    save_json({"type": "FeatureCollection", "features": features}, out)
    print(f"[ok]   {out.name}: {len(features)} features")


def with_overpass_fallback(fn, *args, **kwargs):
    """Call an OSMnx downloader, switching Overpass mirror on failure."""
    last = None
    for _ in range(3):
        for ep in OVERPASS_ENDPOINTS:
            ox.settings.overpass_url = ep.rsplit("/", 1)[0]
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - OSMnx raises a variety of HTTP errors
                last = e
                print(f"   osmnx retry ({ep}): {type(e).__name__}: {str(e)[:120]}")
    raise RuntimeError(f"OSMnx download failed: {last}")


def fetch_osm_features(name: str, tags: dict) -> None:
    out = RAW / f"{name}.gpkg"
    if out.exists() and not FORCE:
        print(f"[skip] {out.name}")
        return
    gdf = with_overpass_fallback(ox.features_from_bbox, (W, S, E, N), tags)
    gdf = gdf.reset_index()
    # Keep only the attributes we use: OSM has hundreds of sparse tags, some of which clash
    # case-insensitively (e.g. 'ref:HK' vs 'ref:hk'), which GeoPackage cannot store.
    keep = [c for c in gdf.columns if c in KEEP_TAGS or c.startswith("addr:")]
    gdf = gdf[keep + ["geometry"]]
    for c in keep:
        gdf[c] = gdf[c].map(lambda v: None if v is None or v != v else str(v))
    gdf.to_file(out, driver="GPKG")
    print(f"[ok]   {out.name}: {len(gdf)} features")


def fetch_walk_network() -> None:
    out = RAW / "walk_network.graphml"
    if out.exists() and not FORCE:
        print(f"[skip] {out.name}")
        return
    G = with_overpass_fallback(ox.graph_from_bbox, (W, S, E, N), network_type="walk",
                               simplify=True, retain_all=False)
    ox.save_graphml(G, out)
    print(f"[ok]   {out.name}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")


# Operator pick-up point lists. hillmalaya.com.hk publishes machine-readable lists compiled from
# the operators' own sites (SF, JD, Cainiao; last_verified noted in each file header), with
# coordinates. SF *stores* are not in that list, so SF's own store PDF is used for them.
OPERATOR_FILES = {
    "pickup-points-sf.js": "https://hillmalaya.com.hk/cdn/shop/t/107/assets/pickup-points-sf.js",
    "pickup-points-jd.js": "https://hillmalaya.com.hk/cdn/shop/t/107/assets/pickup-points-jd.js",
    "pickup-points-cainiao.js":
        "https://hillmalaya.com.hk/cdn/shop/t/107/assets/pickup-points-cainiao.js",
    "sf_store_hk_island_20261029.pdf":
        "https://www.birkenstock.com/on/demandware.static/-/Library-Sites-birkenstock-apac/default/"
        "dw3b57c9ec/documents/drop-off-points/20261029-SF-Store-Locations-Hong-Kong-Island.pdf",
}


def fetch_operator_lists() -> None:
    d = RAW / "operators"
    d.mkdir(exist_ok=True)
    for name, url in OPERATOR_FILES.items():
        out = d / name
        if out.exists() and not FORCE:
            print(f"[skip] operators/{name}")
            continue
        r = requests.get(url, headers=HTTP_HEADERS, timeout=180)
        r.raise_for_status()
        out.write_bytes(r.content)
        print(f"[ok]   operators/{name}: {len(r.content) / 1e3:.0f} kB")


DTM_URL = "https://www.landsd.gov.hk/landsd_psi_data/SMO/data/Whole_HK_DTM_5m.zip"


def fetch_dtm() -> None:
    """LandsD 5 m Digital Terrain Model (whole HK, ESRI ASCII grid in HK1980, ~29 MB zip).
    Only a clip covering FETCH_BBOX is kept (the zip itself is git-ignored)."""
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import from_bounds

    out = RAW / "dtm" / "dtm_5m_study_bbox.tif"
    if out.exists() and not FORCE:
        print(f"[skip] {out.name}")
        return
    z = RAW / "dtm" / "Whole_HK_DTM_5m.zip"
    z.parent.mkdir(exist_ok=True)
    if not z.exists():
        r = requests.get(DTM_URL, headers=HTTP_HEADERS, timeout=600)
        r.raise_for_status()
        z.write_bytes(r.content)
    t = Transformer.from_crs(WGS84, HK1980, always_xy=True)
    x0, y0 = t.transform(W, S)
    x1, y1 = t.transform(E, N)
    with rasterio.open(f"/vsizip/{z}/Whole_HK_DTM_5m.asc") as src:
        win = from_bounds(x0, y0, x1, y1, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win)
        prof = src.profile | dict(driver="GTiff", crs=HK1980, height=arr.shape[0],
                                  width=arr.shape[1], transform=src.window_transform(win),
                                  compress="deflate")
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(arr, 1)
    print(f"[ok]   {out.name}: {arr.shape}, elevation {arr[arr > -100].min():.0f}-"
          f"{arr.max():.0f} m")


if __name__ == "__main__":
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW / ".osmnx_cache")
    ox.settings.requests_timeout = 180
    ox.settings.http_user_agent = HTTP_HEADERS["User-Agent"]

    fetch_csdi("census2021_ssg", bbox=True)
    fetch_csdi("ipostal_stations", bbox=False)  # territory-wide; filtered spatially later
    fetch_csdi("census2021_dcd", bbox=False)    # 18 districts: study boundary + official totals
    fetch_csdi("landsd_buildings", bbox=True)

    fetch_osm_features("osm_buildings", {"building": True})
    fetch_osm_features("osm_pois", {
        "railway": ["subway_entrance", "station"],
        "public_transport": ["station"],
        "amenity": ["marketplace", "post_office", "parcel_locker", "community_centre",
                    "library", "bus_station", "townhall", "social_centre"],
        "shop": ["convenience", "supermarket", "mall", "department_store"],
        "leisure": ["sports_centre"],
        "building": ["retail", "commercial"],
    })
    fetch_walk_network()
    fetch_dtm()
    fetch_operator_lists()

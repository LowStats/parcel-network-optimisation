"""Step 1 - download raw data into data/raw/ (idempotent: skips files that already exist).

Sources
  * C&SD 2021 Population Census, Small Subunit Group (SSG) boundaries + population/households (CSDI WFS)
  * Hongkong Post iPostal Station locations (CSDI WFS)
  * OpenStreetMap buildings (footprints, names, levels, building type)
  * OpenStreetMap POIs that can host a locker (MTR exits, markets, malls, convenience stores, ...)
  * OpenStreetMap pedestrian network (OSMnx, network_type="walk": includes steps, footbridges, lifts)

Run:  .venv/bin/python pipeline/01_fetch_raw.py [--force]
"""
from __future__ import annotations

import sys

import geopandas as gpd
import osmnx as ox
import requests

from common import (CSDI_DATASETS, CSDI_WFS, FETCH_BBOX, HTTP_HEADERS, OVERPASS_ENDPOINTS, RAW, WGS84)

FORCE = "--force" in sys.argv
KEEP_TAGS = {
    "element", "id", "name", "name:en", "name:zh", "brand", "brand:en", "operator", "operator:en",
    "building", "building:levels", "building:min_level", "height", "building:use", "residential",
    "amenity", "shop", "railway", "public_transport", "station", "leisure", "landuse", "ref",
    "level", "opening_hours", "wheelchair",
}
S, W, N, E = FETCH_BBOX


def fetch_csdi(name: str, bbox: bool) -> None:
    out = RAW / f"{name}.geojson"
    if out.exists() and not FORCE:
        print(f"[skip] {out.name}")
        return
    ds_id, layer = CSDI_DATASETS[name]
    params = {
        "service": "wfs", "version": "2.0.0", "request": "GetFeature",
        "typeNames": layer, "outputFormat": "GEOJSON", "srsName": WGS84,
    }
    if bbox:
        params["bbox"] = f"{S},{W},{N},{E},{WGS84}"
    r = requests.get(CSDI_WFS.format(id=ds_id), params=params, headers=HTTP_HEADERS, timeout=300)
    r.raise_for_status()
    out.write_bytes(r.content)
    print(f"[ok]   {out.name}: {len(gpd.read_file(out))} features")


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


if __name__ == "__main__":
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW / ".osmnx_cache")
    ox.settings.requests_timeout = 180
    ox.settings.http_user_agent = HTTP_HEADERS["User-Agent"]

    fetch_csdi("census2021_ssg", bbox=True)
    fetch_csdi("ipostal_stations", bbox=False)  # territory-wide; filtered spatially later

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

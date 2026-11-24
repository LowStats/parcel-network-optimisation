"""Shared paths, constants and helpers for the Kennedy Town parcel-locker data pipeline."""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MANUAL = ROOT / "data" / "manual"
PROCESSED = ROOT / "data" / "processed"
FIGURES = ROOT / "figures"
for _d in (RAW, MANUAL, PROCESSED, FIGURES):
    _d.mkdir(parents=True, exist_ok=True)

# Coordinate reference systems
WGS84 = "EPSG:4326"
HK1980 = "EPSG:2326"  # Hong Kong 1980 Grid (metres) - use for all distance/area work

# Bounding box used for raw downloads (generous: study area + buffer for edge effects)
# (south, west, north, east)
FETCH_BBOX = (22.268, 114.108, 22.296, 114.150)

# ---------------------------------------------------------------------------------------------
# Study design parameters (state these in the methodology)
# ---------------------------------------------------------------------------------------------
# Study area = union of these 2021-Census Tertiary Planning Units. TPU 111 = Kennedy Town &
# Mount Davis (contains The Merton, The Belcher's, Kwun Lung Lau, Sai Wan Estate, KET MTR).
# Add "116" to extend east towards Shek Tong Tsui.
STUDY_TPUS = ["111"]

# Supply sites (existing + candidate) are searched up to this far OUTSIDE the boundary, so that
# residents near the edge can be served by lockers just across it (avoids edge-effect bias).
# Set >= the largest coverage radius used in the sensitivity analysis (500 m).
SUPPLY_BUFFER_M = 500

# Demand nodes: residential buildings inside each census SSG are grouped into clusters whose
# diameter is at most this many metres (complete-linkage). Small SSGs (~street blocks) give one
# node; large SSGs that contain several separate estates are split into several nodes.
CLUSTER_MAX_DIAMETER_M = 150
# ...and clusters with fewer households than this are merged into the nearest cluster of the
# same SSG (prevents dozens of near-empty nodes from scattered village houses on Mount Davis).
MIN_NODE_HOUSEHOLDS = 50

# Candidate sites closer than this to a higher-priority site are treated as duplicates.
CANDIDATE_DEDUP_M = 25

# Walking time = network length / speed. 75 m/min (4.5 km/h) => 300 m ~ 4 min.
WALK_SPEED_M_PER_MIN = 75.0
STEPS_TIME_FACTOR = 2.0  # edges tagged highway=steps take this many times longer to walk

# ASSUMPTION - replace with a sourced figure before final results. Average parcels delivered per
# household per day; used only to convert households -> expected daily parcels (lambda_i) for the
# simulation. The ILP coverage weights h_i use households/population, not this rate.
PARCELS_PER_HOUSEHOLD_PER_DAY = 0.30

# CSDI WFS endpoints (official HKSAR open data)
CSDI_WFS = "https://portal.csdi.gov.hk/server/services/common/{id}/MapServer/WFSServer"
CSDI_DATASETS = {
    # 2021 Population Census - statistics & boundaries of Small Subunit Groups (C&SD)
    "census2021_ssg": ("censtatd_rcd_1635933365202_19171", "csdi:SSUG_21C"),
    # iPostal Station information (Hongkong Post)
    "ipostal_stations": ("hkpo_rcd_1639010988627_23643", "csdi:IPOST"),
}

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HTTP_HEADERS = {"User-Agent": "academic-research-script/0.1 (parcel locker location study)"}


def overpass(query: str, rounds: int = 4, wait: float = 10.0) -> list[dict]:
    """Run an Overpass QL query, cycling through public mirrors until one answers."""
    last = None
    for _ in range(rounds):
        for ep in OVERPASS_ENDPOINTS:
            try:
                r = requests.post(ep, data={"data": query}, headers=HTTP_HEADERS, timeout=180)
                if r.status_code == 200:
                    return r.json()["elements"]
                last = f"{ep} -> HTTP {r.status_code}"
            except requests.RequestException as e:  # network hiccup: try next mirror
                last = f"{ep} -> {type(e).__name__}"
            print("   overpass retry:", last)
        time.sleep(wait)
    raise RuntimeError(f"All Overpass endpoints failed (last: {last})")


def save_json(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1))

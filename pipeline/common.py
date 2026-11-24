"""Shared paths, constants and helpers for the Central & Western parcel-locker data pipeline."""
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
# Central & Western District extent is 22.252-22.291 N, 114.111-114.168 E; +~600 m on each side.
FETCH_BBOX = (22.246, 114.104, 22.297, 114.174)

# ---------------------------------------------------------------------------------------------
# Study design parameters (state these in the methodology)
# ---------------------------------------------------------------------------------------------
# Study area = a 2021-Census District Council District (official C&SD boundary). All census Small
# Subunit Groups (SSGs) inside it supply the demand. Mainland part only (Green Island and Little
# Green Island are uninhabited).
STUDY_DISTRICT = "Central and Western"
# Optional override: restrict to these Tertiary Planning Units instead, e.g. ["111"] for the
# original Kennedy Town & Mount Davis study. None = whole district.
STUDY_TPUS: list[str] | None = None

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

# Operators whose public sites form the "existing network" baseline (the study's scope).
# JD Logistics points are kept in the data for reference but not in the baseline.
BASELINE_OPERATORS = {"SF Express", "Cainiao", "Hongkong Post (iPostal)"}

# Walking distances beyond this are not needed for coverage models (max radius 500 m); pairs
# further apart are stored as missing to keep the matrix computation fast.
MAX_PAIR_DISTANCE_M = 2000

# Candidate sites closer than this to a higher-priority site are treated as duplicates.
CANDIDATE_DEDUP_M = 25

# Walking time = network length / speed. 75 m/min (4.5 km/h) => 300 m ~ 4 min.
WALK_SPEED_M_PER_MIN = 75.0
STEPS_TIME_FACTOR = 2.0  # edges tagged highway=steps take this many times longer to walk

# Average e-commerce parcels per household per day -> expected daily parcels (lambda_i) for the
# simulation. The ILP coverage weights h_i use households/population, not this rate.
# Derivation (secondary sources - see README "Simulation parameters"):
#   ~350 million e-commerce parcels delivered in HK in 2025 (Statista figure, cited second-hand)
#   / ~2.7 million domestic households / 365 days  ~= 0.35 parcels per household per day.
# Not every parcel goes to a locker: LOCKER_SHARE scales lambda_i in the simulation.
PARCELS_PER_HOUSEHOLD_PER_DAY = 0.35
PARCEL_RATE_RANGE = (0.20, 0.50)  # low / high values for sensitivity analysis

# ---------------------------------------------------------------------------------------------
# Simulation (Section 3) starting values - all documented in README; tune in the simulation code
# ---------------------------------------------------------------------------------------------
LOCKER_SHARE = 0.30  # ASSUMPTION: share of parcels routed to a locker / pick-up point (vs door)
# Plan's dwell-time distribution (share of parcels collected within 1 / 2 / 3 days)
DWELL_PLAN = {24: 0.60, 48: 0.30, 72: 0.10}
# Empirical benchmark (Urban Freight Lab, Seattle residential lockers): median 4.2 h, mean 12.5 h,
# 84% collected within 24 h, 99% within one week. Use as an alternative / validation scenario.
DWELL_EMPIRICAL = {"median_h": 4.2, "mean_h": 12.5, "share_within_24h": 0.84,
                   "share_within_7d": 0.99}
LOCKER_SIZES = {"small": 40, "medium": 80, "large": 120}  # compartments per bank (plan)
PEAK_MULTIPLIERS = (1.0, 1.5, 2.0)  # baseline, +50 %, +100 % surge (plan, Section 4)

# CSDI WFS endpoints (official HKSAR open data)
CSDI_WFS = "https://portal.csdi.gov.hk/server/services/common/{id}/MapServer/WFSServer"
CSDI_DATASETS = {
    # 2021 Population Census - statistics & boundaries of Small Subunit Groups (C&SD)
    "census2021_ssg": ("censtatd_rcd_1635933365202_19171", "csdi:SSUG_21C"),
    # iPostal Station information (Hongkong Post)
    "ipostal_stations": ("hkpo_rcd_1639010988627_23643", "csdi:IPOST"),
    # 2021 Population Census - statistics & boundaries of District Council Districts (C&SD)
    "census2021_dcd": ("censtatd_rcd_1635932856094_19982", "csdi:DC_21C"),
    # Lands Department building footprints (official): names, storeys, base/top height
    "landsd_buildings": ("landsd_rcd_1637211194312_35158", "csdi:Building"),
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

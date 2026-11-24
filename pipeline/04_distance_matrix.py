"""Step 4 - pedestrian walking distance / time matrix d_ij between demand nodes i and sites j.

Network: OpenStreetMap pedestrian graph (OSMnx network_type="walk"), which includes stairs,
footbridges, subways, lifts/escalators where mapped, and excludes motorways. It is treated as
undirected (pedestrians can walk both ways along one-way streets).

Distance
  d_ij = (straight-line access from point i to its nearest network node)
         + shortest network path length
         + (access from site j's nearest network node to j)
  Pairs <= CANDIDATE_DEDUP_M apart (same building / next door) use the straight-line distance.

Time (slope-aware)
  Every network node gets an elevation from the LandsD 5 m DTM. Each edge's walking speed follows
  Tobler's hiking function, rescaled so that flat ground = WALK_SPEED_M_PER_MIN:
      v(s) = v_flat * exp(-3.5 * |s + 0.05|) / exp(-3.5 * 0.05),   s = rise / run
  Because residents walk to the locker AND back, each edge's time is the mean of its uphill and
  downhill traversal times (= half the round trip), which keeps the graph undirected.
  Tunnel/subway edges are treated as flat (the DTM shows the ground surface above them) and grades
  are capped at +/-MAX_GRADE to damp DTM noise on very short edges.
  walk_min_flat (flat speed, stairs x STEPS_TIME_FACTOR) is kept for comparison.
  climb_m = total ascent along the shortest path when walking from home to the site.

Outputs (data/processed/)
  distance_long.csv        one row per (node_id, site_id): walk_m, walk_min, walk_min_flat,
                           climb_m, euclid_m, detour_ratio
  distance_matrix_m.csv    wide matrix of walking distance (rows = demand nodes, cols = sites)
  walk_time_matrix_min.csv wide matrix of slope-aware walking time
  snapping_report.csv      distance of each point to the network + its ground elevation (QA)
"""
from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio

from common import (CANDIDATE_DEDUP_M, HK1980, PROCESSED, RAW, STEPS_TIME_FACTOR,
                    WALK_SPEED_M_PER_MIN)

SNAP_WARN_M = 75  # points further than this from the network are flagged for checking
MAX_GRADE = 0.5   # cap on |rise/run| per edge (steep stairs are ~0.5-0.7)
MIN_EDGE_FOR_GRADE_M = 5.0  # shorter edges: DTM cell size, grade unreliable -> treat as flat
DTM = RAW / "dtm" / "dtm_5m_kennedy_town.tif"


def tobler_speed(s: float) -> float:
    return WALK_SPEED_M_PER_MIN * np.exp(-3.5 * abs(s + 0.05)) / np.exp(-3.5 * 0.05)


def sample_dtm(xs, ys) -> np.ndarray:
    with rasterio.open(DTM) as src:
        z = np.array([v[0] for v in src.sample(zip(xs, ys))], dtype=float)
    return np.where(z < -50, np.nan, z)


def load_graph() -> nx.Graph:
    G = ox.load_graphml(RAW / "walk_network.graphml")
    G = ox.project_graph(G, to_crs=HK1980)
    G = ox.convert.to_undirected(G)
    ids = list(G.nodes)
    z = sample_dtm([G.nodes[n]["x"] for n in ids], [G.nodes[n]["y"] for n in ids])
    for n, zz in zip(ids, z):
        G.nodes[n]["elev_m"] = zz
    for u, v, d in G.edges(data=True):
        hw = d.get("highway", "")
        hw = hw if isinstance(hw, list) else [hw]
        is_steps = "steps" in hw
        tunnel = str(d.get("tunnel", "no")) not in ("no", "nan", "None")
        L = d["length"]
        d["walk_min_flat"] = L / WALK_SPEED_M_PER_MIN * (STEPS_TIME_FACTOR if is_steps else 1)
        rise = G.nodes[v]["elev_m"] - G.nodes[u]["elev_m"]
        if tunnel or L < MIN_EDGE_FOR_GRADE_M or np.isnan(rise):
            s = 0.0
        else:
            s = float(np.clip(rise / L, -MAX_GRADE, MAX_GRADE))
        d["grade"] = s
        d["walk_min"] = 0.5 * (L / tobler_speed(s) + L / tobler_speed(-s))
    return G


def snap(G, gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray]:
    nodes, dist = ox.distance.nearest_nodes(G, gdf.geometry.x.values, gdf.geometry.y.values,
                                            return_dist=True)
    return np.asarray(nodes), np.asarray(dist)


def path_ascent(G, path: list) -> float:
    z = np.array([G.nodes[n]["elev_m"] for n in path])
    dz = np.diff(z)
    return float(np.nansum(dz[dz > 0]))


if __name__ == "__main__":
    demand = gpd.read_file(PROCESSED / "demand_nodes.gpkg").to_crs(HK1980)
    sites = gpd.read_file(PROCESSED / "supply_sites.gpkg").to_crs(HK1980)
    G = load_graph()
    grades = np.array([abs(d["grade"]) for *_, d in G.edges(data=True)])
    print(f"Walk network: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges; "
          f"edges steeper than 10%: {(grades > .10).mean():.0%}")

    d_node, d_snap = snap(G, demand)
    s_node, s_snap = snap(G, sites)

    rows = []
    for j, site in sites.iterrows():
        len_from, paths = nx.single_source_dijkstra(G, s_node[j], weight="length")
        min_from = nx.single_source_dijkstra_path_length(G, s_node[j], weight="walk_min")
        flat_from = nx.single_source_dijkstra_path_length(G, s_node[j], weight="walk_min_flat")
        for i, dn in demand.iterrows():
            access_m = d_snap[i] + s_snap[j]
            p = paths.get(d_node[i])
            rows.append(dict(
                node_id=dn.node_id, site_id=site.site_id,
                walk_m=len_from.get(d_node[i], np.inf) + access_m,
                walk_min=min_from.get(d_node[i], np.inf) + access_m / WALK_SPEED_M_PER_MIN,
                walk_min_flat=flat_from.get(d_node[i], np.inf) + access_m / WALK_SPEED_M_PER_MIN,
                climb_m=path_ascent(G, p[::-1]) if p else np.nan,  # home -> site
                euclid_m=dn.geometry.distance(site.geometry)))
    long = pd.DataFrame(rows)
    # Same building / next door (<= CANDIDATE_DEDUP_M apart): use the straight-line distance -
    # routing both points out to the street network and back would overstate the walk.
    same = long["euclid_m"] <= CANDIDATE_DEDUP_M
    long.loc[same, "walk_m"] = long.loc[same, "euclid_m"]
    for col in ("walk_min", "walk_min_flat"):
        long.loc[same, col] = long.loc[same, "euclid_m"] / WALK_SPEED_M_PER_MIN
    long.loc[same, "climb_m"] = 0.0
    long["detour_ratio"] = long["walk_m"] / long["euclid_m"].clip(lower=1)
    long = long.round({"walk_m": 1, "walk_min": 2, "walk_min_flat": 2, "climb_m": 1,
                       "euclid_m": 1, "detour_ratio": 3})
    long.to_csv(PROCESSED / "distance_long.csv", index=False)
    long.pivot(index="node_id", columns="site_id", values="walk_m") \
        .to_csv(PROCESSED / "distance_matrix_m.csv")
    long.pivot(index="node_id", columns="site_id", values="walk_min") \
        .to_csv(PROCESSED / "walk_time_matrix_min.csv")

    snap_rep = pd.concat([
        pd.DataFrame(dict(kind="demand", id=demand.node_id, snap_m=d_snap.round(1),
                          elev_m=sample_dtm(demand.geometry.x, demand.geometry.y).round(1))),
        pd.DataFrame(dict(kind="site", id=sites.site_id, snap_m=s_snap.round(1),
                          elev_m=sample_dtm(sites.geometry.x, sites.geometry.y).round(1)))])
    snap_rep["flag"] = snap_rep["snap_m"] > SNAP_WARN_M
    snap_rep.to_csv(PROCESSED / "snapping_report.csv", index=False)

    # ---- summary
    print(f"Matrix: {demand.shape[0]} demand nodes x {sites.shape[0]} sites = {len(long)} pairs; "
          f"unreachable pairs: {np.isinf(long.walk_m).sum()}")
    print(f"Snapping: median {np.median(d_snap):.0f} m (demand), {np.median(s_snap):.0f} m (sites); "
          f"flagged > {SNAP_WARN_M} m: {int(snap_rep.flag.sum())}")
    dz = snap_rep[snap_rep.kind == "demand"].elev_m
    print(f"Demand-node ground elevation: {dz.min():.0f}-{dz.max():.0f} m (median {dz.median():.0f})")
    near = long[long.euclid_m <= 500]
    print(f"Detour ratio walk/straight-line for pairs within 500 m: median "
          f"{near.detour_ratio.median():.2f}, 90th pct {near.detour_ratio.quantile(.9):.2f}")
    euc300 = (long.euclid_m <= 300).sum()
    walk300 = (long.walk_m <= 300).sum()
    print(f"Pairs within 300 m: straight-line {euc300} vs walking {walk300} "
          f"-> straight-line overstates 300 m coverage pairs by {euc300 / max(walk300, 1) - 1:.0%}")
    w = long[long.walk_m <= 500]
    print(f"Pairs within 500 m walk: slope-aware time is on average "
          f"{(w.walk_min / w.walk_min_flat.clip(lower=.01)).mean() - 1:+.0%} vs flat time; "
          f"median climb home->site {w.climb_m.median():.0f} m (90th pct {w.climb_m.quantile(.9):.0f} m)")
    for D in (200, 300, 400, 500):
        cov = long[long.walk_m <= D].groupby("node_id").size()
        print(f"  D={D} m: demand nodes with >=1 site in reach: {len(cov)}/{len(demand)}")

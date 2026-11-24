"""Step 4 - pedestrian walking distance / time matrix d_ij between demand nodes i and sites j.

Network: OpenStreetMap pedestrian graph (OSMnx network_type="walk"), which includes stairs,
footbridges, subways, lifts/escalators where mapped, and excludes motorways. It is treated as
undirected (pedestrians can walk both ways along one-way streets).

d_ij = (straight-line access from point i to its nearest network node)
       + shortest network path length
       + (access from site j's nearest network node to j)

Pairs <= CANDIDATE_DEDUP_M apart (same building / next door) use the straight-line distance.

Walking time uses WALK_SPEED_M_PER_MIN, with edges tagged highway=steps slowed by
STEPS_TIME_FACTOR. NOTE: gradient (slope) effort is not yet modelled - see README.

Outputs (data/processed/)
  distance_long.csv        one row per (node_id, site_id): walk_m, walk_min, euclid_m, detour
  distance_matrix_m.csv    wide matrix of walking distance (rows = demand nodes, cols = sites)
  walk_time_matrix_min.csv wide matrix of walking time
  snapping_report.csv      how far each point is from the network (QA)
"""
from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

from common import CANDIDATE_DEDUP_M, HK1980, PROCESSED, RAW, STEPS_TIME_FACTOR, WALK_SPEED_M_PER_MIN

SNAP_WARN_M = 75  # points further than this from the network are flagged for checking


def load_graph() -> nx.Graph:
    G = ox.load_graphml(RAW / "walk_network.graphml")
    G = ox.project_graph(G, to_crs=HK1980)
    G = ox.convert.to_undirected(G)
    for _, _, d in G.edges(data=True):
        hw = d.get("highway", "")
        is_steps = "steps" in (hw if isinstance(hw, list) else [hw])
        d["walk_min"] = d["length"] / WALK_SPEED_M_PER_MIN * (STEPS_TIME_FACTOR if is_steps else 1)
    return G


def snap(G, gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray]:
    nodes, dist = ox.distance.nearest_nodes(G, gdf.geometry.x.values, gdf.geometry.y.values,
                                            return_dist=True)
    return np.asarray(nodes), np.asarray(dist)


if __name__ == "__main__":
    demand = gpd.read_file(PROCESSED / "demand_nodes.gpkg").to_crs(HK1980)
    sites = gpd.read_file(PROCESSED / "supply_sites.gpkg").to_crs(HK1980)
    G = load_graph()
    print(f"Walk network: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    d_node, d_snap = snap(G, demand)
    s_node, s_snap = snap(G, sites)

    rows = []
    for j, site in sites.iterrows():
        len_from = nx.single_source_dijkstra_path_length(G, s_node[j], weight="length")
        min_from = nx.single_source_dijkstra_path_length(G, s_node[j], weight="walk_min")
        for i, dn in demand.iterrows():
            net_m = len_from.get(d_node[i], np.inf)
            net_min = min_from.get(d_node[i], np.inf)
            access_m = d_snap[i] + s_snap[j]
            rows.append(dict(node_id=dn.node_id, site_id=site.site_id,
                             walk_m=net_m + access_m,
                             walk_min=net_min + access_m / WALK_SPEED_M_PER_MIN,
                             euclid_m=dn.geometry.distance(site.geometry)))
    long = pd.DataFrame(rows)
    # Same building / next door (<= CANDIDATE_DEDUP_M apart): use the straight-line distance -
    # routing both points out to the street network and back would overstate the walk.
    same = long["euclid_m"] <= CANDIDATE_DEDUP_M
    long.loc[same, "walk_m"] = long.loc[same, "euclid_m"]
    long.loc[same, "walk_min"] = long.loc[same, "euclid_m"] / WALK_SPEED_M_PER_MIN
    long["detour_ratio"] = long["walk_m"] / long["euclid_m"].clip(lower=1)
    long = long.round({"walk_m": 1, "walk_min": 2, "euclid_m": 1, "detour_ratio": 3})
    long.to_csv(PROCESSED / "distance_long.csv", index=False)
    long.pivot(index="node_id", columns="site_id", values="walk_m") \
        .to_csv(PROCESSED / "distance_matrix_m.csv")
    long.pivot(index="node_id", columns="site_id", values="walk_min") \
        .to_csv(PROCESSED / "walk_time_matrix_min.csv")

    snap_rep = pd.concat([
        pd.DataFrame(dict(kind="demand", id=demand.node_id, snap_m=d_snap.round(1))),
        pd.DataFrame(dict(kind="site", id=sites.site_id, snap_m=s_snap.round(1)))])
    snap_rep["flag"] = snap_rep["snap_m"] > SNAP_WARN_M
    snap_rep.to_csv(PROCESSED / "snapping_report.csv", index=False)

    # ---- summary
    print(f"Matrix: {demand.shape[0]} demand nodes x {sites.shape[0]} sites = {len(long)} pairs; "
          f"unreachable pairs: {np.isinf(long.walk_m).sum()}")
    print(f"Snapping: median {np.median(d_snap):.0f} m (demand), {np.median(s_snap):.0f} m (sites); "
          f"flagged > {SNAP_WARN_M} m: {int(snap_rep.flag.sum())}")
    near = long[long.euclid_m <= 500]
    print(f"Detour ratio walk/straight-line for pairs within 500 m: median "
          f"{near.detour_ratio.median():.2f}, 90th pct {near.detour_ratio.quantile(.9):.2f}")
    euc300 = (long.euclid_m <= 300).sum()
    walk300 = (long.walk_m <= 300).sum()
    print(f"Pairs within 300 m: straight-line {euc300} vs walking {walk300} "
          f"-> straight-line overstates 300 m coverage pairs by {euc300 / max(walk300, 1) - 1:.0%}")
    for D in (200, 300, 400, 500):
        cov = long[long.walk_m <= D].groupby("node_id").size()
        print(f"  D={D} m: demand nodes with >=1 site in reach: {len(cov)}/{len(demand)}")

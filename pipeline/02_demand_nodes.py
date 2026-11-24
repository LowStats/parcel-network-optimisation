"""Step 2 - study boundary + demand nodes (i in I) with demand weights h_i.

Method
  1. Study boundary = dissolve of 2021 Census Small Subunit Groups (SSGs) in STUDY_TPUS
     (mainland part only - offshore islets dropped).
  2. Residential buildings (OSM footprints) are assigned to the SSG containing them.
     Gross floor area (GFA) proxy = footprint area x storeys (missing storeys imputed with the
     median of residential buildings in the same SSG, else the study-area median).
  3. Within each SSG, buildings are grouped by complete-linkage clustering so that no cluster is
     wider than CLUSTER_MAX_DIAMETER_M; clusters with < MIN_NODE_HOUSEHOLDS are merged into the
     nearest cluster of the same SSG. Each cluster becomes one demand node, located on the
     building nearest the GFA-weighted centroid of the cluster.
  4. The SSG's official 2021 Census households (dh) and population (t_pop) are split across its
     clusters in proportion to GFA. Totals therefore match the Census exactly. Student halls /
     hostels / dormitories receive population but not (domestic) households.

Outputs (data/processed/)
  study_area.gpkg          layers: boundary, supply_buffer, ssg
  demand_buildings.gpkg    residential buildings with allocated households
  demand_nodes.csv/.gpkg   demand nodes: id, lat/lon, households, population, daily parcels
"""
from __future__ import annotations

import re

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage

from common import (CLUSTER_MAX_DIAMETER_M, MIN_NODE_HOUSEHOLDS, HK1980, PARCELS_PER_HOUSEHOLD_PER_DAY, PROCESSED, RAW,
                    STUDY_TPUS, SUPPLY_BUFFER_M, WGS84)

RESIDENTIAL = {"apartments", "residential", "house", "dormitory", "terrace", "detached",
               "semidetached_house", "yes"}
# 'yes' (untyped) footprints are kept unless OSM says they are clearly something else
NON_RESIDENTIAL_NAME = re.compile(
    r"office|complex|depot|station|school|college|market|reservoir|social centre|community|church|"
    r"temple|clinic|hospital|library|police|fire|ambulance|sports|pumping|substation|toilet|"
    r"government|services building|museum|kindergarten", re.I)
DORMITORY_NAME = re.compile(r"\bhall\b|hostel|dormitory|宿舍|舍堂", re.I)
NON_RESIDENTIAL_HINTS = {"school", "university", "hospital", "industrial", "warehouse", "service",
                         "roof", "commercial", "retail", "office", "church", "temple", "garage"}


def load_ssg() -> gpd.GeoDataFrame:
    g = gpd.read_file(RAW / "census2021_ssg.geojson").to_crs(HK1980)
    g["tpu"] = g["ssbg_eng"].str[:3]
    for c in ("dh", "oq", "t_pop"):
        g[c] = pd.to_numeric(g[c], errors="coerce").fillna(0)
    g = g[g["tpu"].isin(STUDY_TPUS)].copy()
    g = g.rename(columns={"ssbg_eng": "ssg", "dh": "households", "t_pop": "population"})
    return g[["ssg", "tpu", "households", "population", "geometry"]].reset_index(drop=True)


def load_residential_buildings(ssg: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    b = gpd.read_file(RAW / "osm_buildings.gpkg").to_crs(HK1980)
    b = b[b.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()
    b = b[b["building"].isin(RESIDENTIAL)]
    hint = b.get("building:use", pd.Series(index=b.index, dtype=object)).fillna("")
    b = b[~((b["building"] == "yes") & (b.get("amenity", "").fillna("").isin(NON_RESIDENTIAL_HINTS)
                                         | hint.isin(NON_RESIDENTIAL_HINTS)))]
    names = b["name:en"].fillna("") + " " + b["name"].fillna("")
    b = b[~names.str.contains(NON_RESIDENTIAL_NAME)]
    b["footprint_m2"] = b.area
    b = b[b["footprint_m2"] >= 20]  # drop sheds / kiosks
    b["storeys"] = pd.to_numeric(b["building:levels"], errors="coerce")
    b["storeys_imputed"] = b["storeys"].isna()

    pts = b.set_geometry(b.representative_point())
    j = gpd.sjoin(pts[["geometry"]], ssg[["ssg", "geometry"]], predicate="within", how="inner")
    b = b.loc[j.index].assign(ssg=j["ssg"].values)
    med_ssg = b.groupby("ssg")["storeys"].transform("median")
    b["storeys"] = b["storeys"].fillna(med_ssg).fillna(b["storeys"].median())
    b["gfa_m2"] = b["footprint_m2"] * b["storeys"]
    names = b["name:en"].fillna("") + " " + b["name"].fillna("")
    b["is_dormitory"] = (b["building"] == "dormitory") | names.str.contains(DORMITORY_NAME)
    keep = ["id", "name", "name:en", "building", "storeys", "storeys_imputed", "footprint_m2",
            "gfa_m2", "is_dormitory", "ssg", "geometry"]
    return b[[c for c in keep if c in b.columns]].reset_index(drop=True)


def cluster_ssg(bld: gpd.GeoDataFrame) -> np.ndarray:
    if len(bld) == 1:
        return np.array([1])
    xy = np.column_stack([bld.centroid.x, bld.centroid.y])
    return fcluster(linkage(xy, method="complete"), t=CLUSTER_MAX_DIAMETER_M, criterion="distance")


def merge_small_clusters(sb: gpd.GeoDataFrame) -> pd.Series:
    """Fold clusters with < MIN_NODE_HOUSEHOLDS into the nearest cluster of the same SSG
    (scattered village houses / huts would otherwise become many near-empty nodes)."""
    lab = sb["cluster"].copy()
    while lab.nunique() > 1:
        hh = sb.groupby(lab)["households"].sum()
        small = hh.idxmin()
        if hh[small] >= MIN_NODE_HOUSEHOLDS:
            break
        cen = sb.centroid.groupby(lab).apply(lambda g: g.union_all().centroid)
        others = cen.drop(small)
        lab[lab == small] = others.index[np.argmin([cen[small].distance(o) for o in others])]
    return pd.Series(pd.factorize(lab)[0] + 1, index=sb.index)


def build_nodes(ssg: gpd.GeoDataFrame, bld: gpd.GeoDataFrame):
    rows, bld_parts = [], []
    for _, s in ssg.iterrows():
        sb = bld[bld["ssg"] == s["ssg"]].copy()
        if sb.empty:
            # No mapped residential footprint: fall back to one node at the SSG's interior point.
            p = s.geometry.representative_point()
            rows.append(dict(ssg=s["ssg"], cluster=1, n_buildings=0, gfa_m2=np.nan, x=p.x, y=p.y,
                             households=s["households"], population=s["population"],
                             location_method="ssg_interior_point"))
            continue
        sb["cluster"] = cluster_ssg(sb)
        sb["share"] = sb["gfa_m2"] / sb["gfa_m2"].sum()
        # Census 'households' are DOMESTIC households only: student halls / hostels / dormitories
        # get population (residents) but no households, unless an SSG has nothing else.
        hh_w = sb["gfa_m2"].where(~sb["is_dormitory"], 0)
        hh_w = hh_w if hh_w.sum() > 0 else sb["gfa_m2"]
        sb["households"] = hh_w / hh_w.sum() * s["households"]
        sb["cluster"] = merge_small_clusters(sb)
        sb["population"] = sb["share"] * s["population"]
        bld_parts.append(sb)
        for c, cb in sb.groupby("cluster"):
            w = cb["gfa_m2"].to_numpy()
            # Node location: the building nearest the GFA-weighted centroid, so a node always sits
            # on a real residential building (a plain centroid of scattered huts can land on
            # empty hillside far from any footpath).
            cx, cy = np.average(cb.centroid.x, weights=w), np.average(cb.centroid.y, weights=w)
            near = cb.centroid.distance(gpd.points_from_xy([cx], [cy], crs=HK1980)[0]).idxmin()
            loc = cb.loc[near].geometry.representative_point()
            rows.append(dict(
                ssg=s["ssg"], cluster=int(c), n_buildings=len(cb), gfa_m2=w.sum(),
                x=loc.x, y=loc.y,
                households=cb["households"].sum(), population=cb["population"].sum(),
                location_method="building_nearest_gfa_centroid",
                example_buildings="; ".join(cb["name:en"].fillna(cb["name"]).dropna().astype(str)
                                            .unique()[:3])))
    nodes = pd.DataFrame(rows)
    nodes = gpd.GeoDataFrame(nodes, geometry=gpd.points_from_xy(nodes.x, nodes.y), crs=HK1980)
    nodes = nodes.sort_values(["ssg", "cluster"]).reset_index(drop=True)
    nodes.insert(0, "node_id", [f"D{k:03d}" for k in range(1, len(nodes) + 1)])
    nodes["daily_parcels"] = nodes["households"] * PARCELS_PER_HOUSEHOLD_PER_DAY
    ll = nodes.to_crs(WGS84)
    nodes["lat"], nodes["lon"] = ll.geometry.y.round(6), ll.geometry.x.round(6)
    nodes = nodes.drop(columns=["x", "y"])
    return nodes, pd.concat(bld_parts, ignore_index=True)


if __name__ == "__main__":
    ssg = load_ssg()
    # TPU 111 also contains Green Island & Little Green Island (uninhabited offshore islets);
    # keep only the mainland polygon so the boundary/buffer is not stretched into the harbour.
    union = ssg.union_all().buffer(0)
    if union.geom_type == "MultiPolygon":
        union = max(union.geoms, key=lambda g: g.area)
    boundary = gpd.GeoDataFrame({"tpus": [",".join(STUDY_TPUS)]}, geometry=[union], crs=HK1980)
    ssg["geometry"] = ssg.intersection(union)  # islet buildings get no households
    buffer = gpd.GeoDataFrame({"buffer_m": [SUPPLY_BUFFER_M]},
                              geometry=boundary.buffer(SUPPLY_BUFFER_M), crs=HK1980)
    out = PROCESSED / "study_area.gpkg"
    out.unlink(missing_ok=True)
    boundary.to_file(out, layer="boundary")
    buffer.to_file(out, layer="supply_buffer")
    ssg.to_file(out, layer="ssg")

    bld = load_residential_buildings(ssg)
    nodes, bld_alloc = build_nodes(ssg, bld)
    bld_alloc.to_file(PROCESSED / "demand_buildings.gpkg")
    (PROCESSED / "demand_nodes.gpkg").unlink(missing_ok=True)
    nodes.to_file(PROCESSED / "demand_nodes.gpkg")
    cols = ["node_id", "ssg", "cluster", "lat", "lon", "households", "population",
            "daily_parcels", "n_buildings", "gfa_m2", "location_method", "example_buildings"]
    nodes[cols].round({"households": 1, "population": 1, "daily_parcels": 2, "gfa_m2": 0}) \
        .to_csv(PROCESSED / "demand_nodes.csv", index=False)

    print(f"Study area: TPU {STUDY_TPUS}, {len(ssg)} SSGs, "
          f"{boundary.area.iloc[0] / 1e6:.2f} km2")
    print(f"Residential buildings used: {len(bld_alloc)} "
          f"({bld_alloc['storeys_imputed'].mean():.0%} with imputed storeys)")
    print(f"Demand nodes: {len(nodes)} | households {nodes.households.sum():,.0f} "
          f"(census {ssg.households.sum():,.0f}) | population {nodes.population.sum():,.0f} "
          f"(census {ssg.population.sum():,.0f})")
    print(nodes["households"].describe().round(0).to_string())
    print("Fallback nodes:", (nodes.location_method != "building_nearest_gfa_centroid").sum())

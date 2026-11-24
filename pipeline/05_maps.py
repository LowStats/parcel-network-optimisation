"""Step 5 - overview maps of the prepared data.

Outputs: figures/study_area_map.png (static, viewable on GitHub)
         figures/study_area_map.html (interactive Leaflet map - open locally in a browser)
"""
from __future__ import annotations

import folium
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from common import FIGURES, HK1980, PROCESSED, WGS84  # noqa: E402

OPERATOR_COLORS = {"SF Express": "#1f1f1f", "Hongkong Post (iPostal)": "#2e7d32",
                   "Cainiao": "#ef6c00", "JD Logistics": "#c62828", "Alfred24": "#6a1b9a"}
CATEGORY_COLORS = {"mtr_exit": "#d32f2f", "wet_market": "#795548", "shopping_mall": "#7b1fa2",
                   "community_facility": "#0288d1", "post_office": "#2e7d32",
                   "supermarket": "#f9a825", "convenience_store": "#00897b",
                   "bus_terminus": "#5d4037", "residential_building": "#9e9e9e"}

if __name__ == "__main__":
    boundary = gpd.read_file(PROCESSED / "study_area.gpkg", layer="boundary").to_crs(HK1980)
    buffer = gpd.read_file(PROCESSED / "study_area.gpkg", layer="supply_buffer").to_crs(HK1980)
    ssg = gpd.read_file(PROCESSED / "study_area.gpkg", layer="ssg").to_crs(HK1980)
    bld = gpd.read_file(PROCESSED / "demand_buildings.gpkg").to_crs(HK1980)
    nodes = gpd.read_file(PROCESSED / "demand_nodes.gpkg").to_crs(HK1980)
    sites = gpd.read_file(PROCESSED / "supply_sites.gpkg").to_crs(HK1980)
    ex, cand = sites[sites.site_class == "existing"], sites[sites.site_class == "candidate"]

    # ---------------------------------------------------------------- static PNG
    fig, ax = plt.subplots(figsize=(15, 10))
    buffer.boundary.plot(ax=ax, color="grey", ls="--", lw=0.8)
    ssg.boundary.plot(ax=ax, color="#bbbbbb", lw=0.5)
    boundary.boundary.plot(ax=ax, color="black", lw=1.6)
    bld.plot(ax=ax, color="#cfd8dc")
    for cat, g in cand.groupby("category"):
        g.plot(ax=ax, marker="^", color=CATEGORY_COLORS.get(cat, "k"), markersize=28, alpha=.85,
               label=f"candidate: {cat.replace('_', ' ')} ({len(g)})")
    nodes.plot(ax=ax, color="#1565c0", alpha=.55, edgecolor="white",
               markersize=nodes["households"] / 6)
    for op, g in ex.groupby("operator"):
        g.plot(ax=ax, marker="*", color=OPERATOR_COLORS.get(op, "k"), markersize=260,
               edgecolor="white", lw=.8, label=f"existing: {op} ({len(g)})")
    for _, r in nodes.iterrows():
        ax.annotate(r.node_id, (r.geometry.x, r.geometry.y), fontsize=6, ha="center",
                    va="center", color="white")
    handles, labels = ax.get_legend_handles_labels()
    handles += [Line2D([], [], marker="o", ls="", color="#1565c0", alpha=.55, markersize=12),
                Line2D([], [], color="black", lw=1.6),
                Line2D([], [], color="grey", ls="--")]
    labels += ["demand node (size = households)", "study area (TPU 111)",
               f"supply search buffer ({int(buffer.buffer_m.iloc[0])} m)"]
    ax.legend(handles, labels, loc="lower left", fontsize=8, framealpha=.9)
    xmin, ymin, xmax, ymax = buffer.total_bounds
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_title(f"Kennedy Town parcel-locker study: {len(nodes)} demand nodes "
                 f"({nodes.households.sum():,.0f} households), {len(ex)} existing sites, "
                 f"{len(cand)} candidate sites", fontsize=11)
    ax.set_axis_off()
    fig.savefig(FIGURES / "study_area_map.png", dpi=130, bbox_inches="tight")
    print("saved figures/study_area_map.png")

    # ---------------------------------------------------------------- interactive HTML
    c = boundary.to_crs(WGS84).geometry.iloc[0].centroid
    m = folium.Map(location=[c.y, c.x], zoom_start=16, tiles="cartodbpositron")
    folium.GeoJson(buffer.to_crs(WGS84), name="supply buffer",
                   style_function=lambda _: dict(color="grey", dashArray="5", fill=False)).add_to(m)
    folium.GeoJson(ssg.to_crs(WGS84), name="census SSGs",
                   style_function=lambda _: dict(color="#999", weight=1, fillOpacity=0),
                   tooltip=folium.GeoJsonTooltip(["ssg", "households", "population"])).add_to(m)
    folium.GeoJson(boundary.to_crs(WGS84), name="study area",
                   style_function=lambda _: dict(color="black", weight=3, fill=False)).add_to(m)
    fg = folium.FeatureGroup("demand nodes").add_to(m)
    for _, r in nodes.to_crs(WGS84).iterrows():
        folium.CircleMarker([r.geometry.y, r.geometry.x], radius=max(3, (r.households ** .5) / 2),
                            color="#1565c0", fill=True, fill_opacity=.5,
                            tooltip=f"{r.node_id} | {r.households:.0f} hh | {r.population:.0f} "
                                    f"pop | {r.example_buildings or ''}").add_to(fg)
    fg = folium.FeatureGroup("candidate sites").add_to(m)
    for _, r in cand.to_crs(WGS84).iterrows():
        folium.RegularPolygonMarker([r.geometry.y, r.geometry.x], number_of_sides=3, radius=6,
                                    color=CATEGORY_COLORS.get(r.category, "k"), fill=True,
                                    tooltip=f"{r.site_id} | {r.category} | {r['name']}").add_to(fg)
    fg = folium.FeatureGroup("existing sites").add_to(m)
    for _, r in ex.to_crs(WGS84).iterrows():
        folium.Marker([r.geometry.y, r.geometry.x],
                      icon=folium.Icon(color="black" if r.operator == "SF Express" else "green"
                                       if "iPostal" in r.operator else "orange"),
                      tooltip=f"{r.site_id} | {r.operator} | {r['name']}").add_to(fg)
    folium.LayerControl().add_to(m)
    m.save(FIGURES / "study_area_map.html")
    print("saved figures/study_area_map.html")

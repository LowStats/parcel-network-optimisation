"""Section 2 (ILP location models) + Section 4 (radius / budget sensitivity).

Models (0-1 integer programs, solved by branch-and-bound with HiGHS):
  A  SCLP  - minimum number of NEW lockers so every household has a locker within D metres
  B  MCLP  - maximum households covered with P NEW lockers
Both are run as *expansion* models (the 20 existing public lockers stay open for free) and as
*greenfield* models (no existing lockers) for comparison. Each is also solved as an LP
relaxation (x_j continuous in [0,1]) to show the integrality gap and branch-and-bound effort.

Candidate locker locations J:
  public    OSM candidate sites (MTR exits, markets, malls, supermarkets, convenience stores,
            community facilities, bus termini, post offices) + existing staffed pick-up shops
            (SF / Cainiao / JD stations could add a locker bank)
  estate    one in-building / estate-lobby locker per demand node; residents-only, so it covers
            ONLY its own node
Outputs: results/m1_*.csv|json, figures/models/m1_*.png
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

from mcommon import (D_BASE, D_SWEEP, FIG, INK, INK2, MUTED, RESULTS, SEQ, SERIES, STATUS, T_BASE,
                     Data, dump, mclp, save, sclp, style)

P_MAX = 60
ELBOW_SHARE = 0.005   # stop when the next locker adds < 0.5% of district households


def candidate_sets(D: Data):
    s = D.sites
    shops = s[(s.site_class == "existing") & (s.category == "staffed_station")
              & (s.public_access == "yes")]
    pub = list(D.cand_pub.site_id) + list(shops.site_id)
    res = list(D.cand_res.site_id)
    return pub, res


def coverage_by_group(D: Data, covered: set) -> pd.Series:
    d = D.demand.assign(is_cov=D.demand.node_id.isin(covered))
    g = d.groupby("group").apply(lambda x: 100 * x[x.is_cov].households.sum()
                                 / x.households.sum(), include_groups=False)
    return g.reindex(["Western", "Sheung Wan", "Central", "Mid-Levels", "The Peak"])


def elbow(curve: pd.DataFrame, H: float) -> int:
    gains = curve.households.diff().fillna(0)
    for P in curve.P[1:]:
        if gains[curve.P == P].iloc[0] < ELBOW_SHARE * H:
            return int(P) - 1
    return int(curve.P.max())


if __name__ == "__main__":
    plt = style()
    D = Data()
    pub, res = candidate_sets(D)
    allc = pub + res
    ex_pub = list(D.ex_lock.site_id)
    ex = ex_pub + list(D.ex_lock_res.site_id)   # residents-only lockers serve their own estate
    out = {"existing_public_lockers": len(ex_pub), "existing_residents_only": len(ex) - len(ex_pub)}

    # ---------------------------------------------------------------- 0. baseline coverage
    rows = []
    for R in D_SWEEP:
        rows.append(dict(radius_m=R,
                         existing_public_lockers=D.pct(D.covered_nodes(ex_pub, R)),
                         existing_lockers=D.pct(D.covered_nodes(ex, R)),
                         all_public_points=D.pct(D.covered_nodes(D.ex_all.site_id, R))))
    base = pd.DataFrame(rows).round(1)
    base.to_csv(RESULTS / "m1_baseline_coverage.csv", index=False)
    print(base.to_string(index=False))

    # ---------------------------------------------------------------- 1. SCLP (Model A)
    sc = []
    for label, cands, fixed in [("expansion, public sites only", pub, ex),
                                ("expansion, public + estate lockers", allc, ex),
                                ("greenfield, public + estate lockers", allc, [])]:
        r = sclp(D, cands, D_BASE, fixed)
        rl = sclp(D, cands, D_BASE, fixed, relax=True)
        ch = r["chosen"] or []
        n_res = sum(c in set(res) for c in ch)
        unc_hh = float(D.h.reindex(r["uncoverable"]).sum())
        sc.append(dict(model=label, new_lockers=len(ch), public=len(ch) - n_res, estate=n_res,
                       lp_relaxation=round(rl["obj"], 2), fractional_vars=rl["n_frac"],
                       bnb_nodes=r["nodes"], uncoverable_nodes=len(r["uncoverable"]),
                       uncoverable_households=round(unc_hh)))
        if label.startswith("expansion, public sites only"):
            unc = D.demand[D.demand.node_id.isin(r["uncoverable"])]
            out["sclp_public_uncoverable_by_group"] = unc.groupby("group").households.sum() \
                .round().to_dict()
        if label == "expansion, public + estate lockers":
            out["sclp_expansion_sites"] = ch
    sc = pd.DataFrame(sc)
    sc.to_csv(RESULTS / "m1_sclp.csv", index=False)
    print(sc.to_string(index=False))

    # ---------------------------------------------------------------- 2. MCLP (Model B) curves
    curves, chosen_by_P = [], {}
    for label, cands, fixed in [("public + estate", allc, ex), ("public only", pub, ex),
                                ("greenfield public only", pub, [])]:
        for P in range(0, P_MAX + 1):
            r = mclp(D, cands, P, D_BASE, fixed)
            curves.append(dict(variant=label, P=P, households=r["obj"], pct=r["pct"],
                               bnb_nodes=r["nodes"]))
            if label == "public + estate":
                chosen_by_P[P] = r["chosen"]
            if r["pct"] >= 99.999:
                break
    curves = pd.DataFrame(curves)
    curves.to_csv(RESULTS / "m1_mclp_curves.csv", index=False)
    main = curves[curves.variant == "public + estate"].reset_index(drop=True)
    P_star = elbow(main, D.H)
    p_to = {t: int(main[main.pct >= t].P.min()) if (main.pct >= t).any() else None
            for t in (80, 90, 95, 99)}
    print("MCLP elbow P* =", P_star, "coverage", round(main.pct[P_star], 1), "P for targets", p_to)

    # LP relaxation / branch-and-bound at a few budgets
    lp_rows = []
    for P in (5, 10, P_star, 20, 30):
        ri = mclp(D, allc, P, D_BASE, ex)
        rl = mclp(D, allc, P, D_BASE, ex, relax=True)
        lp_rows.append(dict(P=P, ilp_pct=round(ri["pct"], 3), lp_pct=round(rl["pct"], 3),
                            gap_households=round(rl["obj"] - ri["obj"], 1),
                            fractional_vars=rl["n_frac"], bnb_nodes=ri["nodes"]))
    lp_rows = pd.DataFrame(lp_rows)
    lp_rows.to_csv(RESULTS / "m1_lp_relaxation.csv", index=False)
    print(lp_rows.to_string(index=False))

    # efficiency of the current 20-locker layout
    gf20 = curves[(curves.variant == "greenfield public only") & (curves.P == len(ex_pub))]
    out["current_layout"] = dict(n=len(ex_pub),
                                 pct=float(base.existing_public_lockers[base.radius_m == 300].iloc[0]),
                                 optimal_same_n_pct=float(gf20.pct.iloc[0]) if len(gf20) else None)

    # ---------------------------------------------------------------- 3. radius sensitivity
    rad = []
    for R in D_SWEEP:
        r = sclp(D, allc, R, ex)
        ch = r["chosen"] or []
        n_res = sum(c in set(res) for c in ch)
        rp = sclp(D, pub, R, ex)
        row = dict(radius_m=R, sclp_new_lockers=len(ch), sclp_public=len(ch) - n_res,
                   sclp_estate=n_res, public_only_uncoverable_hh=round(float(
                       D.h.reindex(rp["uncoverable"]).sum())),
                   baseline_pct=D.pct(D.covered_nodes(ex, R)))
        for P in (5, 10, 15, 20, 30):
            row[f"mclp_P{P}_pct"] = round(mclp(D, allc, P, R, ex)["pct"], 1)
        rad.append(row)
    rad = pd.DataFrame(rad).round(1)
    rad.to_csv(RESULTS / "m1_radius_sensitivity.csv", index=False)
    print(rad.to_string(index=False))
    rad_curves = []
    for R in D_SWEEP:
        for P in range(0, 41):
            rad_curves.append(dict(radius_m=R, P=P, pct=mclp(D, allc, P, R, ex)["pct"]))
    rad_curves = pd.DataFrame(rad_curves)
    rad_curves.to_csv(RESULTS / "m1_radius_curves.csv", index=False)

    # ---------------------------------------------------------------- 4. distance vs time
    tm = []
    for P in (0, P_star):
        rd = mclp(D, allc, P, D_BASE, ex)
        rt = mclp(D, allc, P, T_BASE, ex, metric="walk_min")
        gd, gt = coverage_by_group(D, rd["covered"]), coverage_by_group(D, rt["covered"])
        for g in gd.index:
            tm.append(dict(P=P, group=g, by_distance_300m=round(gd[g], 1),
                           by_time_4min=round(gt[g], 1)))
        tm.append(dict(P=P, group="District", by_distance_300m=round(rd["pct"], 1),
                       by_time_4min=round(rt["pct"], 1)))
        if P == P_star:
            out["time_vs_distance_overlap"] = len(set(rd["chosen"]) & set(rt["chosen"]))
    tm = pd.DataFrame(tm)
    tm.to_csv(RESULTS / "m1_time_vs_distance.csv", index=False)
    print(tm.to_string(index=False))

    # ---------------------------------------------------------------- 5. recommended plan
    plan_ids = chosen_by_P[P_star]
    sites = D.sites.set_index("site_id")
    cov_ex = D.covered_nodes(ex, D_BASE)
    pairs = D.coverage(plan_ids, D_BASE)
    newly = pairs[~pairs.node_id.isin(cov_ex)]
    rows = []
    for sid in plan_ids:
        nodes = set(newly[newly.site_id == sid].node_id)
        # households only this site covers (not covered by any other open locker)
        others = set(newly[newly.site_id != sid].node_id)
        node_row = D.demand[D.demand.node_id.isin(nodes)]
        s = sites.loc[sid]
        rows.append(dict(site_id=sid, name=D.label(sid), name_original=s["name"],
                         category=s["category"],
                         operator=s["operator"] if isinstance(s["operator"], str) else "",
                         lat=s.lat, lon=s.lon,
                         area=node_row.area.mode().iloc[0] if len(node_row) else "",
                         households_reached=round(float(D.h.reindex(list(nodes)).sum())),
                         households_only_this_site=round(float(
                             D.h.reindex(list(nodes - others)).sum()))))
    plan = pd.DataFrame(rows).sort_values("households_only_this_site", ascending=False)
    plan.to_csv(RESULTS / "m1_recommended_plan.csv", index=False)
    print(plan.to_string(index=False))
    grp = pd.DataFrame({"existing lockers": coverage_by_group(D, cov_ex),
                        f"+ {P_star} new lockers": coverage_by_group(D, D.covered_nodes(
                            ex + plan_ids, D_BASE))}).round(1)
    grp.to_csv(RESULTS / "m1_coverage_by_area.csv")
    print(grp)

    out.update(P_star=P_star, P_star_pct=float(main.pct[P_star]), P_for_target=p_to,
               baseline_pct=float(main.pct[0]), plan_sites=plan_ids,
               chosen_by_P={int(k): v for k, v in chosen_by_P.items()})
    dump(out, "m1_summary")

    # ================================================================ figures
    # (a) MCLP coverage curve
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    for k, (lab, col) in enumerate([("public + estate", SERIES[0]), ("public only", SERIES[1]),
                                    ("greenfield public only", SERIES[2])]):
        c = curves[curves.variant == lab]
        ax.plot(c.P, c.pct, color=col, lw=2, label={"public + estate":
                "Expansion: public sites + estate lockers",
                "public only": "Expansion: public sites only",
                "greenfield public only": "Greenfield (no existing lockers), public sites"}[lab])
    ax.axvline(P_star, color=MUTED, lw=1, ls="--")
    ax.annotate(f"Elbow P* = {P_star}\n{main.pct[P_star]:.1f}% of households",
                (P_star, main.pct[P_star]), xytext=(P_star + 3, main.pct[P_star] - 18),
                fontsize=9, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, lw=.8))
    ax.scatter([len(ex_pub)], [out["current_layout"]["pct"]], color=INK, s=40, zorder=5)
    ax.annotate(f"Today: {len(ex_pub)} public lockers, {out['current_layout']['pct']:.0f}%",
                (len(ex_pub), out["current_layout"]["pct"]), xytext=(len(ex_pub) - 14,
                                                                 out["current_layout"]["pct"] - 9),
                fontsize=9, color=INK2)
    ax.set_xlabel("New lockers added, P")
    ax.set_ylabel("Households within 300 m walk (%)")
    ax.set_ylim(0, 102)
    ax.set_xlim(0, P_MAX)
    ax.set_title("Maximal covering: coverage gained per locker budget")
    ax.legend(loc="lower right")
    save(fig, "m1_mclp_curve")

    # (b) marginal gain
    fig, ax = plt.subplots(figsize=(8.4, 3.6))
    g = main.households.diff().iloc[1:41]
    ax.bar(main.P.iloc[1:41], g, color=[SERIES[0] if p <= P_star else SEQ[1]
                                       for p in main.P.iloc[1:41]], width=.8)
    ax.axhline(ELBOW_SHARE * D.H, color=MUTED, lw=1, ls="--")
    ax.text(40, ELBOW_SHARE * D.H * 1.15, "0.5% of district households", ha="right",
            fontsize=8, color=INK2)
    ax.set_xlabel("P-th new locker")
    ax.set_ylabel("Extra households covered")
    ax.set_title("Diminishing returns: households added by each extra locker")
    save(fig, "m1_marginal_gain")

    # (c) radius trade-off
    fig, ax = plt.subplots(figsize=(8.4, 4))
    ax.bar(rad.radius_m - 9, rad.sclp_public, width=18, color=SERIES[0], label="public sites")
    ax.bar(rad.radius_m + 9, rad.sclp_estate, width=18, color=SERIES[1], label="estate lockers")
    for _, r in rad.iterrows():
        ax.text(r.radius_m, max(r.sclp_public, r.sclp_estate) + 2, f"{int(r.sclp_new_lockers)}",
                ha="center", fontsize=9, color=INK)
    ax.set_xticks(rad.radius_m)
    ax.set_xlabel("Coverage radius D (metres of walking)")
    ax.set_ylabel("New lockers needed for 100% coverage")
    ax.set_title("Convenience vs infrastructure: set-covering requirement by radius")
    ax.legend()
    save(fig, "m1_radius_tradeoff")

    # (d) MCLP curves by radius
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ramp = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#256abf", "#184f95", "#0d366b"]
    for k, R in enumerate(D_SWEEP):
        c = rad_curves[rad_curves.radius_m == R]
        ax.plot(c.P, c.pct, color=ramp[k], lw=2)
        ax.text(40.5, c.pct.iloc[-1], f"{R} m", va="center", fontsize=8, color=INK2)
    ax.set_xlim(0, 44)
    ax.set_ylim(0, 102)
    ax.set_xlabel("New lockers added, P")
    ax.set_ylabel("Households covered (%)")
    ax.set_title("Coverage curves for walking radii 200-500 m")
    save(fig, "m1_radius_curves")

    # (e) coverage by area
    fig, ax = plt.subplots(figsize=(8.4, 3.8))
    x = np.arange(len(grp))
    for k, col in enumerate(grp.columns):
        ax.bar(x + (k - .5) * .38, grp[col], width=.36, color=SERIES[k], label=col)
        for xi, v in zip(x, grp[col]):
            ax.text(xi + (k - .5) * .38, v + 1.5, f"{v:.0f}", ha="center", fontsize=8, color=INK2)
    ax.set_xticks(x, grp.index)
    ax.set_ylim(0, 110)
    ax.set_ylabel("Households within 300 m of a locker (%)")
    ax.set_title("Who gains: locker coverage by area")
    ax.legend(loc="upper right")
    save(fig, "m1_coverage_by_area")

    # (f) plan map
    proc = RESULTS.parent / "data" / "processed"
    bnd = gpd.read_file(proc / "study_area.gpkg", layer="boundary")
    bld = gpd.read_file(proc / "demand_buildings.gpkg")
    dn = gpd.read_file(proc / "demand_nodes.gpkg").to_crs(bnd.crs)
    st = gpd.read_file(proc / "supply_sites.gpkg").to_crs(bnd.crs)
    allcov = D.covered_nodes(ex + plan_ids, D_BASE)
    fig, ax = plt.subplots(figsize=(11, 7.5))
    bnd.boundary.plot(ax=ax, color=INK2, lw=1)
    bld.to_crs(bnd.crs).plot(ax=ax, color="#e4e3df")
    status = np.where(dn.node_id.isin(cov_ex), "today", np.where(dn.node_id.isin(allcov), "new",
                                                                  "no"))
    for k, (st_, col, lab) in enumerate([("today", SEQ[3], "covered today"),
                                         ("new", SERIES[2], f"newly covered (+{P_star} lockers)"),
                                         ("no", STATUS["critical"], "still > 300 m")]):
        sel = dn[status == st_]
        ax.scatter(sel.geometry.x, sel.geometry.y, s=sel.households / 9, color=col, alpha=.6,
                   edgecolor="white", lw=.5, label=lab)
    e = st[st.site_id.isin(ex)]
    ax.scatter(e.geometry.x, e.geometry.y, marker="s", s=36, color=INK, label="existing locker")
    p = st[st.site_id.isin(plan_ids)]
    ax.scatter(p.geometry.x, p.geometry.y, marker="*", s=170, color=SERIES[1],
               edgecolor="white", lw=.6, label="new locker (MCLP)")
    ax.set_axis_off()
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title(f"Recommended expansion: {P_star} new lockers at 300 m")
    xmin, ymin, xmax, ymax = bnd.total_bounds
    ax.set_xlim(xmin - 200, xmax + 200)
    ax.set_ylim(ymin - 200, ymax + 200)
    save(fig, "m1_plan_map")
    print("done")

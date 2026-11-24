"""Capacitated allocation as a transportation LP (Ch4) + LP sensitivity / duality (Ch2).

The coverage models assume every locker has unlimited capacity. Here the recommended network
(existing lockers + the MCLP plan) receives each demand node's daily locker-bound parcels:

  sources  i : demand nodes, supply  s_i = h_i * parcels/hh/day * locker share   (parcels/day)
  sinks    j : open lockers, capacity c_j = compartments K_j / mean dwell (days)  (parcels/day)
  dummy    0 : "door delivery / staffed counter" - absorbs parcels no locker can take
               (unbalanced problem: total supply > total locker capacity -> dummy column)

  min  sum_ij d_ij f_ij + M sum_i f_i0          d_ij = walking metres (only if d_ij <= 500 m)
  s.t. sum_j f_ij + f_i0 = s_i      (every parcel is delivered somewhere)
       sum_i f_ij       <= c_j      (locker capacity)
       f >= 0

Dual values: u_j on capacity = shadow price of one more parcel/day of capacity at locker j
(metres of walking saved, and parcels moved off door delivery). RHS ranging is computed by
re-solving with perturbed capacity (Ch2.4 'range of feasibility').
A small textbook instance is also solved with the Northwest-Corner rule and Vogel's
Approximation Method (Ch4) and compared with the LP optimum.
Outputs: results/m2_*.csv|json, figures/models/m2_*.png
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from mcommon import (D_BASE, D_MAX_ALLOC, INK, INK2, LAMBDA_PER_HH, MUTED, RESULTS, SEQ, SERIES,
                     Data, dump, save, style)
from mcommon import C  # noqa: E402  pipeline parameters

K_MEDIUM = C.LOCKER_SIZES["medium"]
MEAN_DWELL = sum(h / 24 * p for h, p in C.DWELL_PLAN.items())   # days (plan: 1.5)
M_PENALTY = 1000.0   # metre-equivalent cost of a parcel that no locker can take


def build(D: Data, open_ids, cap: dict, lam: pd.Series):
    pairs = D.L[D.L.site_id.isin(open_ids) & (D.L.walk_m <= D_MAX_ALLOC)].copy()
    ro = pairs.site_id.isin(D.resonly)
    pairs = pairs[~ro | (pairs.euclid_m <= 25)]
    nodes = list(lam.index)
    sites = list(open_ids)
    ni = {n: k for k, n in enumerate(nodes)}
    sj = {s: k for k, s in enumerate(sites)}
    pairs = pairs[pairs.node_id.isin(ni)]
    nA = len(pairs)
    nv = nA + len(nodes)                     # arc flows + dummy flows
    cost = np.concatenate([pairs.walk_m.to_numpy(), np.full(len(nodes), M_PENALTY)])
    # supply equalities
    r1 = np.concatenate([pairs.node_id.map(ni).to_numpy(), np.arange(len(nodes))])
    c1 = np.arange(nv)
    Aeq = coo_matrix((np.ones(nv), (r1, c1)), shape=(len(nodes), nv)).tocsr()
    # capacity inequalities
    Aub = coo_matrix((np.ones(nA), (pairs.site_id.map(sj).to_numpy(), np.arange(nA))),
                     shape=(len(sites), nv)).tocsr()
    bub = np.array([cap[s] for s in sites])
    return dict(pairs=pairs, nodes=nodes, sites=sites, cost=cost, Aeq=Aeq, beq=lam.to_numpy(),
                Aub=Aub, bub=bub, nA=nA)


def solve(m, bub=None):
    r = linprog(m["cost"], A_ub=m["Aub"], b_ub=m["bub"] if bub is None else bub, A_eq=m["Aeq"],
                b_eq=m["beq"], bounds=(0, None), method="highs")
    assert r.status == 0, r.message
    return r


# --------------------------------------------------------------- Ch4 textbook methods
def northwest_corner(supply, demand):
    s, d = list(supply), list(demand)
    x = np.zeros((len(s), len(d)))
    i = j = 0
    while i < len(s) and j < len(d):
        q = min(s[i], d[j])
        x[i, j] = q
        s[i] -= q
        d[j] -= q
        if s[i] <= 1e-9:
            i += 1
        else:
            j += 1
    return x


def vogel(cost, supply, demand):
    cost = np.array(cost, float)
    s, d = np.array(supply, float), np.array(demand, float)
    x = np.zeros_like(cost)
    rows, cols = set(range(len(s))), set(range(len(d)))
    while rows and cols:
        def pen(vals):
            v = sorted(vals)
            return v[1] - v[0] if len(v) > 1 else v[0]
        rp = {i: pen([cost[i, j] for j in cols]) for i in rows}
        cp = {j: pen([cost[i, j] for i in rows]) for j in cols}
        bi, bj = max(rp, key=rp.get), max(cp, key=cp.get)
        if rp[bi] >= cp[bj]:
            i = bi
            j = min(cols, key=lambda c: cost[i, c])
        else:
            j = bj
            i = min(rows, key=lambda r: cost[r, j])
        q = min(s[i], d[j])
        x[i, j] = q
        s[i] -= q
        d[j] -= q
        if s[i] <= 1e-9:
            rows.discard(i)
        if d[j] <= 1e-9:
            cols.discard(j)
    return x


if __name__ == "__main__":
    plt = style()
    D = Data()
    m1 = json.loads((RESULTS / "m1_summary.json").read_text())
    ex = list(D.ex_lock.site_id) + list(D.ex_lock_res.site_id)
    plan = m1["plan_sites"]
    open_ids = ex + plan
    lam = D.h * LAMBDA_PER_HH
    cap1 = K_MEDIUM / MEAN_DWELL
    cap = {s: cap1 for s in open_ids}
    m = build(D, open_ids, cap, lam)
    r = solve(m)
    nA = m["nA"]
    f = r.x[:nA]
    f0 = r.x[nA:]
    pairs = m["pairs"].assign(flow=f)
    served = pairs.groupby("site_id").flow.sum().reindex(open_ids).fillna(0)
    duals = pd.Series(-r.ineqlin.marginals, index=m["sites"])   # >=0: metres saved per +1 cap
    tot = lam.sum()
    summary = dict(locker_parcels_per_day=tot, lockers=len(open_ids),
                   capacity_per_locker=cap1, total_capacity=cap1 * len(open_ids),
                   served_by_lockers=served.sum(), to_dummy=f0.sum(),
                   share_served=served.sum() / tot, objective=r.fun,
                   avg_walk_m=float((pairs.flow * pairs.walk_m).sum() / max(served.sum(), 1e-9)),
                   binding=int((served >= cap1 - 1e-6).sum()), mean_dwell_days=MEAN_DWELL,
                   compartments=K_MEDIUM, M_penalty=M_PENALTY, lambda_per_hh=LAMBDA_PER_HH)
    print(json.dumps(summary, indent=1, default=float))

    sites = D.sites.set_index("site_id")
    tab = pd.DataFrame({"site_id": open_ids,
                        "name": [D.label(s) for s in open_ids],
                        "type": ["existing" if s in ex else "new" for s in open_ids],
                        "served_per_day": served.values.round(1),
                        "capacity_per_day": round(cap1, 1),
                        "utilisation_pct": (100 * served.values / cap1).round(1),
                        "shadow_price_m_per_parcel": duals.reindex(open_ids).values.round(1)})
    # demand within reach of each locker (what it COULD attract)
    reach = pairs.groupby("site_id").node_id.apply(lambda n: lam.reindex(n).sum())
    tab["demand_within_500m"] = reach.reindex(open_ids).fillna(0).values.round(1)
    tab = tab.sort_values("shadow_price_m_per_parcel", ascending=False)
    tab.to_csv(RESULTS / "m2_locker_allocation.csv", index=False)
    print(tab.head(15).to_string(index=False))

    # ---------- RHS ranging (range of feasibility) for the 5 most valuable lockers
    rng = []
    for sid in tab.site_id.head(5):
        k = m["sites"].index(sid)
        u0 = duals[sid]

        def dual_at(c):
            b = m["bub"].copy()
            b[k] = c
            return -solve(m, b).ineqlin.marginals[k]
        lo, hi = 0.0, cap1
        # lower limit: smallest capacity with the same dual
        a, b = 0.0, cap1
        for _ in range(25):
            mid = (a + b) / 2
            if abs(dual_at(mid) - u0) < 1e-6:
                b = mid
            else:
                a = mid
        lo = b
        a, b = cap1, cap1 * 40
        if abs(dual_at(b) - u0) < 1e-6:
            hi = np.inf
        else:
            for _ in range(25):
                mid = (a + b) / 2
                if abs(dual_at(mid) - u0) < 1e-6:
                    a = mid
                else:
                    b = mid
            hi = a
        rng.append(dict(site_id=sid, name=D.label(sid), shadow_price=round(u0, 1),
                        capacity_now=round(cap1, 1), range_lower=round(lo, 1),
                        range_upper=round(hi, 1) if np.isfinite(hi) else "inf",
                        extra_modules_at_this_price=round((hi - cap1) * MEAN_DWELL / 40, 1)
                        if np.isfinite(hi) else "inf"))
    rng = pd.DataFrame(rng)
    rng.to_csv(RESULTS / "m2_rhs_ranging.csv", index=False)
    print(rng.to_string(index=False))

    # ---------- reduced costs of unused routes (Cj - Zj / improvement index)
    rc = r.lower.marginals[:nA]
    unused = pairs.assign(reduced_cost=rc)[(pairs.flow < 1e-9)]
    rc_tab = unused.sort_values("reduced_cost").head(10)[["node_id", "site_id", "walk_m",
                                                          "reduced_cost"]]
    rc_tab.to_csv(RESULTS / "m2_reduced_costs.csv", index=False)

    # ---------- capacity scenarios: how many 40-compartment modules does the network need?
    scen = []
    for mods_per_locker in (1, 2, 3, 4, 6, 8):
        cp = {s: mods_per_locker * 40 / MEAN_DWELL for s in open_ids}
        mm = build(D, open_ids, cp, lam)
        rr = solve(mm)
        sv = rr.x[:mm["nA"]].sum()
        scen.append(dict(compartments_per_locker=mods_per_locker * 40,
                         network_capacity=round(cp[open_ids[0]] * len(open_ids)),
                         served=round(sv), share_served_pct=round(100 * sv / tot, 1),
                         avg_walk_m=round(float((rr.x[:mm["nA"]] * mm["pairs"].walk_m.to_numpy())
                                                .sum() / max(sv, 1e-9)), 1)))
    scen = pd.DataFrame(scen)
    scen.to_csv(RESULTS / "m2_capacity_scenarios.csv", index=False)
    print(scen.to_string(index=False))
    # all demand that is within 500 m of an open locker (upper bound on what lockers can serve)
    reachable = lam.reindex(pairs.node_id.unique()).sum()
    summary["reachable_parcels"] = reachable

    # ---------- Ch4 textbook demo: Kennedy Town sub-problem
    kt_nodes = D.demand[D.demand.tpu == "111"].sort_values("households", ascending=False) \
        .node_id.head(6).tolist()
    kt_sites = [s for s in open_ids if s in set(pairs[pairs.node_id.isin(kt_nodes)].site_id)][:4]
    cmat = D.L[D.L.node_id.isin(kt_nodes) & D.L.site_id.isin(kt_sites)] \
        .pivot(index="node_id", columns="site_id", values="walk_m").reindex(index=kt_nodes,
                                                                            columns=kt_sites)
    cmat = cmat.fillna(2000).round(0)
    sup = (lam.reindex(kt_nodes)).round(0).to_numpy()
    dem_total = sup.sum()
    dem = np.full(len(kt_sites), np.floor(dem_total / len(kt_sites)))
    dem[-1] += dem_total - dem.sum()                                # balanced instance
    nw = northwest_corner(sup, dem)
    vam = vogel(cmat.to_numpy(), sup, dem)
    # exact optimum of the balanced transportation problem
    nI, nJ = cmat.shape
    A_eq = np.zeros((nI + nJ, nI * nJ))
    for i in range(nI):
        A_eq[i, i * nJ:(i + 1) * nJ] = 1
    for j in range(nJ):
        A_eq[nI + j, j::nJ] = 1
    opt = linprog(cmat.to_numpy().ravel(), A_eq=A_eq, b_eq=np.concatenate([sup, dem]),
                  bounds=(0, None), method="highs")
    demo = dict(nodes=kt_nodes, sites=kt_sites,
                site_names=[D.label(s) for s in kt_sites],
                node_names=[str(D.demand.set_index("node_id").loc[n, "example_buildings"])
                            .split(";")[0] for n in kt_nodes],
                cost=cmat.to_numpy().tolist(), supply=sup.tolist(), demand=dem.tolist(),
                northwest=nw.tolist(), vogel=vam.tolist(),
                optimal=opt.x.reshape(nI, nJ).round(1).tolist(),
                cost_northwest=float((nw * cmat.to_numpy()).sum()),
                cost_vogel=float((vam * cmat.to_numpy()).sum()), cost_optimal=float(opt.fun),
                u_rows=(-opt.eqlin.marginals[:nI]).round(1).tolist(),
                v_cols=(-opt.eqlin.marginals[nI:]).round(1).tolist())
    print("Ch4 demo costs: NW", demo["cost_northwest"], "VAM", demo["cost_vogel"], "opt",
          demo["cost_optimal"])

    # ---------- Model C: module allocation MILP (Ch3.2 mixed-integer)
    #   max parcels served by lockers  (min sum M*f_i0 + d_ij f_ij  with small walking weight)
    #   s.t. flows as above, sum_i f_ij <= (40/dwell) * n_j,  n_j >= 2,  sum_j n_j <= 2|J| + B
    #        n_j integer (modules of 40 compartments)
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import hstack, vstack, csr_matrix, diags
    mm = build(D, open_ids, {s_: 0 for s_ in open_ids}, lam)
    nv, J = len(mm["cost"]), len(open_ids)
    mod_cap = 40 / MEAN_DWELL
    alloc_rows = []
    module_plan = {}
    for B in (0, 26, 52, 104, 156, 260):
        cvec = np.concatenate([mm["cost"], np.zeros(J)])
        A_cap = hstack([mm["Aub"], diags(-mod_cap * np.ones(J))]).tocsr()      # flow - cap*n <= 0
        A_bud = csr_matrix(np.concatenate([np.zeros(nv), np.ones(J)])[None, :])
        A_eq = hstack([mm["Aeq"], csr_matrix((len(mm["nodes"]), J))]).tocsr()
        cons = [LinearConstraint(A_cap, -np.inf, 0), LinearConstraint(A_bud, -np.inf, 2 * J + B),
                LinearConstraint(A_eq, mm["beq"], mm["beq"])]
        lb = np.concatenate([np.zeros(nv), 2 * np.ones(J)])
        ub = np.concatenate([np.full(nv, np.inf), np.full(J, 60)])
        integ = np.concatenate([np.zeros(nv), np.ones(J)])
        rr = milp(cvec, constraints=cons, integrality=integ, bounds=Bounds(lb, ub),
                  options={"time_limit": 120, "mip_rel_gap": 1e-4})
        n = np.round(rr.x[nv:]).astype(int)
        sv = rr.x[:mm["nA"]].sum()
        alloc_rows.append(dict(extra_modules=B, total_compartments=int(n.sum() * 40),
                               served=round(sv), share_served_pct=round(100 * sv / tot, 1),
                               lockers_expanded=int((n > 2).sum()), max_modules=int(n.max()),
                               bnb_nodes=getattr(rr, "mip_node_count", None),
                               mip_gap=getattr(rr, "mip_gap", None)))
        module_plan[B] = dict(zip(open_ids, n.tolist()))
    alloc = pd.DataFrame(alloc_rows)
    alloc.to_csv(RESULTS / "m2_module_allocation.csv", index=False)
    print(alloc.to_string(index=False))
    mp = pd.DataFrame(module_plan)
    mp.insert(0, "name", [D.label(s_) for s_ in mp.index])
    mp.insert(1, "type", ["existing" if s_ in ex else "new" for s_ in mp.index])
    mp.to_csv(RESULTS / "m2_module_plan.csv")
    summary["module_allocation"] = alloc.to_dict("records")
    summary["ch4_demo"] = demo
    summary["rhs_ranging"] = rng.to_dict("records")
    dump(summary, "m2_summary")

    # ================================================================ figures
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    t = tab.sort_values("demand_within_500m", ascending=False).reset_index(drop=True)
    x = np.arange(len(t))
    ax.bar(x, t.demand_within_500m, color=SEQ[1], width=.8, label="parcels/day within 500 m")
    ax.bar(x, t.served_per_day, color=SERIES[0], width=.8, label="served by the LP allocation")
    ax.axhline(cap1, color=INK2, lw=1, ls="--")
    ax.text(len(t) - .5, cap1 + 45, f"capacity of one {K_MEDIUM}-compartment bank = "
            f"{cap1:.0f}/day", ha="right", fontsize=8, color=INK2)
    ax.set_xticks([])
    ax.set_xlabel("Open lockers (existing + recommended), sorted by nearby demand")
    ax.set_ylabel("Parcels per day")
    ax.set_title("Coverage is not capacity: nearby demand vs one locker bank")
    ax.legend(loc="upper right")
    save(fig, "m2_demand_vs_capacity")

    fig, ax = plt.subplots(figsize=(8.4, 3.8))
    ax.plot(scen.compartments_per_locker, scen.share_served_pct, color=SERIES[0], marker="o",
            ms=6)
    for _, rr in scen.iterrows():
        ax.text(rr.compartments_per_locker, rr.share_served_pct + 3,
                f"{rr.share_served_pct:.0f}%", ha="center", fontsize=8, color=INK2)
    ax.axhline(100 * reachable / tot, color=MUTED, lw=1, ls="--")
    ax.text(scen.compartments_per_locker.max(), 100 * reachable / tot - 6,
            "all parcels within 500 m of a locker", ha="right", fontsize=8, color=INK2)
    ax.set_ylim(0, 105)
    ax.set_xlabel("Compartments per locker bank")
    ax.set_ylabel("Locker-bound parcels served (%)")
    ax.set_title("How much capacity the network needs (transportation LP)")
    save(fig, "m2_capacity_scenarios")
    print("done")

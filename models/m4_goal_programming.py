"""Extension (Ch3.3) - preemptive goal programming: efficiency vs equity vs budget.

The MCLP maximises district-wide coverage, which favours the dense flat west. Planners usually
also want a minimum service level in every area (Mid-Levels, The Peak). Goal programming makes
the trade-off explicit with deviational variables and priority levels.

Variables: x_j in {0,1} (new locker at site j), 0 <= y_i <= 1 (node i covered), deviations >= 0
Goal constraints (households, 300 m walk, existing lockers open):
  G1 equity   for every area g:  sum_{i in g} h_i y_i + d_g^-  >= 0.70 H_g
  G2 district                    sum_i h_i y_i + d_D^-          >= 0.90 H
  G3 budget                      sum_j x_j - d_B^+              <= 22   (the MCLP elbow)
Priorities are solved lexicographically (P1 first; its optimum is then fixed as a constraint
while P2 is minimised, and so on). Two priority orders are compared.
Outputs: results/m4_goal_programming.csv|json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix, hstack, identity, vstack

from mcommon import D_BASE, RESULTS, Data, dump
from m1_location_ilp import candidate_sets, coverage_by_group

EQUITY, DISTRICT = 0.70, 0.90
GROUPS = ["Western", "Sheung Wan", "Central", "Mid-Levels", "The Peak"]


def solve_goal_program(D: Data, cands, budget: int, order: list[str]):
    ex = list(D.ex_lock.site_id) + list(D.ex_lock_res.site_id)
    base = D.covered_nodes(ex, D_BASE)
    cand = [c for c in cands if c not in set(ex)]
    pairs = D.coverage(cand, D_BASE)
    nodes = D.nodes
    ni = {n: k for k, n in enumerate(nodes)}
    cj = {c: k for k, c in enumerate(cand)}
    A = csr_matrix((np.ones(len(pairs)), (pairs.node_id.map(ni), pairs.site_id.map(cj))),
                   shape=(len(nodes), len(cand)))
    h = D.h.reindex(nodes).to_numpy()
    grp = D.demand.set_index("node_id").reindex(nodes)["group"].to_numpy()
    nx_, ny, ng = len(cand), len(nodes), len(GROUPS)
    # variable order: x | y | d_g^- (ng) | d_D^- | d_B^+
    nv = nx_ + ny + ng + 2
    iG, iD, iB = nx_ + ny, nx_ + ny + ng, nx_ + ny + ng + 1
    cons = []
    # y_i <= sum_j a_ij x_j  (or 1 if already covered by an existing locker)
    cover = hstack([-A, identity(ny), csr_matrix((ny, ng + 2))]).tocsr()
    ub = np.where(np.isin(nodes, list(base)), 1.0, 0.0)
    cons.append(LinearConstraint(cover, -np.inf, ub))
    # equity goals: -sum h y - d_g <= -0.7 H_g
    for k, g in enumerate(GROUPS):
        row = np.zeros(nv)
        row[nx_:nx_ + ny] = -h * (grp == g)
        row[iG + k] = -1
        cons.append(LinearConstraint(row[None, :], -np.inf, -EQUITY * h[grp == g].sum()))
    row = np.zeros(nv)
    row[nx_:nx_ + ny] = -h
    row[iD] = -1
    cons.append(LinearConstraint(row[None, :], -np.inf, -DISTRICT * h.sum()))
    row = np.zeros(nv)
    row[:nx_] = 1
    row[iB] = -1
    cons.append(LinearConstraint(row[None, :], -np.inf, budget))
    integ = np.zeros(nv)
    integ[:nx_] = 1
    lb = np.zeros(nv)
    ubv = np.concatenate([np.ones(nx_ + ny), np.full(ng + 2, np.inf)])
    objs = {"equity": np.r_[np.zeros(nx_ + ny), np.ones(ng), 0, 0],
            "district": np.r_[np.zeros(nx_ + ny + ng), 1, 0],
            "budget": np.r_[np.zeros(nx_ + ny + ng + 1), 1]}
    stages = []
    for goal in order:
        c = objs[goal].copy()
        if goal != "budget":   # tiny tie-break: prefer fewer lockers
            c[:nx_] += 1e-3
        r = milp(c, constraints=cons, integrality=integ, bounds=Bounds(lb, ubv),
                 options={"time_limit": 300, "mip_rel_gap": 1e-6})
        assert r.status == 0, (goal, r.message)
        val = float(objs[goal] @ r.x)
        stages.append(dict(goal=goal, achieved_deviation=round(val, 1)))
        cons.append(LinearConstraint(objs[goal][None, :], -np.inf, val + 1e-6))
    x = r.x[:nx_]
    chosen = [cand[k] for k in np.flatnonzero(x > .5)]
    covered = D.covered_nodes(ex + chosen, D_BASE)
    return dict(order=" > ".join(order), new_lockers=len(chosen), district_pct=D.pct(covered),
                by_group=coverage_by_group(D, covered).round(1).to_dict(), stages=stages,
                chosen=chosen,
                estate_lockers=int(sum(c in set(D.cand_res.site_id) for c in chosen)))


if __name__ == "__main__":
    D = Data()
    pub, res = candidate_sets(D)
    m1 = json.loads((RESULTS / "m1_summary.json").read_text())
    budget = m1["P_star"]
    runs = [solve_goal_program(D, pub + res, budget, ["equity", "district", "budget"]),
            solve_goal_program(D, pub + res, budget, ["budget", "district", "equity"]),
            solve_goal_program(D, pub + res, budget, ["equity", "budget", "district"]),
            solve_goal_program(D, pub + res, budget, ["district", "equity", "budget"])]
    rows = []
    for r in runs:
        row = dict(priority_order=r["order"], new_lockers=r["new_lockers"],
                   estate_lockers=r["estate_lockers"], district_pct=round(r["district_pct"], 1))
        row.update({f"{g} %": v for g, v in r["by_group"].items()})
        rows.append(row)
    # reference: pure MCLP at the elbow
    mc = set(m1["plan_sites"])
    ex = list(D.ex_lock.site_id) + list(D.ex_lock_res.site_id)
    cov = D.covered_nodes(ex + list(mc), D_BASE)
    ref = dict(priority_order="MCLP (coverage only), P = %d" % budget, new_lockers=len(mc),
               estate_lockers=int(sum(c in set(D.cand_res.site_id) for c in mc)),
               district_pct=round(D.pct(cov), 1))
    ref.update({f"{g} %": v for g, v in coverage_by_group(D, cov).round(1).to_dict().items()})
    tab = pd.DataFrame([ref] + rows)
    tab.to_csv(RESULTS / "m4_goal_programming.csv", index=False)
    print(tab.to_string(index=False))
    dump(dict(equity_target=EQUITY, district_target=DISTRICT, budget=budget,
              runs=[{k: v for k, v in r.items() if k != "chosen"} for r in runs]),
         "m4_summary")

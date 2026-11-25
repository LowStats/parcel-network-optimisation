"""Section 8 - how sensitive is locker-network performance to demand and to capacity?

Builds on the loss-system simulation (m3) and the Erlang B model (Ch7) for the recommended
network (existing lockers + the MCLP plan from m1).

A  Demand-performance curves. Capacity is held fixed (four configurations) while locker demand
   is scaled from 0.25x to 3x the base (0.35 parcels/hh/day x 30% share). Monte Carlo with
   independent replications and 95% confidence intervals, plus the analytic Erlang curve,
   demand elasticities and the demand "headroom" before rejection passes 2%, 5% and 10%.
B  Marginal benefit of extra capacity. Expected parcels served by a bank of K compartments is
   lambda (1 - B(K, a)). Because Erlang B is convex in K, served is concave, so adding 40-cell
   modules greedily to the bank with the largest marginal gain is optimal (marginal analysis).
   Compared with the deterministic LP view (a module adds 40/T parcels/day until demand is met),
   priced against an assumed module cost to find the economically optimal service level, and
   checked by simulation.
C  One-at-a-time (tornado) sensitivity of (i) compartments needed for <=2% rejection and
   (ii) the rejection rate of the base-sized network, to every simulation input.
D  Risk analysis: Monte Carlo over the uncertain inputs themselves (parcel rate, locker share,
   mean dwell, building-level online-shopping propensity) -> distribution of rejection and of the
   capacity requirement; a 90th-percentile ("robust") sizing.
E  Operating policies (node-level simulation): 48-hour hold limit, pick-up reminders, redirecting
   overflow to the household's second-nearest locker.
F  Precision of the simulation: 10 independent replications of the m3 scenario grid.
Outputs: results/m6_*.csv|json, figures/models/m6_*.png
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, t as student_t

from mcommon import (D_BASE, INK, INK2, LAMBDA_PER_HH, MUTED, RESULTS, SEQ, SERIES, STATUS, C,
                     Data, dump, end_labels, save, style)
from m3_simulation import (DAYS, DWELL_EMPIRICAL, DWELL_PLAN, MODULE, NB_K, SEED, SERVICE_TARGET,
                           erlang_b, ranked_choices, simulate, size_banks)
from m5_decision_analysis import COST_PER_BANK, SAVING_PER_PARCEL

MULTS = np.round(np.arange(0.25, 3.001, 0.25), 2)
REPS = 5                                  # replications per point on the demand curves
DWELL_SLOW = {1: .30, 2: .40, 3: .30}     # mean 2.0 days: residents collect late
DWELL_48H = {1: .60, 2: .40}              # 48-hour hold: day-3 parcels go back to a counter
MODULE_COST = COST_PER_BANK / 4           # HK$/yr per extra 40-compartment module (assumption)
RISK_N = 2000
SIGMA_NODE = 0.30                         # lognormal sd of building-level shopping propensity
N_MAX = 150                               # largest bank considered (modules)


def mean_dwell(dw: dict) -> float:
    return sum(d * p for d, p in dw.items())


def erlang_table(a: np.ndarray, n_max=N_MAX, module=MODULE) -> np.ndarray:
    """B(n x module, a) for n = 0..n_max, vectorised over an array of offered loads a.
    Uses the Erlang B recursion B(k) = a B(k-1) / (k + a B(k-1)). Shape (n_max+1, *a.shape)."""
    a = np.asarray(a, float)
    out = np.empty((n_max + 1,) + a.shape)
    b = np.ones_like(a)
    out[0] = b
    for k in range(1, n_max * module + 1):
        b = a * b / (k + a * b)
        if k % module == 0:
            out[k // module] = b
    return out


def modules_needed(B: np.ndarray, target=SERVICE_TARGET) -> np.ndarray:
    ok = B[1:] <= target
    n = np.argmax(ok, axis=0) + 1
    return np.where(ok.any(axis=0), n, B.shape[0] - 1)


def network_reject(lam, K, mdw) -> float:
    """Network rejection share under Poisson arrivals (Erlang B per bank)."""
    lost = sum(l * erlang_b(int(k), l * mdw) for l, k in zip(lam, K) if l > 0)
    return lost / lam.sum()


def headroom(lam, K, mdw, target) -> float:
    """Largest demand multiplier m with network rejection <= target (bisection)."""
    lo, hi = 0.0, 6.0
    if network_reject(lam * hi, K, mdw) <= target:
        return hi
    for _ in range(30):
        mid = (lo + hi) / 2
        if network_reject(lam * mid, K, mdw) <= target:
            lo = mid
        else:
            hi = mid
    return lo


def tcrit(n):
    return float(student_t.ppf(0.975, n - 1))


def simulate_policy(lam_node, first, second, K, dwell, mult=1.0, redirect=False, days=DAYS,
                    warmup=30, seed=SEED):
    """Node-level daily loss simulation. Parcels for node i go to locker first[i]; if it is
    full they are rejected, or (redirect=True) offered to the household's second-choice locker
    second[i] (-1 = none) if that bank still has a free compartment after its own deliveries."""
    rng = np.random.default_rng(seed)
    J = len(K)
    dvals = np.array(sorted(dwell))
    dprob = np.array([dwell[d] for d in dvals])
    maxd = int(dvals.max())
    release = np.zeros((maxd + 1, J), dtype=np.int64)
    occ = np.zeros(J, dtype=np.int64)
    nodes_of = [np.flatnonzero(first == j) for j in range(J)]
    has2 = second >= 0
    tot_a = tot_rej = tot_redir = 0
    for t in range(days + warmup):
        occ -= release[t % (maxd + 1)]
        release[t % (maxd + 1)] = 0
        A = rng.poisson(lam_node * mult)
        F = np.bincount(first, weights=A, minlength=J).astype(np.int64)
        acc = np.minimum(F, K - occ)
        R = F - acc
        occ += acc
        rej = R.sum()
        redirected = 0
        if redirect and rej:
            r_node = np.zeros_like(A)
            for j in np.flatnonzero(R):
                nd = nodes_of[j]
                r_node[nd] = rng.multivariate_hypergeometric(A[nd], int(R[j]))
            G = np.bincount(second[has2], weights=r_node[has2], minlength=J).astype(np.int64)
            acc2 = np.minimum(G, K - occ)
            occ += acc2
            acc = acc + acc2
            redirected = acc2.sum()
            rej -= redirected
        split = rng.multinomial(acc, dprob)
        for k, d in enumerate(dvals):
            release[(t + d) % (maxd + 1)] += split[:, k]
        if t >= warmup:
            tot_a += A.sum()
            tot_rej += rej
            tot_redir += redirected
    return dict(unmet_pct=100 * tot_rej / tot_a, redirected_per_day=tot_redir / days,
                parcels_per_day=tot_a / days)


if __name__ == "__main__":
    plt = style()
    D = Data()
    m1 = json.loads((RESULTS / "m1_summary.json").read_text())
    ex = list(D.ex_lock.site_id) + list(D.ex_lock_res.site_id)
    open_ids = ex + m1["plan_sites"]
    J = len(open_ids)
    sj = {s: k for k, s in enumerate(open_ids)}
    ranked = ranked_choices(D, open_ids)
    first_choice = ranked[ranked["rank"] == 0]
    lam = (pd.Series((D.h * LAMBDA_PER_HH).reindex(first_choice.node_id).to_numpy(),
                     index=first_choice.site_id.to_numpy()).groupby(level=0).sum()
           .reindex(open_ids).fillna(0.0).to_numpy())
    mdw = mean_dwell(DWELL_PLAN)
    per = pd.read_csv(RESULTS / "m3_per_locker.csv").set_index("site_id").reindex(open_ids)
    sized = per.modules_needed_normal.to_numpy().astype(int)
    m2plan = pd.read_csv(RESULTS / "m2_module_plan.csv", index_col=0).reindex(open_ids)
    names = [D.label(s) for s in open_ids]
    out = dict(lockers=J, locker_parcels_per_day=float(lam.sum()), mean_dwell=mdw,
               module_cost=MODULE_COST, saving_per_parcel=SAVING_PER_PARCEL)
    print(f"{J} lockers, {lam.sum():.0f} locker parcels/day")

    # ============================================================ A. demand-performance curves
    configs = {"80 compartments each": np.full(J, 80),
               "module MILP plan (+104 modules)": m2plan["104"].to_numpy() * MODULE,
               "sized for ≤2% at base demand": sized * MODULE,
               "sized for ≤2% at double demand": per["modules_needed_x2"].to_numpy() * MODULE}
    rows = []
    for ci, (cname, K) in enumerate(configs.items()):
        K = K.astype(np.int64)
        for m in MULTS:
            erl = 100 * network_reject(lam * m, K, mdw)
            for dist in ("poisson", "negbin"):
                v = []
                for r in range(REPS):
                    s = simulate(lam * m, K, DWELL_PLAN, 1.0, dist,
                                 seed=SEED + 1000 * r + int(m * 100) + 17 * ci)
                    A, Rj = s["arrivals"].sum(), s["rejected"].sum()
                    v.append((100 * Rj / A, (A - Rj) / DAYS,
                              100 * np.average(s["utilisation"], weights=K),
                              (s["overflow_prob"] > .5).sum()))
                v = np.array(v, float)
                mu, sd = v.mean(0), v.std(0, ddof=1)
                rows.append(dict(config=cname, compartments=int(K.sum()), multiplier=m,
                                 equiv_locker_share_pct=round(30 * m, 1),
                                 demand_per_day=round(lam.sum() * m), arrivals=dist,
                                 unmet_pct=mu[0], unmet_ci95=tcrit(REPS) * sd[0] / np.sqrt(REPS),
                                 served_per_day=mu[1],
                                 served_ci95=tcrit(REPS) * sd[1] / np.sqrt(REPS),
                                 utilisation_pct=mu[2], lockers_full_most_days=mu[3],
                                 erlang_unmet_pct=erl))
    curves = pd.DataFrame(rows).round(2)
    curves.to_csv(RESULTS / "m6_demand_curves.csv", index=False)
    print(curves[curves.arrivals == "poisson"][["config", "multiplier", "unmet_pct",
                                                 "unmet_ci95", "erlang_unmet_pct",
                                                 "served_per_day"]].to_string(index=False))

    # elasticities at the base point (Erlang, +-5%) and headroom
    hr = []
    for cname, K in configs.items():
        K = K.astype(np.int64)
        r0 = network_reject(lam, K, mdw)
        rp, rm = network_reject(lam * 1.05, K, mdw), network_reject(lam * .95, K, mdw)
        sv = lambda r, m: lam.sum() * m * (1 - r)          # noqa: E731
        e_served = (np.log(sv(rp, 1.05)) - np.log(sv(rm, .95))) / (np.log(1.05) - np.log(.95))
        e_rej = (np.log(rp * 1.05) - np.log(rm * .95)) / (np.log(1.05) - np.log(.95))
        hr.append(dict(config=cname, compartments=int(K.sum()), unmet_pct_base=100 * r0,
                       elasticity_served=e_served, elasticity_rejected=e_rej,
                       unmet_pct_at_plus10=100 * network_reject(lam * 1.1, K, mdw),
                       unmet_pct_at_plus25=100 * network_reject(lam * 1.25, K, mdw),
                       headroom_2pct=headroom(lam, K, mdw, .02),
                       headroom_5pct=headroom(lam, K, mdw, .05),
                       headroom_10pct=headroom(lam, K, mdw, .10)))
    hr = pd.DataFrame(hr).round(3)
    hr.to_csv(RESULTS / "m6_headroom.csv", index=False)
    print(hr.to_string(index=False))

    # ============================================================ B. marginal value of capacity
    a = lam * mdw
    B = erlang_table(a)                                    # (N_MAX+1, J)
    served = lam[None, :] * (1 - B)                        # expected served/day with n modules
    cols = np.arange(J)
    n = np.full(J, 2)                                      # start: 80 compartments everywhere
    tot = served[n, cols].sum()
    greedy = [dict(step=0, extra_modules=0, compartments=int(n.sum() * MODULE),
                   marginal_parcels=np.nan, served=tot, unmet_pct=100 * (1 - tot / lam.sum()),
                   site_id="", name="")]
    plans_at = {}
    budgets = (26, 52, 104, 156, 260)
    step = 0
    while True:
        gain = served[np.minimum(n + 1, N_MAX), cols] - served[n, cols]
        j = int(np.argmax(gain))
        if gain[j] < 0.05 or step >= 600:
            break
        step += 1
        n[j] += 1
        tot += gain[j]
        greedy.append(dict(step=step, extra_modules=step, compartments=int(n.sum() * MODULE),
                           marginal_parcels=gain[j], served=tot,
                           unmet_pct=100 * (1 - tot / lam.sum()), site_id=open_ids[j],
                           name=names[j]))
        if step in budgets:
            plans_at[step] = n.copy()
    greedy = pd.DataFrame(greedy)
    greedy.to_csv(RESULTS / "m6_marginal_capacity.csv", index=False)
    # deterministic (fluid / LP) view: a module adds 40/T parcels/day until the bank meets demand
    cap_mod = MODULE / mdw
    need_fluid = np.maximum(lam - 2 * cap_mod, 0)
    fl = []
    for j in range(J):
        rem = need_fluid[j]
        while rem > 1e-9:
            g = min(cap_mod, rem)
            fl.append(g)
            rem -= g
    fluid = np.sort(np.array(fl))[::-1]
    out["fluid_modules_to_serve_all"] = int(len(fluid))
    out["fluid_compartments_to_serve_all"] = int((2 * J + len(fluid)) * MODULE)

    # economics: add modules while 365 x saving x marginal parcels >= module cost
    econ = []
    for sv_ in (2.0, 3.0, 5.0, 8.0, 10.0):
        for mc in (MODULE_COST / 2, MODULE_COST, MODULE_COST * 2):
            thr = mc / (365 * sv_)
            ok = greedy[(greedy.step > 0) & (greedy.marginal_parcels >= thr)]
            last = ok.iloc[-1] if len(ok) else greedy.iloc[0]
            econ.append(dict(saving_per_parcel=sv_, module_cost=mc,
                             breakeven_parcels_per_module=thr,
                             extra_modules=int(last.extra_modules),
                             compartments=int(last.compartments),
                             unmet_pct=float(last.unmet_pct),
                             net_benefit=float(365 * sv_ * (last.served - greedy.served.iloc[0])
                                               - mc * last.extra_modules)))
    econ = pd.DataFrame(econ).round(2)
    econ.to_csv(RESULTS / "m6_economic_capacity.csv", index=False)
    print(econ.to_string(index=False))
    thr0 = MODULE_COST / (365 * SAVING_PER_PARCEL)
    eopt = greedy[(greedy.step > 0) & (greedy.marginal_parcels >= thr0)].iloc[-1]
    n_opt = np.full(J, 2)
    for sid in greedy[(greedy.step > 0) & (greedy.step <= eopt.step)].site_id:
        n_opt[sj[sid]] += 1
    s_opt = simulate(lam, n_opt * MODULE, DWELL_PLAN, seed=SEED + 5)
    out["econ_opt"] = dict(extra_modules=int(eopt.extra_modules),
                           compartments=int(eopt.compartments),
                           unmet_pct_erlang=float(eopt.unmet_pct),
                           unmet_pct_simulated=float(100 * s_opt["rejected"].sum()
                                                     / s_opt["arrivals"].sum()),
                           breakeven_parcels_per_module=thr0)
    g2 = greedy[greedy.unmet_pct <= 100 * SERVICE_TARGET]
    out["greedy_for_2pct"] = dict(compartments=int(g2.compartments.iloc[0]),
                                  last_marginal=float(g2.marginal_parcels.iloc[0])) \
        if len(g2) else None

    # MILP (deterministic, m2) vs greedy (stochastic) plans at the same module budgets
    cmp_ = []
    for b in budgets:
        Km = m2plan[str(b)].to_numpy() * MODULE
        Kg = plans_at[b] * MODULE if b in plans_at else None
        cmp_.append(dict(extra_modules=b,
                         milp_plan_unmet_pct=100 * network_reject(lam, Km, mdw),
                         greedy_plan_unmet_pct=100 * network_reject(lam, Kg, mdw)
                         if Kg is not None else np.nan))
    cmp_ = pd.DataFrame(cmp_).round(2)
    cmp_.to_csv(RESULTS / "m6_milp_vs_greedy.csv", index=False)
    print(cmp_.to_string(index=False))

    # per-locker value of the next module
    alloc = pd.read_csv(RESULTS / "m2_locker_allocation.csv").set_index("site_id")
    pl = pd.DataFrame({"site_id": open_ids, "name": names, "lambda_per_day": lam,
                       "next_module_at_80": served[3, cols] - served[2, cols],
                       "modules_sized": sized,
                       "next_module_at_sized": served[np.minimum(sized + 1, N_MAX), cols]
                       - served[sized, cols],
                       "lp_shadow_price": alloc.shadow_price_m_per_parcel.reindex(open_ids)
                       .to_numpy()})
    pl["value_next_module_at_80_hkd"] = 365 * SAVING_PER_PARCEL * pl.next_module_at_80
    pl = pl.sort_values("next_module_at_80", ascending=False).round(2)
    pl.to_csv(RESULTS / "m6_module_value_by_locker.csv", index=False)
    out["lp_shadow_price_spread"] = [float(pl.lp_shadow_price.min()),
                                     float(pl.lp_shadow_price.max())]
    out["lp_shadow_price_binding"] = [float(pl.lp_shadow_price[pl.lp_shadow_price > 0].min()),
                                      float(pl.lp_shadow_price.max())]

    # ============================================================ C. tornado (one at a time)
    def lam_for(rate=C.PARCELS_PER_HOUSEHOLD_PER_DAY, share=C.LOCKER_SHARE, radius=D_BASE):
        fc = ranked_choices(D, open_ids, radius).query("rank == 0")
        v = pd.Series((D.h * rate * share).reindex(fc.node_id).to_numpy(),
                      index=fc.site_id.to_numpy()).groupby(level=0).sum()
        return v.reindex(open_ids).fillna(0.0).to_numpy()

    def requirement(rate=C.PARCELS_PER_HOUSEHOLD_PER_DAY, share=C.LOCKER_SHARE, radius=D_BASE,
                    dwell=DWELL_PLAN, dist="poisson", nb_k=NB_K, target=SERVICE_TARGET,
                    module=MODULE):
        lm = lam_for(rate, share, radius)
        return int((size_banks(lm, dwell, target, dist=dist, module=module, nb_k=nb_k)
                    * module).sum())

    def rejection(rate=C.PARCELS_PER_HOUSEHOLD_PER_DAY, share=C.LOCKER_SHARE, radius=D_BASE,
                  dwell=DWELL_PLAN, dist="poisson", nb_k=NB_K, **_):
        lm = lam_for(rate, share, radius)
        v = []
        for r in range(3):          # common random numbers across parameter values
            s = simulate(lm, sized * MODULE, dwell, 1.0, dist, seed=SEED + 300 + r, nb_k=nb_k)
            v.append(100 * s["rejected"].sum() / s["arrivals"].sum())
        return float(np.mean(v))

    specs = [
        ("Parcels per household per day", "0.20", "0.50", dict(rate=.20), dict(rate=.50)),
        ("Locker share of parcels", "15%", "45%", dict(share=.15), dict(share=.45)),
        ("Mean dwell time", "1.22 d (reminders)", "2.0 d (slow pick-up)",
         dict(dwell=DWELL_EMPIRICAL), dict(dwell=DWELL_SLOW)),
        ("Catchment radius for locker users", "250 m", "400 m", dict(radius=250),
         dict(radius=400)),
        ("Day-to-day burstiness", "Poisson", "Neg. binomial k=5", dict(),
         dict(dist="negbin", nb_k=5.0)),
        ("Service target (max rejection)", "5%", "1%", dict(target=.05), dict(target=.01)),
        ("Module size", "20 cells", "60 cells", dict(module=20), dict(module=60)),
    ]
    base_req = requirement()
    base_rej = rejection()
    tor = []
    for lab, lo_l, hi_l, lo, hi in specs:
        rq_lo, rq_hi = requirement(**lo), requirement(**hi)
        rj = ("target" not in lo and "module" not in lo)
        tor.append(dict(parameter=lab, low=lo_l, high=hi_l, requirement_low=rq_lo,
                        requirement_high=rq_hi,
                        rejection_low=rejection(**lo) if rj else np.nan,
                        rejection_high=rejection(**hi) if rj else np.nan))
    tor = pd.DataFrame(tor)
    tor["requirement_swing"] = (tor.requirement_high - tor.requirement_low).abs()
    tor["rejection_swing"] = (tor.rejection_high - tor.rejection_low).abs()
    tor = tor.sort_values("requirement_swing", ascending=False).round(2)
    tor.to_csv(RESULTS / "m6_tornado.csv", index=False)
    out.update(base_requirement=base_req, base_rejection_sized=base_rej)
    print("base requirement", base_req, "base rejection", round(base_rej, 2))
    print(tor.to_string(index=False))

    # burstiness: negative-binomial dispersion k (Var = m + m^2/k)
    disp = []
    for k in (None, 50.0, 20.0, 10.0, 5.0, 2.0, 1.0):
        kw = dict(dist="negbin", nb_k=k) if k else dict()
        disp.append(dict(arrivals="Poisson" if k is None else f"NB k = {k:g}",
                         cv_at_100_per_day=round(np.sqrt((100 + (100 ** 2 / k if k else 0)))
                                                 / 100, 3),
                         requirement=requirement(**kw), rejection_sized_pct=rejection(**kw)))
    disp = pd.DataFrame(disp).round(2)
    disp.to_csv(RESULTS / "m6_dispersion.csv", index=False)
    print(disp.to_string(index=False))

    # ============================================================ D. risk analysis
    rng = np.random.default_rng(SEED + 99)
    nodes = list(first_choice.node_id)
    A_mat = np.zeros((len(nodes), J))
    A_mat[np.arange(len(nodes)), first_choice.site_id.map(sj).to_numpy()] = 1.0
    h_n = D.h.reindex(nodes).to_numpy()
    rate = rng.triangular(.20, .35, .50, RISK_N)
    share = rng.triangular(.15, .30, .45, RISK_N)
    mdw_d = rng.triangular(1.22, 1.5, 2.0, RISK_N)
    risk = {}
    for lab, sig in (("all inputs uncertain", SIGMA_NODE), ("no building-level variation", 0.0)):
        u = rng.lognormal(-sig ** 2 / 2, sig, (RISK_N, len(nodes))) if sig else \
            np.ones((RISK_N, len(nodes)))
        lam_d = ((h_n[None, :] * u) @ A_mat) * (rate * share)[:, None]       # (draws, J)
        Bd = erlang_table(lam_d * mdw_d[:, None])
        rej_d = (lam_d * Bd[sized, np.arange(RISK_N)[:, None], cols[None, :]]).sum(1) \
            / lam_d.sum(1)
        need_d = modules_needed(Bd)                                           # (draws, J)
        req_d = need_d.sum(1) * MODULE
        risk[lab] = dict(rej=100 * rej_d, req=req_d, need=need_d, lam=lam_d, B=Bd)
    R0 = risk["all inputs uncertain"]
    robust = np.percentile(R0["need"], 90, axis=0, method="higher").astype(int)
    rej_rob = 100 * (R0["lam"] * R0["B"][robust, np.arange(RISK_N)[:, None], cols[None, :]]
                     ).sum(1) / R0["lam"].sum(1)
    rk = []
    for lab, r in risk.items():
        rk.append(dict(case=lab, sizing="base sizing",
                       compartments=int(sized.sum() * MODULE),
                       rejection_p5=np.percentile(r["rej"], 5),
                       rejection_median=np.median(r["rej"]),
                       rejection_p95=np.percentile(r["rej"], 95),
                       prob_rejection_over_2=100 * (r["rej"] > 2).mean(),
                       prob_rejection_over_5=100 * (r["rej"] > 5).mean(),
                       prob_rejection_over_10=100 * (r["rej"] > 10).mean(),
                       requirement_median=np.median(r["req"]),
                       requirement_p90=np.percentile(r["req"], 90),
                       requirement_p95=np.percentile(r["req"], 95)))
    rk.append(dict(case="all inputs uncertain", sizing="robust (P90 per bank)",
                   compartments=int(robust.sum() * MODULE),
                   rejection_p5=np.percentile(rej_rob, 5), rejection_median=np.median(rej_rob),
                   rejection_p95=np.percentile(rej_rob, 95),
                   prob_rejection_over_2=100 * (rej_rob > 2).mean(),
                   prob_rejection_over_5=100 * (rej_rob > 5).mean(),
                   prob_rejection_over_10=100 * (rej_rob > 10).mean()))
    rk = pd.DataFrame(rk).round(2)
    rk.to_csv(RESULTS / "m6_risk.csv", index=False)
    print(rk.to_string(index=False))
    sp = []
    for lab, x in (("parcels per household", rate), ("locker share", share),
                   ("mean dwell", mdw_d)):
        sp.append(dict(input=lab, spearman_with_rejection=spearmanr(x, R0["rej"])[0],
                       spearman_with_requirement=spearmanr(x, R0["req"])[0]))
    sp = pd.DataFrame(sp).round(3)
    sp.to_csv(RESULTS / "m6_risk_drivers.csv", index=False)
    print(sp.to_string(index=False))
    out["risk_draws"] = RISK_N
    out["sigma_node"] = SIGMA_NODE

    # ============================================================ E. operating policies
    pairs = ranked.copy()
    nodes_all = sorted(pairs.node_id.unique())
    ni = {nd: k for k, nd in enumerate(nodes_all)}
    first = np.full(len(nodes_all), -1)
    second = np.full(len(nodes_all), -1)
    walk1 = np.full(len(nodes_all), np.nan)
    walk2 = np.full(len(nodes_all), np.nan)
    for row in pairs[pairs["rank"] <= 1].itertuples():
        k = ni[row.node_id]
        if row.rank == 0:
            first[k], walk1[k] = sj[row.site_id], row.walk_m
        else:
            second[k], walk2[k] = sj[row.site_id], row.walk_m
    lam_node = (D.h * LAMBDA_PER_HH).reindex(nodes_all).to_numpy()
    hh2 = D.h.reindex(nodes_all).to_numpy()
    out["share_with_second_choice"] = float(100 * hh2[second >= 0].sum() / hh2.sum())
    out["extra_walk_second_choice_m"] = float(np.nanmedian(walk2 - walk1))
    pol_specs = [("as planned (dwell 1/2/3 d = 60/30/10%)", DWELL_PLAN, False),
                 ("48-hour hold limit", DWELL_48H, False),
                 ("pick-up reminders (84% within 24 h)", DWELL_EMPIRICAL, False),
                 ("redirect overflow to 2nd-nearest locker", DWELL_PLAN, True),
                 ("reminders + redirect", DWELL_EMPIRICAL, True)]
    pol_ctx = [("MILP plan (+104 modules), normal demand", m2plan["104"].to_numpy() * MODULE, 1.0),
               ("sized for ≤2%, +50% surge", sized * MODULE, 1.5)]
    pol = []
    for cname, K, mult in pol_ctx:
        for pname, dw, red in pol_specs:
            v = [simulate_policy(lam_node, first, second, K.astype(np.int64), dw, mult, red,
                                 seed=SEED + 40 + r) for r in range(3)]
            pol.append(dict(context=cname, policy=pname,
                            unmet_pct=np.mean([x["unmet_pct"] for x in v]),
                            redirected_per_day=np.mean([x["redirected_per_day"] for x in v]),
                            parcels_per_day=np.mean([x["parcels_per_day"] for x in v])))
    pol = pd.DataFrame(pol).round(2)
    pol.to_csv(RESULTS / "m6_policies.csv", index=False)
    print(pol.to_string(index=False))

    # ============================================================ F. replication precision
    grid = pd.read_csv(RESULTS / "m3_scenarios.csv")
    cfgK = {"80 compartments each (plan 'medium')": np.full(J, 80),
            "LP/MILP module plan (+104 modules)": m2plan["104"].to_numpy() * MODULE,
            "sized for <=2% rejection": sized * MODULE}
    rep = []
    NREP = 10
    for cname, K in cfgK.items():
        for dist in ("poisson", "negbin"):
            for surge in C.PEAK_MULTIPLIERS:
                v = np.array([100 * (s := simulate(lam, K.astype(np.int64), DWELL_PLAN, surge,
                                                   dist, seed=SEED + 500 + r))["rejected"].sum()
                              / s["arrivals"].sum() for r in range(NREP)])
                rep.append(dict(config=cname, arrivals=dist, surge=surge, replications=NREP,
                                unmet_mean=v.mean(), unmet_sd=v.std(ddof=1),
                                ci95_half_width=tcrit(NREP) * v.std(ddof=1) / np.sqrt(NREP),
                                single_run_m3=float(grid[(grid.config == cname)
                                                         & (grid.arrivals == dist)
                                                         & (grid.surge == surge)]
                                                    .unmet_pct.iloc[0])))
    rep = pd.DataFrame(rep).round(3)
    rep.to_csv(RESULTS / "m6_replications.csv", index=False)
    print(rep.to_string(index=False))
    out["max_ci95_half_width"] = float(rep.ci95_half_width.max())
    dump(out, "m6_summary")

    # ============================================================ figures
    lab_short = {"80 compartments each": "80 each (4,160)",
                 "module MILP plan (+104 modules)": "MILP +104 modules (8,320)",
                 "sized for ≤2% at base demand": f"sized at base ({int(sized.sum() * 40):,})",
                 "sized for ≤2% at double demand": "sized at ×2 "
                 f"({int(per.modules_needed_x2.sum() * 40):,})"}
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.4))
    cp = curves[curves.arrivals == "poisson"]
    for k, cname in enumerate(configs):
        c = cp[cp.config == cname]
        for ax, y, ci in ((axs[0], c.unmet_pct, c.unmet_ci95),
                          (axs[1], c.served_per_day, c.served_ci95)):
            ax.plot(c.multiplier, y, color=SERIES[k], lw=2, marker="o", ms=3.5)
            ax.fill_between(c.multiplier, y - ci, y + ci, color=SERIES[k], alpha=.18, lw=0)
    axs[1].plot(MULTS, lam.sum() * MULTS, color=MUTED, ls="--", lw=1.2)
    axs[1].text(2.95, lam.sum() * 2.95, "all demand", fontsize=8, color=INK2, ha="right",
                va="bottom")
    axs[0].axhline(2, color=MUTED, lw=1, ls=":")
    axs[0].text(1.35, 3.5, "2% service target", fontsize=8, color=INK2)
    for ax in axs:
        ax.axvline(1, color=MUTED, lw=1, ls="--")
        ax.set_xlabel("Locker demand relative to base (1 = 30% share; 2 = 60%)")
    axs[0].set_ylabel("Parcels rejected (%)")
    axs[0].set_ylim(0, 100)
    axs[0].set_title("Rejection rate")
    axs[1].set_ylabel("Parcels served per day")
    axs[1].set_title("Parcels served")
    end_labels(axs[1], 3.05, {lab_short[cn]: cp[(cp.config == cn) & (cp.multiplier == 3)]
                              .served_per_day.iloc[0] for cn in configs})
    axs[1].set_xlim(0.2, 4.0)
    fig.suptitle("Network performance as locker demand changes (Poisson, 5 replications, 95% CI)",
                 x=.01, ha="left", fontweight="bold", fontsize=12)
    fig.tight_layout()
    save(fig, "m6_demand_performance")

    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axs[0]
    g = greedy[greedy.step > 0]
    ax.plot(g.extra_modules, g.marginal_parcels, color=SERIES[0], lw=2,
            label="stochastic (Erlang B, greedy)")
    ax.step(np.arange(1, len(fluid) + 1), fluid, where="post", color=SERIES[1], lw=1.6,
            label="deterministic (LP view)")
    ax.axhline(thr0, color=INK2, lw=1, ls="--")
    ax.text(5, thr0 - 1.6, f"break-even {thr0:.1f} parcels/day "
            f"(HK\\${MODULE_COST:,.0f}/yr ÷ 365 ÷ HK\\${SAVING_PER_PARCEL:.0f})", ha="left",
            fontsize=8, color=INK2)
    ax.set_xlabel("Extra 40-compartment modules (added best-first)")
    ax.set_ylabel("Extra parcels served per day")
    ax.set_title("Marginal benefit of the next module")
    ax.legend(loc="lower left", fontsize=8)
    ax = axs[1]
    ax.plot(greedy.compartments, greedy.unmet_pct, color=SERIES[0], lw=2)
    ax.axhline(100 * SERVICE_TARGET, color=MUTED, lw=1, ls=":")
    ax.scatter([eopt.compartments], [eopt.unmet_pct], color=SERIES[1], zorder=5, s=36)
    ax.annotate(f"economic optimum\n{int(eopt.compartments):,} cells, "
                f"{eopt.unmet_pct:.1f}% rejected", (eopt.compartments, eopt.unmet_pct),
                xytext=(eopt.compartments - 600, eopt.unmet_pct + 30), fontsize=8, color=INK,
                ha="right",
                arrowprops=dict(arrowstyle="-", color=MUTED, lw=.8))
    if out["greedy_for_2pct"]:
        c2 = out["greedy_for_2pct"]["compartments"]
        ax.scatter([c2], [2], color=INK, zorder=5, s=28)
        ax.annotate(f"network ≤2%\n{c2:,} cells", (c2, 2), xytext=(c2 - 2600, 12), fontsize=8,
                    ha="right",
                    color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, lw=.8))
    ax.set_xlabel("Total compartments in the network")
    ax.set_ylabel("Parcels rejected (%)")
    ax.set_title("Rejection vs capacity (best-first expansion)")
    ax.set_ylim(0, 80)
    fig.tight_layout()
    save(fig, "m6_marginal_capacity")

    fig, axs = plt.subplots(1, 2, figsize=(12.5, 4.4), gridspec_kw=dict(wspace=.55))
    for ax, key, base_v, title, fmt in (
            (axs[0], "requirement", base_req, "Compartments needed for ≤2% rejection",
             lambda v: f"{v:,.0f}"),
            (axs[1], "rejection", base_rej, "Rejection with base-sized banks (%)",
             lambda v: f"{v:.1f}")):
        t = tor.dropna(subset=[f"{key}_low"]).copy()
        t["sw"] = (t[f"{key}_high"] - t[f"{key}_low"]).abs()
        t = t.sort_values("sw")
        y = np.arange(len(t))
        for yi, (_, r) in zip(y, t.iterrows()):
            lo, hi = r[f"{key}_low"], r[f"{key}_high"]
            ax.barh(yi, lo - base_v, left=base_v, color=SERIES[0], height=.6)
            ax.barh(yi, hi - base_v, left=base_v, color=SERIES[1], height=.6)
            if key == "rejection":      # low values are all ~0: one label on the right
                ax.text(max(lo, hi), yi, f" {r.high}: {hi:.1f}%   ({r.low}: {lo:.1f}%)",
                        ha="left", va="center", fontsize=7.5, color=INK2)
                continue
            ax.text(min(lo, hi), yi, f"{r.low if lo < hi else r.high} ", ha="right",
                    va="center", fontsize=7.5, color=INK2)
            ax.text(max(lo, hi), yi, f" {r.high if hi > lo else r.low}", ha="left",
                    va="center", fontsize=7.5, color=INK2)
        ax.axvline(base_v, color=INK, lw=1)
        ax.set_yticks(y, t.parameter, fontsize=8)
        ax.set_title(title, fontsize=11)
        lo_x = min(t[f"{key}_low"].min(), t[f"{key}_high"].min())
        hi_x = max(t[f"{key}_low"].max(), t[f"{key}_high"].max())
        pad = (hi_x - lo_x) * .45
        ax.set_xlim(-(hi_x - lo_x) * .03 if key == "rejection" else lo_x - pad,
                    hi_x * 1.9 if key == "rejection" else hi_x + pad)
        ax.text(base_v, -.9, f" base {fmt(base_v)}", ha="left", fontsize=8, color=INK)
        ax.set_ylim(-1.2, len(t) - .4)
    fig.suptitle("Tornado: one input at a time (blue = low value, orange = high value)", x=.01,
                 ha="left", fontweight="bold", fontsize=12)
    fig.subplots_adjust(top=.84)
    save(fig, "m6_tornado")

    fig, axs = plt.subplots(1, 2, figsize=(11, 4))
    ax = axs[0]
    xs = np.linspace(0, 40, 401)
    for v, col, lab in ((R0["rej"], SERIES[0], f"base sizing ({int(sized.sum() * 40):,} cells)"),
                        (rej_rob, SERIES[1], f"robust P90 sizing ({int(robust.sum() * 40):,})")):
        ax.plot(xs, [100 * (v > x).mean() for x in xs], color=col, lw=2, label=lab)
    ax.axvline(2, color=INK2, lw=1, ls="--")
    ax.text(2.4, 92, "2% target", fontsize=8, color=INK2)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Network rejection, x (%)")
    ax.set_ylabel("Chance rejection exceeds x (%)")
    ax.set_title("Rejection risk when inputs are uncertain")
    ax.legend(loc="upper right")
    ax = axs[1]
    ax.hist(R0["req"], bins=40, color=SERIES[2], alpha=.9)
    ax.axvline(base_req, color=INK, lw=1)
    ax.text(base_req, ax.get_ylim()[1] * .92, f" base {base_req:,}", fontsize=8, color=INK)
    p90 = np.percentile(R0["req"], 90)
    ax.axvline(p90, color=INK2, lw=1, ls="--")
    ax.text(p90, ax.get_ylim()[1] * .8, f" P90 {p90:,.0f}", fontsize=8, color=INK2)
    ax.set_xlabel("Compartments needed for ≤2% rejection")
    ax.set_title("Capacity requirement when inputs are uncertain")
    fig.tight_layout()
    save(fig, "m6_risk")

    fig, ax = plt.subplots(figsize=(9, 4))
    pn = [p for p, _, _ in pol_specs]
    x = np.arange(len(pn))
    for k, (cname, _, _) in enumerate(pol_ctx):
        v = pol[pol.context == cname].set_index("policy").unmet_pct.reindex(pn).to_numpy()
        ax.bar(x + (k - .5) * .38, v, width=.36, color=SERIES[k], label=cname)
        for xi, vi in zip(x, v):
            ax.text(xi + (k - .5) * .38, vi + .6, f"{vi:.1f}", ha="center", fontsize=8,
                    color=INK2)
    ax.set_xticks(x, [p.replace(" (", "\n(").replace("redirect overflow", "redirect overflow\n")
                      for p in pn], fontsize=8)
    ax.set_ylabel("Parcels rejected (%)")
    ax.set_ylim(0, 54)
    ax.set_title("Operating policies: same hardware, different rules")
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    save(fig, "m6_policies")
    print("done")

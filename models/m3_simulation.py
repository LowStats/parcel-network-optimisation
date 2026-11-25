"""Section 3 - stochastic simulation of locker overflow (Ch7 queuing + Monte Carlo simulation).

Each locker bank is a loss system: parcels arrive, occupy one compartment until collected, and
are REJECTED (redirected to door delivery / a staffed counter) when every compartment is full.

Monte Carlo, daily time step (plan: 1,000 simulated days after a 30-day warm-up):
  1. morning: parcels whose dwell time has ended are collected -> compartments freed
  2. A_t parcels arrive:  Poisson(lambda_j)  or  Negative Binomial (same mean, overdispersed:
     Var = mean + mean^2/k, k = NB_K) to mimic e-commerce bursts
  3. accepted = min(A_t, free compartments); the rest are rejected
  4. each accepted parcel draws a dwell time from the dwell distribution via cumulative
     probabilities / random-number intervals (plan: 1 day 60%, 2 days 30%, 3 days 10%)
Metrics per locker: overflow probability (% of days the bank is full), unmet-demand rate
(% parcels rejected), average utilisation (occupied / compartments).

Analytic check (Ch7 birth-death / generalised Poisson model): with Poisson arrivals, a bank of K
compartments is an M/G/K/K loss system; lambda_n = lambda (n < K), mu_n = n*mu gives
p_n = (a^n/n!) / sum_k a^k/k!,  a = lambda * E[dwell];  blocking = p_K (Erlang B, insensitive to
the dwell distribution).

lambda_j: every household within 300 m of an open locker sends its locker-bound parcels
(h_i x parcels/hh/day x locker share) to its nearest open locker.
Outputs: results/m3_*.csv|json, figures/models/m3_*.png
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from mcommon import (D_BASE, INK, INK2, LAMBDA_PER_HH, MUTED, RESULTS, SEQ, SERIES, STATUS, C,
                     Data, dump, save, style)

DAYS, WARMUP, SEED = 1000, 30, 20261125
NB_K = 5.0                      # NegBin shape: Var = m + m^2/k  (k=5 -> strongly overdispersed)
SERVICE_TARGET = 0.02           # size banks so <= 2% of parcels are rejected
MODULE = 40                     # compartments per module
DWELL_PLAN = {1: .60, 2: .30, 3: .10}
DWELL_EMPIRICAL = {1: .84, 2: .10, 3: .06}   # Urban Freight Lab: 84% within 24 h (rest <= 3 d)


def erlang_b(K: int, a: float) -> float:
    b = 1.0
    for k in range(1, K + 1):
        b = a * b / (k + a * b)
    return b


def ranked_choices(D: Data, open_ids, radius=D_BASE) -> pd.DataFrame:
    """Every (node, open locker) pair within the radius, ranked by preference: a residents-only
    locker first for its own estate, then public lockers by walking distance (rank 0 = first)."""
    pairs = D.L[D.L.site_id.isin(open_ids) & (D.L.walk_m <= radius)]
    ro = pairs.site_id.isin(D.resonly)
    pairs = pairs[~ro | (pairs.euclid_m <= 25)]
    pairs = pairs.assign(pr=np.where(pairs.site_id.isin(D.resonly), 0, 1)) \
        .sort_values(["node_id", "pr", "walk_m"])
    return pairs.assign(rank=pairs.groupby("node_id").cumcount())


def assign_lambda(D: Data, open_ids, radius=D_BASE) -> pd.Series:
    best = ranked_choices(D, open_ids, radius).query("rank == 0")
    lam = (D.h * LAMBDA_PER_HH).reindex(best.node_id).to_numpy()
    return pd.Series(lam, index=best.site_id.to_numpy()).groupby(level=0).sum() \
        .reindex(open_ids).fillna(0.0)


def simulate(lam: np.ndarray, K: np.ndarray, dwell: dict, surge=1.0, dist="poisson",
             days=DAYS, warmup=WARMUP, seed=SEED, peak_days=None, nb_k=NB_K):
    """Vectorised daily Monte Carlo over all lockers. peak_days: (start, length, multiplier)
    applies a temporary surge (e.g. Double 11) on top of `surge`."""
    rng = np.random.default_rng(seed)
    J = len(lam)
    dvals = np.array(sorted(dwell))
    dprob = np.array([dwell[d] for d in dvals])
    maxd = int(dvals.max())
    release = np.zeros((maxd + 1, J), dtype=np.int64)    # ring buffer of future pick-ups
    occ = np.zeros(J, dtype=np.int64)
    T = days + warmup
    arr_tot = np.zeros(J)
    rej_tot = np.zeros(J)
    full_days = np.zeros(J)
    occ_sum = np.zeros(J)
    daily = []
    for t in range(T):
        occ -= release[t % (maxd + 1)]
        release[t % (maxd + 1)] = 0
        m = lam * surge
        if peak_days and peak_days[0] <= t - warmup < peak_days[0] + peak_days[1]:
            m = m * peak_days[2]
        if dist == "poisson":
            A = rng.poisson(m)
        else:
            p = nb_k / (nb_k + np.maximum(m, 1e-9))
            A = rng.negative_binomial(nb_k, p)
        acc = np.minimum(A, K - occ)
        rej = A - acc
        occ += acc
        # dwell of accepted parcels: multinomial split over 1/2/3 days
        split = rng.multinomial(acc, dprob)                  # (J, number of dwell values)
        for k, d in enumerate(dvals):
            release[(t + d) % (maxd + 1)] += split[:, k]
        if t >= warmup:
            arr_tot += A
            rej_tot += rej
            full_days += (occ >= K)
            occ_sum += occ
            daily.append((t - warmup, A.sum(), rej.sum(), occ.sum()))
    n = days
    return dict(arrivals=arr_tot, rejected=rej_tot, overflow_prob=full_days / n,
                unmet_rate=np.divide(rej_tot, arr_tot, out=np.zeros(J), where=arr_tot > 0),
                utilisation=occ_sum / n / K,
                daily=pd.DataFrame(daily, columns=["day", "arrivals", "rejected", "occupied"]))


def size_banks(lam, dwell, target=SERVICE_TARGET, surge=1.0, dist="poisson", max_mod=200,
               module=MODULE, nb_k=NB_K):
    """Smallest number of `module`-compartment modules per bank meeting the rejection target
    (Erlang B start, confirmed by simulation)."""
    mean_d = sum(d * p for d, p in dwell.items())
    mods = np.array([next(m for m in range(1, max_mod + 1)
                          if erlang_b(m * module, l * surge * mean_d) <= target) if l > 0 else 1
                     for l in lam])
    for _ in range(60):  # simulation check; bump banks that miss the target
        s = simulate(lam, mods * module, dwell, surge, dist, days=400, seed=SEED + 7, nb_k=nb_k)
        bad = s["unmet_rate"] > target
        if not bad.any():
            break
        mods = mods + bad.astype(int)
    return mods


def ch7_demo_table(lam_j: float, dwell: dict, days=10, seed=7):
    """Lecture-style Monte Carlo table: probabilities -> cumulative -> random-number intervals."""
    from scipy.stats import poisson
    lo, hi = int(max(0, poisson.ppf(.005, lam_j))), int(poisson.ppf(.995, lam_j))
    vals = np.arange(lo, hi + 1)
    pr = poisson.pmf(vals, lam_j)
    pr = pr / pr.sum()
    # group into ~8 bands for a readable table
    bands = np.array_split(np.arange(len(vals)), 8)
    arr_rows, cum = [], 0.0
    for b in bands:
        p = pr[b].sum()
        mid = int(round(np.average(vals[b], weights=pr[b])))
        start = int(round(cum * 100)) + 1
        cum += p
        end = int(round(cum * 100))
        arr_rows.append(dict(arrivals=f"{vals[b][0]}-{vals[b][-1]}", representative=mid,
                             probability=round(p, 3), cumulative=round(cum, 3),
                             rn_interval=f"{start:02d}-{end % 100:02d}"))
    cum, dw_rows = 0.0, []
    for d, p in dwell.items():
        start = int(round(cum * 100)) + 1
        cum += p
        dw_rows.append(dict(dwell_days=d, probability=p, cumulative=round(cum, 2),
                            rn_interval=f"{start:02d}-{int(round(cum * 100)) % 100:02d}"))
    rng = np.random.default_rng(seed)
    rn = rng.integers(1, 101, size=(days, 2))
    sim = []
    for t in range(days):
        r1 = rn[t, 0]
        a = next(r["representative"] for r in arr_rows
                 if r1 <= int(r["rn_interval"].split("-")[1] or 100) or
                 r["rn_interval"].endswith("00"))
        r2 = rn[t, 1]
        dsel = next(r["dwell_days"] for r in dw_rows
                    if r2 <= (int(r["rn_interval"].split("-")[1]) or 100))
        sim.append(dict(day=t + 1, rn_arrivals=f"{r1 % 100:02d}", arrivals=a,
                        rn_dwell=f"{r2 % 100:02d}", dwell_of_typical_parcel=dsel))
    return arr_rows, dw_rows, sim


if __name__ == "__main__":
    plt = style()
    D = Data()
    m1 = json.loads((RESULTS / "m1_summary.json").read_text())
    ex = list(D.ex_lock.site_id) + list(D.ex_lock_res.site_id)
    open_ids = ex + m1["plan_sites"]
    lam_s = assign_lambda(D, open_ids)
    lam = lam_s.to_numpy()
    names = np.array([D.label(s_) for s_ in open_ids])
    J = len(open_ids)
    mean_plan = sum(d * p for d, p in DWELL_PLAN.items())
    print(f"{J} lockers, locker parcels/day {lam.sum():.0f}; per locker mean {lam.mean():.1f}, "
          f"max {lam.max():.1f}")

    # --- scenario grid
    m2 = pd.read_csv(RESULTS / "m2_module_plan.csv", index_col=0)
    configs = {
        "80 compartments each (plan 'medium')": np.full(J, 80),
        "LP/MILP module plan (+104 modules)": m2.reindex(open_ids)["104"].to_numpy() * MODULE,
    }
    sized = size_banks(lam, DWELL_PLAN)
    configs["sized for <=2% rejection"] = sized * MODULE
    rows, sims = [], {}
    for cname, K in configs.items():
        for dist in ("poisson", "negbin"):
            for surge in C.PEAK_MULTIPLIERS:
                s = simulate(lam, K.astype(np.int64), DWELL_PLAN, surge, dist)
                sims[(cname, dist, surge)] = s
                A = s["arrivals"].sum()
                rows.append(dict(config=cname, arrivals=dist, surge=surge,
                                 compartments=int(K.sum()),
                                 parcels_per_day=round(A / DAYS),
                                 unmet_pct=round(100 * s["rejected"].sum() / A, 2),
                                 mean_overflow_prob_pct=round(100 * np.average(
                                     s["overflow_prob"], weights=lam + 1e-9), 1),
                                 lockers_full_over_half_the_days=int((s["overflow_prob"] > .5)
                                                                     .sum()),
                                 mean_utilisation_pct=round(100 * np.average(
                                     s["utilisation"], weights=K), 1)))
    grid = pd.DataFrame(rows)
    grid.to_csv(RESULTS / "m3_scenarios.csv", index=False)
    print(grid.to_string(index=False))

    # --- per-locker results (base: sized banks, Poisson, x1) + Erlang B check
    base = sims[("80 compartments each (plan 'medium')", "poisson", 1.0)]
    per = pd.DataFrame({"site_id": open_ids, "name": names, "lambda_per_day": lam.round(1),
                        "offered_load_a": (lam * mean_plan).round(1),
                        "sim_unmet_pct_80": (100 * base["unmet_rate"]).round(1),
                        "erlangB_pct_80": [round(100 * erlang_b(80, l * mean_plan), 1)
                                           for l in lam],
                        "sim_overflow_prob_pct_80": (100 * base["overflow_prob"]).round(1),
                        "sim_utilisation_pct_80": (100 * base["utilisation"]).round(1),
                        "modules_needed_normal": sized,
                        "modules_needed_x1.5": size_banks(lam, DWELL_PLAN, surge=1.5),
                        "modules_needed_x2": size_banks(lam, DWELL_PLAN, surge=2.0),
                        "modules_needed_negbin": size_banks(lam, DWELL_PLAN, dist="negbin"),
                        "modules_needed_empirical_dwell": size_banks(lam, DWELL_EMPIRICAL)})
    per = per.sort_values("lambda_per_day", ascending=False)
    per.to_csv(RESULTS / "m3_per_locker.csv", index=False)
    print(per.head(12).to_string(index=False))
    erl_err = (per.sim_unmet_pct_80 - per.erlangB_pct_80).abs()

    # --- dwell-time sensitivity on total requirement
    req = dict(normal=int(per.modules_needed_normal.sum() * MODULE),
               surge_x1_5=int(per["modules_needed_x1.5"].sum() * MODULE),
               surge_x2=int(per.modules_needed_x2.sum() * MODULE),
               negbin=int(per.modules_needed_negbin.sum() * MODULE),
               empirical_dwell=int(per.modules_needed_empirical_dwell.sum() * MODULE))
    print("compartments required:", req)

    # --- Double 11: 7-day surge x2 inside a normal year, banks sized for normal days
    d11 = simulate(lam, sized * MODULE, DWELL_PLAN, 1.0, "negbin", days=60, warmup=30,
                   peak_days=(25, 7, 2.0), seed=SEED + 11)

    # --- parcel-rate / locker-share sensitivity (total requirement)
    sens = []
    for rate in C.PARCEL_RATE_RANGE + (C.PARCELS_PER_HOUSEHOLD_PER_DAY,):
        for share in (0.15, 0.30, 0.45):
            l2 = lam / LAMBDA_PER_HH * rate * share
            sens.append(dict(parcels_per_hh=rate, locker_share=share,
                             parcels_per_day=round(l2.sum()),
                             compartments_needed=int(sum(
                                 next(m for m in range(1, 200) if erlang_b(m * MODULE,
                                      x * mean_plan) <= SERVICE_TARGET) * MODULE
                                 for x in l2 if x > 0))))
    sens = pd.DataFrame(sens).sort_values(["parcels_per_hh", "locker_share"])
    sens.to_csv(RESULTS / "m3_demand_sensitivity.csv", index=False)
    print(sens.to_string(index=False))

    # --- Ch7 lecture-style demo for the busiest locker
    top = per.iloc[0]
    arr_tab, dw_tab, demo = ch7_demo_table(float(top.lambda_per_day), DWELL_PLAN)
    dump(dict(lockers=J, parcels_per_day=float(lam.sum()), mean_dwell=mean_plan,
              requirement=req, erlang_mean_abs_err_pct=float(erl_err.mean()),
              erlang_max_abs_err_pct=float(erl_err.max()),
              busiest=dict(name=top["name"], lam=float(top.lambda_per_day)),
              ch7_arrival_table=arr_tab, ch7_dwell_table=dw_tab, ch7_sim=demo,
              d11_daily=d11["daily"].to_dict("records"), nb_k=NB_K,
              service_target=SERVICE_TARGET), "m3_summary")

    # ================================================================ figures
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    g = grid[grid.arrivals == "poisson"]
    cn = list(configs)
    x = np.arange(len(C.PEAK_MULTIPLIERS))
    for k, c in enumerate(cn):
        v = g[g.config == c].unmet_pct.to_numpy()
        ax.bar(x + (k - 1) * .27, v, width=.25, color=SERIES[k], label=c)
        for xi, vi in zip(x, v):
            ax.text(xi + (k - 1) * .27, vi + 1, f"{vi:.0f}%", ha="center", fontsize=8,
                    color=INK2)
    ax.set_xticks(x, ["normal day", "+50% surge", "+100% surge (Double 11)"])
    ax.set_ylabel("Parcels rejected (unmet demand, %)")
    ax.set_title("Overflow under normal and peak demand (1,000 simulated days)")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -.12), ncol=3, fontsize=8)
    save(fig, "m3_unmet_by_scenario")

    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    ax.scatter(per.erlangB_pct_80, per.sim_unmet_pct_80, s=28, color=SERIES[0])
    mx = max(per.erlangB_pct_80.max(), per.sim_unmet_pct_80.max()) + 5
    ax.plot([0, mx], [0, mx], color=MUTED, lw=1, ls="--")
    ax.text(mx * .55, mx * .62, "simulation = Erlang B", fontsize=8, color=INK2, rotation=27)
    ax.set_xlabel("Erlang B blocking probability (analytic, %)")
    ax.set_ylabel("Simulated rejection rate (%)")
    ax.set_title("Monte Carlo vs queuing theory: 80-compartment banks")
    save(fig, "m3_erlang_check")

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    t = per.head(25)
    y = np.arange(len(t))[::-1]
    ax.barh(y, t.modules_needed_normal * MODULE, color=SERIES[0], height=.38,
            label="normal days")
    ax.barh(y - .4, t.modules_needed_x2 * MODULE, color=SERIES[1], height=.38,
            label="Double 11 (x2)")
    ax.set_yticks(y - .2, [f"{n[:40]}" for n in t.name], fontsize=7)
    ax.set_xlabel("Compartments needed for <= 2% rejection")
    ax.set_title("Where capacity is needed: 25 busiest locker sites")
    ax.legend(loc="lower right")
    save(fig, "m3_sizing_top")

    fig, ax = plt.subplots(figsize=(8.4, 3.8))
    dd = d11["daily"]
    ax.plot(dd.day, dd.arrivals, color=SERIES[0], label="parcels arriving")
    ax.plot(dd.day, dd.rejected, color=STATUS["critical"], label="rejected (bank full)")
    ax.axvspan(25, 32, color=GRID_C if (GRID_C := "#e4e3df") else None, alpha=.7, lw=0)
    ax.text(28.5, dd.arrivals.max() * 1.02, "Double 11 week (x2)", ha="center", fontsize=8,
            color=INK2)
    ax.set_xlabel("Day")
    ax.set_ylabel("Parcels per day (all lockers)")
    ax.set_title("A Double 11 surge on banks sized for normal days")
    ax.legend(loc="upper left")
    save(fig, "m3_double11")
    print("done")

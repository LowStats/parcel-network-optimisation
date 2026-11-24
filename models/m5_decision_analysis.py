"""Extension (Ch6) - decision making under uncertainty: how many lockers to build?

Alternatives  build P new locker banks (MCLP sites from m1), each with 160 compartments
States        locker adoption share s (share of e-commerce parcels collected from lockers):
              low 15% / base 30% / high 45%, probabilities 0.25 / 0.50 / 0.25 (assumption)
Payoff        annual net benefit (HK$) =
              365 x saving per locker parcel x (parcels served with P new banks - with none)
              - annual cost per bank x P
              parcels served use the Ch7 loss model: served_j = lambda_j (1 - ErlangB(K_j, a_j))
Criteria      maximax, maximin, Hurwicz (alpha = 0.7), equally likely (Laplace), minimax regret,
              EMV, EVwPI and EVPI.
Monetary inputs are ASSUMPTIONS anchored to published scenarios (see report) and are varied in a
break-even analysis.
Outputs: results/m5_*.csv|json, figures/models/m5_*.png
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from mcommon import INK, INK2, MUTED, RESULTS, SERIES, C, Data, dump, end_labels, save, style
from m3_simulation import assign_lambda, erlang_b

ALTS = [0, 10, 22, 35, 50]
STATES = {"low adoption (15%)": 0.15, "base (30%)": 0.30, "high adoption (45%)": 0.45}
PROBS = np.array([0.25, 0.50, 0.25])
SAVING_PER_PARCEL = 5.0        # HK$ per parcel collected from a locker instead of the door
COST_PER_BANK = 60_000.0       # HK$ per year for a 160-compartment bank (capex/5 + opex + rent)
NEW_BANK_K, EXISTING_K = 160, 80
HURWICZ_ALPHA = 0.7


def served_per_day(D: Data, open_ids, new_ids, share: float) -> float:
    lam = assign_lambda(D, open_ids) / C.LOCKER_SHARE * share
    mean_d = sum(d * p for d, p in {1: .6, 2: .3, 3: .1}.items())
    tot = 0.0
    for sid, l in lam.items():
        K = NEW_BANK_K if sid in new_ids else EXISTING_K
        tot += l * (1 - erlang_b(K, l * mean_d)) if l > 0 else 0.0
    return tot


def payoff_table(D: Data, chosen_by_P, saving=SAVING_PER_PARCEL, cost=COST_PER_BANK):
    ex = list(D.ex_lock.site_id) + list(D.ex_lock_res.site_id)
    served = {}
    for P in ALTS:
        new = chosen_by_P[str(P)] if P else []
        for st, s in STATES.items():
            served[(P, st)] = served_per_day(D, ex + new, set(new), s)
    tab = pd.DataFrame(index=[f"build {P}" for P in ALTS], columns=list(STATES), dtype=float)
    for P in ALTS:
        for st in STATES:
            tab.loc[f"build {P}", st] = 365 * saving * (served[(P, st)] - served[(0, st)]) \
                - cost * P
    return tab, served


def criteria(tab: pd.DataFrame):
    v = tab.to_numpy()
    regret = v.max(axis=0) - v
    res = pd.DataFrame({
        "maximax": v.max(axis=1), "maximin": v.min(axis=1),
        "hurwicz_0.7": HURWICZ_ALPHA * v.max(axis=1) + (1 - HURWICZ_ALPHA) * v.min(axis=1),
        "equally_likely": v.mean(axis=1), "max_regret": regret.max(axis=1),
        "EMV": v @ PROBS}, index=tab.index)
    best = {c: (res[c].idxmin() if c == "max_regret" else res[c].idxmax()) for c in res}
    evwpi = float(v.max(axis=0) @ PROBS)
    evpi = evwpi - float(res.EMV.max())
    return res, best, evwpi, evpi, pd.DataFrame(regret, index=tab.index, columns=tab.columns)


if __name__ == "__main__":
    plt = style()
    D = Data()
    m1 = json.loads((RESULTS / "m1_summary.json").read_text())
    cbp = m1["chosen_by_P"]
    tab, served = payoff_table(D, cbp)
    res, best, evwpi, evpi, regret = criteria(tab)
    print((tab / 1e6).round(2))
    print((res / 1e6).round(2))
    print(best, "EVwPI", round(evwpi / 1e6, 2), "EVPI", round(evpi / 1e6, 2))
    tab.to_csv(RESULTS / "m5_payoff_table.csv")
    res.to_csv(RESULTS / "m5_criteria.csv")
    regret.to_csv(RESULTS / "m5_regret.csv")

    # break-even: EMV-optimal alternative as the saving per parcel varies
    be = []
    for sv in np.arange(1, 12.5, 0.5):
        t, _ = payoff_table(D, cbp, saving=sv)
        e = t.to_numpy() @ PROBS
        be.append(dict(saving_per_parcel=sv, **{f"EMV build {P}": e[k] for k, P in
                                                enumerate(ALTS)},
                       best=t.index[int(np.argmax(e))]))
    be = pd.DataFrame(be)
    be.to_csv(RESULTS / "m5_breakeven.csv", index=False)
    print(be[["saving_per_parcel", "best"]].to_string(index=False))

    # sensitivity to the probability of high adoption (Ch6 EMV-vs-probability lines)
    ph = []
    for p in np.linspace(0, 1, 21):
        probs = np.array([(1 - p) / 3, 2 * (1 - p) / 3, p])   # keep low:base ratio 1:2
        e = tab.to_numpy() @ probs
        ph.append(dict(p_high=p, **{f"build {P}": e[k] for k, P in enumerate(ALTS)}))
    ph = pd.DataFrame(ph)
    ph.to_csv(RESULTS / "m5_prob_sensitivity.csv", index=False)

    dump(dict(alternatives=ALTS, states=STATES, probs=PROBS.tolist(),
              saving_per_parcel=SAVING_PER_PARCEL, cost_per_bank=COST_PER_BANK,
              new_bank_compartments=NEW_BANK_K, existing_compartments=EXISTING_K,
              hurwicz_alpha=HURWICZ_ALPHA, best=best, EVwPI=evwpi, EVPI=evpi,
              served_per_day={f"{k[0]}|{k[1]}": v for k, v in served.items()}), "m5_summary")

    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    for k, P in enumerate(ALTS):
        ax.plot(be.saving_per_parcel, be[f"EMV build {P}"] / 1e6, color=SERIES[k], lw=2)
    end_labels(ax, be.saving_per_parcel.iloc[-1] + .15,
               {f"build {P}": be[f"EMV build {P}"].iloc[-1] / 1e6 for P in ALTS})
    ax.axvline(SAVING_PER_PARCEL, color=MUTED, lw=1, ls="--")
    ax.text(SAVING_PER_PARCEL + .1, ax.get_ylim()[1] * .9, "base assumption", fontsize=8,
            color=INK2)
    ax.axhline(0, color=INK2, lw=.8)
    ax.set_xlim(1, 13.5)
    ax.set_xlabel("Saving per parcel collected from a locker (HK$)")
    ax.set_ylabel("Expected annual net benefit, EMV (HK$ m)")
    ax.set_title("Break-even: which expansion size maximises EMV")
    save(fig, "m5_breakeven")

    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    for k, P in enumerate(ALTS):
        ax.plot(ph.p_high, ph[f"build {P}"] / 1e6, color=SERIES[k], lw=2)
    end_labels(ax, 1.02, {f"build {P}": ph[f"build {P}"].iloc[-1] / 1e6 for P in ALTS})
    ax.axhline(0, color=INK2, lw=.8)
    ax.set_xlim(0, 1.15)
    ax.set_xlabel("Probability of high adoption (low:base kept at 1:2)")
    ax.set_ylabel("EMV (HK$ m per year)")
    ax.set_title("EMV of each alternative as adoption optimism changes")
    save(fig, "m5_prob_sensitivity")
    print("done")

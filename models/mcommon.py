"""Shared data loading, coverage sets, solvers and plot style for the modelling stage.

Solvers: SciPy's HiGHS interface - `milp` for (mixed-)integer programs (branch-and-bound),
`linprog` for linear programs (returns dual values = shadow prices, and reduced costs).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import csr_matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import common as C  # noqa: E402  (pipeline parameters: parcel rate, dwell, sizes ...)

PROC = ROOT / "data" / "processed"
RESULTS = ROOT / "results"
FIG = ROOT / "figures" / "models"
for d in (RESULTS, FIG):
    d.mkdir(parents=True, exist_ok=True)

# Base-case modelling parameters (all varied in the sensitivity analysis)
D_BASE = 300          # coverage radius, metres of walking (plan: 300 m ~ 4-5 min)
T_BASE = 4.0          # equivalent slope-aware walking-time radius, minutes
D_SWEEP = [200, 250, 300, 350, 400, 450, 500]
# the same standards expressed as flat-ground walking time (D / 75 m per min): 2.7 ... 6.7 min
T_SWEEP = [R / C.WALK_SPEED_M_PER_MIN for R in D_SWEEP]
ESTATE_M = 25         # a residents-only locker serves demand nodes within this distance
D_MAX_ALLOC = 500     # furthest a resident will walk to a locker in the allocation LP
LAMBDA_PER_HH = C.PARCELS_PER_HOUSEHOLD_PER_DAY * C.LOCKER_SHARE  # locker parcels / hh / day

TPU_AREA = {
    "111": "Kennedy Town & Mount Davis", "112": "Shek Tong Tsui / Sai Ying Pun W",
    "113": "Sai Ying Pun / Sheung Wan W", "114": "Sheung Wan", "115": "Sheung Wan waterfront",
    "116": "Western waterfront", "121": "Central core", "122": "Central / Soho",
    "124": "Admiralty / Garden Road", "141": "Mid-Levels West", "142": "Mid-Levels",
    "143": "Mid-Levels East", "181": "The Peak", "182": "The Peak",
}
AREA_GROUP = {"111": "Western", "112": "Western", "113": "Western", "114": "Sheung Wan",
              "115": "Sheung Wan", "116": "Western", "121": "Central", "122": "Central",
              "124": "Central", "141": "Mid-Levels", "142": "Mid-Levels", "143": "Mid-Levels",
              "181": "The Peak", "182": "The Peak"}


# --------------------------------------------------------------------------------- data
class Data:
    def __init__(self):
        self.demand = pd.read_csv(PROC / "demand_nodes.csv")
        self.demand["tpu"] = self.demand["ssg"].str[:3]
        self.demand["area"] = self.demand["tpu"].map(TPU_AREA)
        self.demand["group"] = self.demand["tpu"].map(AREA_GROUP)
        self.sites = pd.read_csv(PROC / "supply_sites.csv")
        self.L = pd.read_csv(PROC / "distance_long.csv")
        s = self.sites
        ex = s.site_class == "existing"
        self.ex_lock = s[ex & (s.include_baseline == "yes") & (s.category == "locker")]
        self.ex_all = s[ex & (s.include_baseline == "yes")]
        self.cand_pub = s[(s.site_class == "candidate") & (s.category != "residential_building")]
        self.cand_res = s[(s.site_class == "candidate") & (s.category == "residential_building")]
        # existing residents-only lockers of in-scope operators (e.g. SF Cayman Rise)
        self.ex_lock_res = s[ex & s.operator.isin(C.BASELINE_OPERATORS) & (s.category == "locker")
                             & (s.public_access == "no")]
        # residents-only sites serve only their own estate (demand node within ESTATE_M)
        self.resonly = set(self.cand_res.site_id) | set(self.ex_lock_res.site_id)
        self.h = self.demand.set_index("node_id")["households"]
        self.nodes = list(self.demand.node_id)
        self.H = float(self.h.sum())

    def coverage(self, site_ids, radius: float, metric: str = "walk_m") -> pd.DataFrame:
        """Long table of (node_id, site_id) pairs where the site covers the node.
        Residents-only sites (estate-lobby candidates, existing residents-only lockers) cover only
        their own estate: demand nodes within ESTATE_M straight-line metres."""
        site_ids = set(site_ids)
        p = self.L[self.L.site_id.isin(site_ids) & (self.L[metric] <= radius)]
        ro = p.site_id.isin(self.resonly)
        p = p[~ro | (p.euclid_m <= ESTATE_M)]
        return p[["node_id", "site_id"]]

    def area_of(self, site_id: str) -> str:
        near = self.L[self.L.site_id == site_id].sort_values("walk_m").head(1)
        if near.empty:
            return ""
        return self.demand.set_index("node_id").loc[near.node_id.iloc[0], "area"]

    def label(self, site_id: str) -> str:
        """Readable English label for a site (operator lists carry Chinese names only)."""
        import re
        r = self.sites.set_index("site_id").loc[site_id]
        name = str(r["name"])
        chains = ("7-Eleven", "Circle K", "Wellcome", "Yata", "Free Market", "ParknShop",
                  "Market Place", "U Select", "Vango", "OK", "Mannings", "Watsons", "fusion")
        if name.isascii():
            if r["category"] == "mtr_exit":
                return f"MTR exit · {name}"[:48]
            if any(name.startswith(c) for c in chains) or r["category"] == "bus_terminus":
                return f"{name} · {self.area_of(site_id)}"[:48]
            return name[:48]
        op = {"SF Express": "SF", "Cainiao": "Cainiao", "JD Logistics": "JD",
              "Hongkong Post (iPostal)": "iPostal"}.get(r["operator"], str(r["operator"]))
        kind = "locker" if r["category"] == "locker" else "counter"
        if "711" in name or "7-Eleven" in name:
            kind += " (7-Eleven)"
        elif "OK便利店" in name:
            kind += " (OK store)"
        addr = str(r["address"]) if isinstance(r["address"], str) and str(r["address"]).isascii() \
            else ""
        m = re.search(r"(?:No\.?\s*)?\d+[A-Z]?(?:-\d+[A-Z]?)?,?\s+[A-Z][A-Za-z' ]+?"
                      r"(?:Street|Road|Terrace|Praya|Path|Lane|Square|Drive|Rise|Strand|Avenue)\b",
                      addr)
        where = re.sub(r"(?<=[a-z])(?=(Street|Road|West)\b)", " ", m.group(0)) if m \
            else self.area_of(site_id)
        return f"{op} {kind} · {where}"[:48]

    def covered_nodes(self, site_ids, radius, metric="walk_m") -> set:
        return set(self.coverage(site_ids, radius, metric).node_id)

    def pct(self, nodes) -> float:
        return 100 * float(self.h.reindex(list(nodes)).sum()) / self.H


# ------------------------------------------------------------------------------ models
def _incidence(pairs, rows, cols):
    ri = {r: k for k, r in enumerate(rows)}
    ci = {c: k for k, c in enumerate(cols)}
    p = pairs[pairs.node_id.isin(ri) & pairs.site_id.isin(ci)]
    return csr_matrix((np.ones(len(p)), (p.node_id.map(ri), p.site_id.map(ci))),
                      shape=(len(rows), len(cols)))


def mclp(D: Data, candidates, P: int, radius=D_BASE, fixed_open=(), metric="walk_m",
         relax=False, weights=None, time_limit=120):
    """Maximal Covering Location Problem (Church & ReVelle).
        max  sum_i h_i y_i
        s.t. y_i <= sum_j a_ij x_j      (i not already covered by fixed_open sites)
             sum_j x_j <= P
             x_j in {0,1}, 0 <= y_i <= 1
    Existing sites in `fixed_open` are open at no cost (expansion model)."""
    h = D.h if weights is None else weights
    base = D.covered_nodes(fixed_open, radius, metric) if len(fixed_open) else set()
    cand = [c for c in candidates if c not in set(fixed_open)]
    pairs = D.coverage(cand, radius, metric)
    rows = [n for n in D.nodes if n not in base and n in set(pairs.node_id)]
    A = _incidence(pairs, rows, cand)
    nx_, ny = len(cand), len(rows)
    c = np.concatenate([np.zeros(nx_), -h.reindex(rows).to_numpy()])
    cons = [LinearConstraint(np.concatenate([np.ones(nx_), np.zeros(ny)])[None, :], -np.inf, P)]
    if ny:
        from scipy.sparse import hstack, identity
        cons.append(LinearConstraint(hstack([-A, identity(ny)]).tocsr(), -np.inf, 0))
    integ = np.concatenate([np.ones(nx_) * (0 if relax else 1), np.zeros(ny)])
    r = milp(c, constraints=cons, integrality=integ, bounds=Bounds(0, 1),
             options={"time_limit": time_limit, "mip_rel_gap": 1e-6})
    x = r.x[:nx_]
    chosen = [cand[k] for k in np.flatnonzero(x > 0.5)] if not relax else None
    covered = base | (D.covered_nodes(chosen, radius, metric) if chosen else set())
    return dict(status=r.status, obj=-r.fun + float(h.reindex(list(base)).sum()), x=x, cand=cand,
                chosen=chosen, covered=covered, pct=D.pct(covered) if chosen is not None
                else 100 * (-r.fun + float(h.reindex(list(base)).sum())) / D.H,
                nodes=getattr(r, "mip_node_count", None), gap=getattr(r, "mip_gap", None),
                n_frac=int(((x > 1e-6) & (x < 1 - 1e-6)).sum()))


def sclp(D: Data, candidates, radius=D_BASE, fixed_open=(), metric="walk_m", cost=None,
         relax=False, time_limit=300):
    """Set Covering Location Problem (Toregas et al.).
        min  sum_j c_j x_j
        s.t. sum_j a_ij x_j >= 1   for every demand node i not already covered
             x_j in {0,1}
    Returns the nodes that no candidate can cover (they make the model infeasible and are
    reported separately)."""
    base = D.covered_nodes(fixed_open, radius, metric) if len(fixed_open) else set()
    cand = [c for c in candidates if c not in set(fixed_open)]
    pairs = D.coverage(cand, radius, metric)
    need = [n for n in D.nodes if n not in base]
    coverable = set(pairs.node_id)
    uncoverable = [n for n in need if n not in coverable]
    rows = [n for n in need if n in coverable]
    A = _incidence(pairs, rows, cand)
    cvec = np.ones(len(cand)) if cost is None else np.array([cost[c] for c in cand], float)
    r = milp(cvec, constraints=[LinearConstraint(A, 1, np.inf)],
             integrality=np.zeros(len(cand)) if relax else np.ones(len(cand)),
             bounds=Bounds(0, 1), options={"time_limit": time_limit})
    x = r.x
    chosen = None if relax else [cand[k] for k in np.flatnonzero(x > 0.5)]
    return dict(status=r.status, obj=r.fun, x=x, cand=cand, chosen=chosen,
                uncoverable=uncoverable, nodes=getattr(r, "mip_node_count", None),
                n_frac=int(((x > 1e-6) & (x < 1 - 1e-6)).sum()))


# -------------------------------------------------------------------------------- style
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}


def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 10, "xtick.color": INK2, "ytick.color": INK2,
        "xtick.labelsize": 9, "ytick.labelsize": 9, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "legend.frameon": False, "legend.fontsize": 9,
        "font.family": ["Helvetica Neue", "Arial Unicode MS", "Heiti TC", "DejaVu Sans"],
        "lines.linewidth": 2, "axes.axisbelow": True,
        "font.size": 10, "text.color": INK,
    })
    return plt


def end_labels(ax, x, ys: dict, min_gap_frac=0.045, **kw):
    """Direct labels at the line ends, nudged apart so they never overlap."""
    lo, hi = ax.get_ylim()
    gap = (hi - lo) * min_gap_frac
    items = sorted(ys.items(), key=lambda kv: kv[1])
    pos = []
    for k, (lab, y) in enumerate(items):
        y2 = y if not pos else max(y, pos[-1] + gap)
        pos.append(y2)
    for (lab, _), y2 in zip(items, pos):
        ax.text(x, y2, lab, va="center", fontsize=8, color=INK2, **kw)


def save(fig, name):
    path = FIG / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return path


def dump(obj, name):
    (RESULTS / f"{name}.json").write_text(json.dumps(obj, indent=1, default=float))

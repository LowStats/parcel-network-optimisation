"""Build the self-contained HTML report (report/index.html) from results/ and figures/.

Every number in the text is read from the model outputs, so re-running the models and this script
keeps the report consistent.
"""
from __future__ import annotations

import base64
import html
import json
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
FIG = ROOT / "figures"
OUT = ROOT / "report" / "index.html"


# ------------------------------------------------------------------------------- helpers
def j(name):
    return json.loads((RES / f"{name}.json").read_text())


def csv(name, **kw):
    return pd.read_csv(RES / f"{name}.csv", **kw)


def img(path: str, alt: str, caption: str, num: str):
    p = FIG / path
    b64 = base64.b64encode(p.read_bytes()).decode()
    return (f'<figure><img src="data:image/png;base64,{b64}" alt="{html.escape(alt)}" '
            f'loading="lazy"><figcaption><b>Figure {num}.</b> {caption}</figcaption></figure>')


def table(df: pd.DataFrame, caption: str = "", num: str = "", index=False, cls=""):
    t = df.to_html(index=index, escape=True, border=0, classes=f"tbl {cls}".strip(),
                   na_rep="–")
    cap = f'<p class="tcap"><b>Table {num}.</b> {caption}</p>' if caption else ""
    return f'<div class="tblwrap">{cap}{t}</div>'


def f0(x):
    return f"{x:,.0f}"


def f1(x):
    return f"{x:,.1f}"


def pc(x, d=1):
    return f"{x:.{d}f}%"


def hk(x):
    return f"HK${x / 1e6:,.2f}m"


# ------------------------------------------------------------------------------- data
m1 = j("m1_summary")
m2 = j("m2_summary")
m3 = j("m3_summary")
m4 = j("m4_summary")
m5 = j("m5_summary")
base = csv("m1_baseline_coverage")
sclp = csv("m1_sclp")
lprel = csv("m1_lp_relaxation")
rad = csv("m1_radius_sensitivity")
tvd = csv("m1_time_vs_distance")
plan = csv("m1_recommended_plan")
grp = csv("m1_coverage_by_area", index_col=0)
curves = csv("m1_mclp_curves")
alloc = csv("m2_locker_allocation")
rng = csv("m2_rhs_ranging")
capsc = csv("m2_capacity_scenarios")
modal = csv("m2_module_allocation")
rc = csv("m2_reduced_costs")
scen = csv("m3_scenarios")
per = csv("m3_per_locker")
dsens = csv("m3_demand_sensitivity")
gp = csv("m4_goal_programming")
pay = csv("m5_payoff_table", index_col=0)
crit = csv("m5_criteria", index_col=0)
be = csv("m5_breakeven")
stats_sites = pd.read_csv(ROOT / "data" / "processed" / "supply_sites.csv")
stats_nodes = pd.read_csv(ROOT / "data" / "processed" / "demand_nodes.csv")
qa_coord = pd.read_csv(ROOT / "data" / "processed" / "qa_operator_coordinates.csv")
qa_net = pd.read_csv(ROOT / "data" / "processed" / "qa_network_gaps.csv")

H = stats_nodes.households.sum()
POP = stats_nodes.population.sum()
b300 = base[base.radius_m == 300].iloc[0]
P_star = m1["P_star"]
Pst_pct = m1["P_star_pct"]
cur = m1["current_layout"]
pt = m1["P_for_target"]
sc_pub = sclp.iloc[0]
sc_all = sclp.iloc[1]
sc_gf = sclp.iloc[2]
pub_max = curves[curves.variant == "public only"].pct.max()
unc = m1["sclp_public_uncoverable_by_group"]
tv22 = tvd[tvd.P == P_star].set_index("group")
tv0 = tvd[tvd.P == 0].set_index("group")
req = m3["requirement"]
gp_eq = gp[gp.priority_order.str.startswith("equity > district")].iloc[0]
gp_eb = gp[gp.priority_order.str.startswith("equity > budget")].iloc[0]
gp_ref = gp.iloc[0]
best = m5["best"]
emv = crit["EMV"]
mod104 = modal[modal.extra_modules == 104].iloc[0]
uni160 = capsc[capsc.compartments_per_locker == 160].iloc[0]
cat_counts = plan.category.value_counts()
n_shop = int(cat_counts.get("staffed_station", 0))
n_estate = int(cat_counts.get("residential_building", 0))
n_pub_poi = len(plan) - n_shop - n_estate
be_ranges = be.groupby("best").saving_per_parcel.agg(["min", "max"])
be_ranges = be_ranges.reindex([b for b in ["build 0", "build 10", "build 22", "build 35",
                                           "build 50"] if b in be_ranges.index])
top_hot = per.head(5)
d11 = pd.DataFrame(m3["d11_daily"])
d11_rej = d11[(d11.day >= 25) & (d11.day < 32)].rejected.sum() / d11[(d11.day >= 25)
                                                                      & (d11.day < 32)].arrivals.sum()
qa_same = qa_coord[qa_coord.als_same_place]
n_ex = int((stats_sites.site_class == "existing").sum())
n_base = int(((stats_sites.site_class == "existing") & (stats_sites.include_baseline == "yes")).sum())
n_cand = int((stats_sites.site_class == "candidate").sum())

# ------------------------------------------------------------------------------- tables
t_base = base.rename(columns={"radius_m": "Walk radius (m)",
                              "existing_public_lockers": "20 public lockers (%)",
                              "existing_lockers": "+ 10 residents-only lockers (%)",
                              "all_public_points": "All public pick-up points (%)"})
t_sclp = sclp.rename(columns={"model": "Model", "new_lockers": "New lockers",
                              "public": "Public sites", "estate": "Estate lockers",
                              "lp_relaxation": "LP relaxation", "fractional_vars":
                              "Fractional x in LP", "bnb_nodes": "B&B nodes",
                              "uncoverable_nodes": "Uncoverable nodes",
                              "uncoverable_households": "Uncoverable households"})
lprel["gap_households"] = lprel["gap_households"].abs().round(1)
t_lp = lprel.rename(columns={"P": "Budget P", "ilp_pct": "ILP coverage (%)",
                             "lp_pct": "LP relaxation (%)", "gap_households":
                             "Integrality gap (households)", "fractional_vars":
                             "Fractional x", "bnb_nodes": "B&B nodes"})
t_rad = rad[["radius_m", "baseline_pct", "sclp_new_lockers", "sclp_public", "sclp_estate",
             "public_only_uncoverable_hh", "mclp_P10_pct", "mclp_P20_pct",
             "mclp_P30_pct"]].rename(columns={
    "radius_m": "Radius (m)", "baseline_pct": "Today (%)", "sclp_new_lockers":
    "SCLP: lockers for 100%", "sclp_public": "of which public", "sclp_estate": "of which estate",
    "public_only_uncoverable_hh": "Households no public site can reach",
    "mclp_P10_pct": "MCLP P=10 (%)", "mclp_P20_pct": "MCLP P=20 (%)",
    "mclp_P30_pct": "MCLP P=30 (%)"})
t_tvd = tvd.rename(columns={"P": "New lockers", "group": "Area", "by_distance_300m":
                            "Covered by distance ≤ 300 m (%)", "by_time_4min":
                            "Covered by slope-aware time ≤ 4 min (%)"})
t_plan = plan[["name", "category", "area", "households_reached",
               "households_only_this_site"]].rename(columns={
    "name": "Site", "category": "Type", "area": "Area", "households_reached":
    "Households newly within 300 m", "households_only_this_site": "Households only this site reaches"})
t_plan["Type"] = t_plan["Type"].str.replace("_", " ")
t_grp = grp.reset_index().rename(columns={"group": "Area"})
t_alloc = alloc.head(10)[["name", "type", "demand_within_500m", "served_per_day",
                          "utilisation_pct", "shadow_price_m_per_parcel"]].rename(columns={
    "name": "Locker", "type": "Status", "demand_within_500m": "Parcels/day within 500 m",
    "served_per_day": "Served (parcels/day)", "utilisation_pct": "Utilisation (%)",
    "shadow_price_m_per_parcel": "Shadow price (m per parcel/day)"})
t_rng = rng.rename(columns={"site_id": "Site", "name": "Locker", "shadow_price": "Shadow price",
                            "capacity_now": "Capacity now", "range_lower": "RHS lower limit",
                            "range_upper": "RHS upper limit",
                            "extra_modules_at_this_price": "Extra 40-cell modules at this price"})
t_capsc = capsc.rename(columns={"compartments_per_locker": "Compartments per bank",
                                "network_capacity": "Network capacity (parcels/day)",
                                "served": "Served (parcels/day)",
                                "share_served_pct": "Share served (%)",
                                "avg_walk_m": "Average walk (m)"})
t_mod = modal[["extra_modules", "total_compartments", "served", "share_served_pct",
               "lockers_expanded", "max_modules", "bnb_nodes"]].rename(columns={
    "extra_modules": "Extra modules (budget)", "total_compartments": "Total compartments",
    "served": "Served (parcels/day)", "share_served_pct": "Share served (%)",
    "lockers_expanded": "Lockers expanded", "max_modules": "Largest bank (modules)",
    "bnb_nodes": "B&B nodes"})
t_scen = scen.rename(columns={"config": "Capacity configuration", "arrivals": "Arrivals",
                              "surge": "Demand multiplier", "compartments": "Compartments",
                              "parcels_per_day": "Parcels/day", "unmet_pct": "Rejected (%)",
                              "mean_overflow_prob_pct": "Days full (%, demand-weighted)",
                              "lockers_full_over_half_the_days": "Lockers full on >50% of days",
                              "mean_utilisation_pct": "Utilisation (%)"})
t_per = per.head(12)[["name", "lambda_per_day", "offered_load_a", "erlangB_pct_80",
                      "sim_unmet_pct_80", "modules_needed_normal", "modules_needed_x2",
                      "modules_needed_negbin"]].rename(columns={
    "name": "Locker", "lambda_per_day": "λ (parcels/day)", "offered_load_a": "Offered load a",
    "erlangB_pct_80": "Erlang B, K=80 (%)", "sim_unmet_pct_80": "Simulated, K=80 (%)",
    "modules_needed_normal": "Modules for ≤2% (normal)",
    "modules_needed_x2": "Modules for ≤2% (Double 11 ×2)",
    "modules_needed_negbin": "Modules for ≤2% (bursty NB)"})
t_dsens = dsens.rename(columns={"parcels_per_hh": "Parcels per household/day",
                                "locker_share": "Locker share", "parcels_per_day":
                                "Locker parcels/day", "compartments_needed":
                                "Compartments for ≤2% rejection"})
t_gp = gp.rename(columns={"priority_order": "Priority order", "new_lockers": "New lockers",
                          "estate_lockers": "of which estate", "district_pct": "District (%)"})
t_pay = (pay / 1e6).round(2).reset_index().rename(columns={"index": "Alternative"})
t_crit = crit.copy()
for c in t_crit.columns:
    t_crit[c] = (t_crit[c] / 1e6).round(2)
t_crit = t_crit.reset_index().rename(columns={"index": "Alternative", "maximax": "Maximax",
                                              "maximin": "Maximin", "hurwicz_0.7":
                                              "Hurwicz α=0.7", "equally_likely":
                                              "Equally likely", "max_regret": "Max regret",
                                              "EMV": "EMV"})
demo = m2["ch4_demo"]
import numpy as _np
_c, _x = _np.array(demo["cost"]), _np.array(demo["optimal"])
_red = _c + _np.array(demo["u_rows"])[:, None] + _np.array(demo["v_cols"])[None, :]
min_ii = float(_red[_x <= 1e-6].min())
t_demo_cost = pd.DataFrame(demo["cost"], index=demo["node_names"], columns=demo["site_names"])
t_demo_cost["Supply (parcels/day)"] = demo["supply"]
t_demo_cost.loc["Demand (capacity)"] = list(demo["demand"]) + [sum(demo["supply"])]
t_demo_cost = t_demo_cost.reset_index().rename(columns={"index": "From node \\ to locker"})


def alloc_tab(mat):
    t = pd.DataFrame(mat, index=demo["node_names"], columns=demo["site_names"]).round(0)
    return t.reset_index().rename(columns={"index": "Node"})


t_ch7a = pd.DataFrame(m3["ch7_arrival_table"]).rename(columns={
    "arrivals": "Daily arrivals", "representative": "Value used", "probability": "Probability",
    "cumulative": "Cumulative", "rn_interval": "Random-number interval"})
t_ch7d = pd.DataFrame(m3["ch7_dwell_table"]).rename(columns={
    "dwell_days": "Dwell (days)", "probability": "Probability", "cumulative": "Cumulative",
    "rn_interval": "Random-number interval"})
t_ch7s = pd.DataFrame(m3["ch7_sim"]).rename(columns={
    "day": "Day", "rn_arrivals": "RN (arrivals)", "arrivals": "Simulated arrivals",
    "rn_dwell": "RN (dwell)", "dwell_of_typical_parcel": "Dwell of a sampled parcel (days)"})
t_qanet = qa_net.issue.value_counts().rename_axis("Issue").reset_index(name="Count")

# ------------------------------------------------------------------------------- text
TODAY = date.today().strftime("%d %B %Y")
kpis = [
    (pc(b300.existing_public_lockers, 0), "of households live within a 300 m walk of one of "
     "today's 20 public lockers"),
    (f"{cur['optimal_same_n_pct']:.0f}%", "would, if the same 20 lockers were optimally "
     "placed"),
    (f"{P_star}", f"new lockers lift coverage to {Pst_pct:.0f}% (the elbow of the curve)"),
    (f"{m2['share_served'] * 100:.0f}%", "of locker-bound parcels fit in standard "
     "80-compartment banks"),
    (f0(req["normal"]), "compartments needed for ≤2% overflow on normal days"),
    (f"{int(gp_eq.new_lockers)}", "lockers to give every area ≥70% coverage (EMV-optimal "
     "size too)"),
]
kpi_html = "".join(f'<div class="kpi"><div class="kv">{v}</div><div class="kl">{l}</div></div>'
                   for v, l in kpis)

course_map = pd.DataFrame([
    ["Ch2 Linear programming, sensitivity, duality", "Transportation LP shadow prices, RHS "
     "ranging, reduced costs (§6)", "Where extra capacity is worth most"],
    ["Ch3.1–3.2 Integer / mixed-integer programming, branch & bound",
     "SCLP, MCLP (0-1 ILP), LP relaxation; module-allocation MILP (§4, §6)",
     "How many lockers, where, and how big"],
    ["Ch3.3 Goal programming", "Preemptive equity / coverage / budget goals (§8)",
     "Cost of serving the Mid-Levels and The Peak"],
    ["Ch4 Transportation problem (NW corner, VAM, dummy, MODI)",
     "Parcel allocation with a dummy destination; Kennedy Town worked example (§6)",
     "Capacity-feasible assignment of demand"],
    ["Ch6 Decision theory", "Payoff table, maximax / maximin / Hurwicz / Laplace / "
     "minimax regret, EMV, EVPI, break-even (§9)", "How big an investment under uncertainty"],
    ["Ch7 Queuing theory & Monte Carlo simulation", "Birth–death loss model (Erlang B), "
     "1,000-day Monte Carlo with random-number intervals (§7)", "Overflow, sizing, Double 11"],
], columns=["Course topic", "Where it is used", "Question it answers"])

# --------------------------------------------------------------------------------- HTML
CSS = r"""
:root{--surface:#fcfcfb;--surface2:#f4f3f0;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;
--line:#e4e3df;--accent:#2a78d6;--accent2:#eb6834;--good:#0ca30c;--crit:#d03b3b;
--callout:#eef5fd;--warn:#fff6e5;color-scheme:light}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){--surface:#1a1a19;
--surface2:#232322;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e88;--line:#383835;--accent:#3987e5;
--accent2:#d95926;--callout:#1d2a3a;--warn:#33291a;color-scheme:dark}}
:root[data-theme="dark"]{--surface:#1a1a19;--surface2:#232322;--ink:#f0efec;--ink2:#c3c2b7;
--muted:#8f8e88;--line:#383835;--accent:#3987e5;--accent2:#d95926;--callout:#1d2a3a;
--warn:#33291a;color-scheme:dark}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--surface);color:var(--ink);font:16px/1.6 -apple-system,
BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{display:grid;grid-template-columns:250px minmax(0,1fr);gap:40px;max-width:1240px;
margin:0 auto;padding:32px 24px 80px}
nav.toc{position:sticky;top:20px;align-self:start;font-size:14px;max-height:calc(100vh - 40px);
overflow:auto;border-right:1px solid var(--line);padding-right:16px}
nav.toc a{display:block;color:var(--ink2);text-decoration:none;padding:4px 0}
nav.toc a:hover{color:var(--accent)}nav.toc .sub{padding-left:12px;font-size:13px}
main{min-width:0}h1{font-size:34px;line-height:1.2;margin:0 0 8px}
h2{font-size:25px;margin:56px 0 12px;padding-top:12px;border-top:1px solid var(--line)}
h3{font-size:19px;margin:32px 0 8px}h4{font-size:16px;margin:20px 0 6px}
.sub-title{color:var(--ink2);font-size:18px;margin:0 0 6px}.meta{color:var(--muted);font-size:14px}
p,li{color:var(--ink)}a{color:var(--accent)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:24px 0}
.kpi{background:var(--surface2);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kv{font-size:30px;font-weight:700;line-height:1.1}.kl{color:var(--ink2);font-size:13.5px;margin-top:6px}
.callout{background:var(--callout);border-left:4px solid var(--accent);padding:12px 16px;
border-radius:6px;margin:16px 0}.callout.warn{background:var(--warn);border-left-color:var(--accent2)}
.callout p:first-child{margin-top:0}.callout p:last-child{margin-bottom:0}
figure{margin:20px 0}figure img{width:100%;height:auto;border:1px solid var(--line);
border-radius:8px;background:#fcfcfb}figcaption{color:var(--ink2);font-size:14px;margin-top:6px}
.tblwrap{overflow-x:auto;margin:16px 0}.tcap{color:var(--ink2);font-size:14px;margin:0 0 6px}
table.tbl{border-collapse:collapse;font-size:13.5px;min-width:100%;font-variant-numeric:tabular-nums}
table.tbl th{text-align:left;color:var(--ink2);font-weight:600;border-bottom:2px solid var(--line);
padding:7px 10px;vertical-align:bottom}
table.tbl td{border-bottom:1px solid var(--line);padding:6px 10px}
table.tbl tbody tr:nth-child(even){background:var(--surface2)}
details{margin:12px 0;border:1px solid var(--line);border-radius:8px;padding:8px 14px}
summary{cursor:pointer;color:var(--ink2);font-weight:600}
.math{overflow-x:auto}code{background:var(--surface2);padding:1px 5px;border-radius:4px;font-size:90%}
.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.rec{counter-reset:r}.rec li{margin:10px 0}
.toggle{position:fixed;right:16px;top:14px;background:var(--surface2);color:var(--ink);
border:1px solid var(--line);border-radius:20px;padding:5px 12px;font-size:13px;cursor:pointer}
@media (max-width:900px){.wrap{grid-template-columns:1fr;padding:20px 16px 60px}
nav.toc{position:static;border-right:0;border-bottom:1px solid var(--line);max-height:none;
padding:0 0 12px}.two{grid-template-columns:1fr}h1{font-size:27px}}
@media print{nav.toc,.toggle{display:none}.wrap{grid-template-columns:1fr}}
"""

TOC = [("summary", "Executive summary"), ("intro", "1. Problem and approach"),
       ("data", "2. Study area and data"), ("baseline", "3. Today's network"),
       ("ilp", "4. Location models (ILP)"), ("sens", "5. Sensitivity analysis"),
       ("capacity", "6. Capacity: transportation LP"), ("sim", "7. Stochastic simulation"),
       ("goal", "8. Goal programming"), ("decision", "9. Decision analysis"),
       ("insights", "10. Managerial insights"), ("limits", "11. Assumptions & limitations"),
       ("appendix", "Appendix")]
toc_html = "".join(f'<a href="#{a}">{t}</a>' for a, t in TOC)

S = []  # sections
S.append(f"""
<h1>Parcel Locker Network Optimisation</h1>
<p class="sub-title">Central &amp; Western District, Hong Kong: where to put parcel lockers,
how many, and how big</p>
<p class="meta">Integer & linear programming · transportation model · queuing & Monte Carlo
simulation · goal programming · decision analysis &nbsp;|&nbsp; Report generated {TODAY}</p>

<section id="summary"><h2 style="border-top:0;margin-top:24px">Executive summary</h2>
<p>Central &amp; Western is home to <b>{f0(H)} households ({f0(POP)} residents)</b>. We combined
official census, building and terrain data with every existing SF Express, Cainiao and
Hongkong Post (iPostal) pick-up point. We then used optimisation and simulation to answer three
questions: <i>where</i> should new parcel lockers go, <i>how many</i> are worth building, and
<i>how big</i> must each one be to avoid turning parcels away?</p>
<div class="kpis">{kpi_html}</div>
<div class="callout"><p><b>Key findings</b></p><ol>
<li><b>Today's lockers are badly placed for coverage.</b> The 20 public lockers reach
{pc(cur['pct'])} of households within a 300 m walk. An optimal layout of the same 20 lockers
would reach {pc(cur['optimal_same_n_pct'])} (greenfield MCLP).</li>
<li><b>{P_star} new lockers is the sweet spot.</b> Coverage rises from {pc(m1['baseline_pct'])} to
{pc(Pst_pct)}. After that, each extra locker adds less than 0.5% of district households.
{pt['80']} lockers reach 80% and {pt['90']} reach 90%.</li>
<li><b>Public sites alone cannot reach everyone.</b> {f0(sc_pub.uncoverable_households)} households,
mostly in the Mid-Levels and The Peak, have <i>no</i> shop, MTR exit or community facility within
300 m on foot. Public sites therefore cap coverage at {pc(pub_max)}. Full coverage needs
{int(sc_all.new_lockers)} new lockers, {int(sc_all.estate)} of them in-building (estate) lockers.</li>
<li><b>Coverage is not capacity.</b> At an assumed 30% locker share, about {f0(m2['locker_parcels_per_day'])}
locker parcels a day are generated. Standard 80-compartment banks can absorb only
{pc(m2['share_served'] * 100, 0)} of them. Monte Carlo simulation (checked against the Erlang loss
formula) shows the network needs about <b>{f0(req['normal'])} compartments</b> for ≤2% overflow on
normal days and <b>{f0(req['surge_x2'])}</b> for a Double 11 doubling.</li>
<li><b>Targeted expansion beats uniform expansion.</b> Placing 104 extra modules with a MILP
serves {pc(mod104.share_served_pct)} of locker parcels. Spreading the same 104 modules evenly
(160 compartments each) serves {pc(uni160.share_served_pct)}.</li>
<li><b>Equity has a price.</b> Coverage-only optimisation leaves The Peak at 0% and the Mid-Levels at
{pc(gp_ref['Mid-Levels %'])}. Giving every area at least 70% coverage takes
{int(gp_eq.new_lockers)} lockers ({int(gp_eq['of which estate'] if 'of which estate' in gp_eq else gp_eq.estate_lockers)} in-building).
Within a {P_star}-locker budget, equity costs {gp_ref.district_pct - gp_eb.district_pct:.1f}
points of district coverage.</li>
<li><b>Build about {best['EMV'].split()[-1]} lockers.</b> Under the base economic assumptions,
expected value (EMV {hk(emv.max())}/yr), Hurwicz, Laplace and minimax regret all choose
"{best['EMV']}". The pessimistic maximin rule picks "{best['maximin']}". The expected value of
perfect information is only {hk(m5['EVPI'])}/yr, so the decision is robust and expensive market
research is not justified.</li>
</ol></div>
</section>
""")

S.append(f"""
<section id="intro"><h2>1. Problem and approach</h2>
<p>Hong Kong's e-commerce volume keeps growing: 49.6% of residents aged 15+ shopped online in 2024,
up from 35.8% in 2018 (C&amp;SD Thematic Household Survey). Doorstep delivery in dense, hilly districts is
expensive and often fails. Self-collection lockers consolidate deliveries, but only if residents
can reach one on foot, and only if the locker has a free compartment when the courier arrives.
Our research plan therefore has four components:</p>
<ol><li><b>Spatial scoping and data</b>: a census-anchored, building-level picture of demand and every
existing and candidate pick-up location, with realistic walking distances on the pedestrian network.</li>
<li><b>Integer linear programming</b>: the Set Covering (SCLP) and Maximal Covering (MCLP) location
problems.</li>
<li><b>Stochastic simulation</b>: overflow and service levels when compartments are finite and demand is
random.</li>
<li><b>Sensitivity analysis and managerial insights</b>: walking radius, budget, demand surges and the
economics.</li></ol>
<p>We extend the plan with further methods from the course. Each is used where it answers a real
question:</p>
{table(course_map, "Course methods and where they are used in this report", "1")}
<p>All models are solved in Python with the open-source HiGHS solver (SciPy <code>milp</code> for
integer programs, which uses branch-and-bound; <code>linprog</code> for linear programs, which returns
dual values). The code, data and outputs are in the project repository and are fully
reproducible (see the Appendix).</p>
</section>
""")

S.append(f"""
<section id="data"><h2>2. Study area and data</h2>
<p><b>Study area.</b> We use the official 2021 Population Census District Council District boundary
for Central &amp; Western (12.4 km² on Hong Kong Island; the uninhabited Green Island and Little Green
Island are dropped). All {f0(H)} households and {f0(POP)} residents from the 160 census Small
Subunit Groups (SSGs) inside the district are included. Their totals equal the published district
totals exactly.</p>
<p><b>Demand nodes.</b> Demand is placed on <b>{len(stats_nodes)} residential building clusters</b>,
not on coarse planning units, so that a 300 m threshold is meaningful.</p>
<ul><li>Building footprints and storey counts come from the Lands Department.</li>
<li>Residential use comes from Buildings Department records.</li>
<li>Each SSG's census households are split across its buildings in proportion to floor area.</li>
<li>Student halls and elderly homes receive residents but no (domestic) households.</li></ul>
<p><b>Supply.</b> There are {n_ex} existing pick-up points in or within 500 m of the district.</p>
<ul><li>SF Express, Cainiao and JD points come from lists compiled from the operators' own sites,
with coordinates.</li>
<li>iPostal stations come from Hongkong Post's official dataset.</li>
<li>{n_base} of these points form the public baseline (SF, Cainiao, iPostal). JD is kept for reference
only, and residents-only lockers count for their own estate only.</li>
<li>There are also <b>{n_cand} candidate sites</b>: MTR exits, supermarkets, convenience stores, malls,
wet markets, community facilities and bus termini from OpenStreetMap, plus one in-building
(estate-lobby) locker per demand node.</li></ul>
<p><b>Walking distance.</b> Distances are shortest paths on the OpenStreetMap pedestrian network
(stairs, footbridges, lifts and the Central–Mid-Levels Escalator included), not straight lines.</p>
<ul><li>Walking time uses Tobler's hiking function on the Lands Department 5 m terrain model, so uphill
walks take longer.</li>
<li>Walking routes are on average 1.6× the straight-line distance. A Euclidean model would overstate
300 m coverage by about 180%.</li></ul>
<p><b>Quality checks.</b></p>
<ul><li>Operator coordinates were checked against an independent government geocode of their Chinese
addresses: median gap {qa_same.gap_m.median():.0f} m on {len(qa_same)} sites.</li>
<li>Possible gaps in the pedestrian network (dead-end lifts, disconnected paths near homes) are
listed for field checking.</li></ul>
{img('study_area_map.png', 'Map of the study area', 'Study area: demand nodes (blue, sized by households), existing pick-up points (stars = lockers, diamonds = staffed counters) and candidate sites (triangles).', '1')}
{table(t_qanet, "Pedestrian-network issues flagged for field checking (qa_network_gaps.csv)", "2")}
</section>
""")

S.append(f"""
<section id="baseline"><h2>3. Today's network</h2>
<p>Coverage is the share of households whose nearest locker is within the walking radius.
Residents-only lockers (e.g. SF's Cayman Rise) count only for their own estate. Staffed counters
(SF stations, Cainiao 驛站, convenience-store counters) have limited opening hours and are shown for
comparison only.</p>
{table(t_base, "Households within a given walk of today's lockers", "3")}
<p>At the plan's 300 m standard (about 4–5 minutes), only <b>{pc(b300.existing_public_lockers)}</b>
of households have a public locker nearby. Counting staffed counters too raises this to
{pc(b300.all_public_points)}. Coverage is very uneven: Sheung Wan is well served, while
the Mid-Levels ({pc(tv0.loc['Mid-Levels','by_distance_300m'])}) and The Peak (0%) are almost
unserved.</p>
</section>
""")

S.append(r"""
<section id="ilp"><h2>4. Location models (integer linear programming)</h2>
<h3>4.1 Formulations</h3>
<p>Let \(I\) be the demand nodes with households \(h_i\) and \(J\) the candidate locations. Let
\(d_{ij}\) be the walking distance and \(N_i=\{j\in J: d_{ij}\le D\}\) the sites within the radius
\(D\) of node \(i\), so \(a_{ij}=1\) if \(j\in N_i\). Decision variable \(x_j=1\) if a locker is opened at
\(j\). In the <i>expansion</i> models the existing lockers are fixed open at no cost. An in-building
(estate) locker serves only its own estate.</p>
<div class="two"><div><h4>Model A: Set Covering (SCLP)</h4><div class="math">
$$\min \sum_{j\in J} c_j x_j$$
$$\text{s.t.}\ \sum_{j\in N_i} x_j \ge 1 \quad \forall i\in I$$
$$x_j\in\{0,1\}$$</div>
<p>The minimum number of lockers (\(c_j=1\)) such that every household has one within \(D\).</p></div>
<div><h4>Model B: Maximal Covering (MCLP)</h4><div class="math">
$$\max \sum_{i\in I} h_i y_i$$
$$\text{s.t.}\ y_i \le \sum_{j\in N_i} x_j\ \ \forall i,\qquad \sum_{j\in J} x_j \le P$$
$$x_j\in\{0,1\},\ 0\le y_i\le 1$$</div>
<p>The most households covered with a budget of \(P\) new lockers.</p></div></div>
<p>Both are 0-1 integer programs, solved by branch-and-bound (Ch3). We also solve their <b>LP
relaxations</b> (\(0\le x_j\le 1\)) to measure the integrality gap.</p>
""")

S.append(f"""
<h3>4.2 Set covering: how many lockers for 100% coverage?</h3>
{table(t_sclp, "SCLP at D = 300 m", "4")}
<ul>
<li><b>Public sites alone cannot cover everyone.</b> {int(sc_pub.new_lockers)} new public lockers cover
every household that <i>any</i> public site can reach. {int(sc_pub.uncoverable_nodes)} nodes
({f0(sc_pub.uncoverable_households)} households) remain out of reach:
{', '.join(f"{k} {f0(v)}" for k, v in unc.items())}. These are hillside residential towers with no
shop, MTR exit or community facility within 300 m on foot.</li>
<li><b>Full coverage needs in-building lockers.</b> {int(sc_all.new_lockers)} new lockers
({int(sc_all.public)} public + {int(sc_all.estate)} estate) cover every household. Most of the
estate lockers each serve a single tower cluster, so the last 13% of households costs about three
times as many lockers as the first 87%.</li>
<li><b>The existing network helps, but only a little.</b> Building from scratch would need
{int(sc_gf.new_lockers)} lockers, only {int(sc_gf.new_lockers - sc_all.new_lockers)} more.</li>
</ul>

<h3>4.3 Maximal covering: what does each locker buy?</h3>
{img('models/m1_mclp_curve.png', 'MCLP coverage curve', f'Households within 300 m as the budget P grows. The black dot is today\'s 20 public lockers; the green curve shows what 20 optimally placed lockers would achieve.', '2')}
{img('models/m1_marginal_gain.png', 'Marginal gain per locker', 'Households added by each extra locker. The elbow is where the gain falls below 0.5% of district households.', '3')}
<ul>
<li><b>The first lockers do the most work.</b> The first 5 new lockers add
{pc(curves[(curves.variant=='public + estate')&(curves.P==5)].pct.iloc[0] - m1['baseline_pct'])}
coverage.</li>
<li><b>The curve flattens quickly.</b> By the elbow (<b>P* = {P_star}</b>,
{pc(Pst_pct)}), each extra locker adds less than 0.5% of district households.</li>
<li><b>Higher targets get expensive.</b> 80%, 90% and 95% coverage need {pt['80']}, {pt['90']} and
{pt['95']} new lockers respectively.</li>
<li><b>Public sites plateau.</b> With public sites only, the curve stops at {pc(pub_max)}.</li>
<li><b>Today's layout is inefficient.</b> Today's 20 lockers sit far below what 20 optimally placed
lockers reach ({pc(cur['optimal_same_n_pct'])}). Many existing SF lockers are clustered in
Sheung Wan and Sai Ying Pun, within a few hundred metres of each other.</li>
</ul>

<h3>4.4 LP relaxation and branch-and-bound</h3>
{table(t_lp, "MCLP: integer optimum vs LP relaxation", "5")}
<p>The LP relaxations of both models solved <b>integral</b> at every budget we tried, so
branch-and-bound finished at the root node with zero gap. This is common for covering problems
on real geography: the coverage matrix is nearly an <i>interval matrix</i> (lockers cover contiguous
stretches of streets), which makes the LP polytope nearly integral. Simply rounding an LP
solution is <i>not</i> safe in general (see the Harrison Electric example in Ch3). Here the LP bound
certifies that the integer solutions are optimal.</p>

<h3>4.5 Recommended expansion (P* = {P_star})</h3>
{img('models/m1_plan_map.png', 'Recommended plan map', f'The {P_star} new lockers (orange stars) chosen by the MCLP, existing lockers (black squares), and demand nodes by status.', '4')}
<p>The plan uses {n_pub_poi} public sites (supermarkets, convenience stores, MTR exits, community
facilities), {n_shop} existing staffed pick-up shops that would add a locker bank, and {n_estate}
in-building lockers in large estates. Sites are listed by the households <i>only</i> they reach,
which shows which sites are indispensable.</p>
{table(t_plan, f"Recommended {P_star} new lockers", "6")}
{img('models/m1_coverage_by_area.png', 'Coverage by area', 'Coverage by area before and after the expansion.', '5')}
</section>
""")

S.append(f"""
<section id="sens"><h2>5. Sensitivity analysis</h2>
<h3>5.1 Walking radius: convenience vs infrastructure</h3>
{img('models/m1_radius_tradeoff.png', 'Radius trade-off', 'New lockers needed for 100% coverage as the acceptable walk grows from 200 m to 500 m.', '6')}
{table(t_rad, "Radius sensitivity (expansion models)", "7")}
<ul>
<li><b>Each 50 m of acceptable walk saves many lockers.</b> Full coverage needs
{int(rad.iloc[0].sclp_new_lockers)} new lockers at 200 m but {int(rad.iloc[-1].sclp_new_lockers)}
at 500 m.</li>
<li><b>The steepest savings are between 200 m and 350 m.</b></li>
<li><b>The budget curves shift too.</b> With 20 new lockers, coverage ranges from
{pc(rad.iloc[0].mclp_P20_pct)} (200 m) to {pc(rad.iloc[-1].mclp_P20_pct)} (500 m).</li>
<li><b>Agree the standard first.</b> The service standard is a policy choice that matters as much as
the budget, and it should be fixed before sites are chosen.</li>
</ul>
{img('models/m1_radius_curves.png', 'MCLP curves by radius', 'MCLP coverage curves for each radius (darker = longer acceptable walk).', '7')}

<h3>5.2 Distance or time? The hill effect</h3>
{table(t_tvd, "Coverage measured by distance (300 m) vs slope-aware walking time (4 min, about 300 m on the flat)", "8")}
<ul>
<li><b>The district looks worse by time.</b> Measured by slope-aware walking time, today's coverage
falls from {pc(tv0.loc['District','by_distance_300m'])} to
{pc(tv0.loc['District','by_time_4min'])}.</li>
<li><b>The Mid-Levels look much worse.</b> After the expansion they reach
{pc(tv22.loc['Mid-Levels','by_time_4min'])} by time, against
{pc(tv22.loc['Mid-Levels','by_distance_300m'])} by distance.</li>
<li><b>The choice of metric changes the plan.</b> Only {m1['time_vs_distance_overlap']} of the
{P_star} sites chosen with distance are also chosen with time.</li>
<li><b>Hilly districts should be planned in time.</b> A distance-based plan systematically
over-serves the hillside on paper.</li>
</ul>

<h3>5.3 Demand, surge and economics</h3>
<p>Demand surges (+50%, +100%), parcel rates (0.20–0.50 per household per day) and locker shares
(15–45%) are tested in the simulation (§7.5). The cost and saving assumptions are tested in the
decision analysis (§9).</p>
</section>
""")

S.append(r"""
<section id="capacity"><h2>6. Capacity: a transportation LP with duals</h2>
<p>Covering models assume a locker never fills up. To test this, we send each node's daily
locker-bound parcels \(s_i = h_i\times 0.35\times 0.30\) through the recommended network as a
<b>transportation problem</b> (Ch4).</p>
<ul><li>Lockers are destinations with capacity \(c_j = K_j/\bar T\). With \(K_j=80\) compartments and a
mean dwell of \(\bar T=1.5\) days, that is 53 parcels per day.</li>
<li>Arcs exist only where the walk is at most 500 m.</li>
<li>Total supply far exceeds total capacity, so the problem is <i>unbalanced</i>. A <b>dummy
destination</b> (door delivery or a staffed counter) at a penalty of 1,000 m-equivalent absorbs the
excess.</li></ul>
<div class="math">$$\min \sum_{i,j} d_{ij} f_{ij} + M\sum_i f_{i0}\quad\text{s.t.}\quad
\sum_j f_{ij}+f_{i0}=s_i\ \ \forall i,\qquad \sum_i f_{ij}\le c_j\ \ \forall j,\qquad f\ge 0$$</div>
""")

S.append(f"""
<h3>6.1 Coverage is not capacity</h3>
<p>About {f0(m2['locker_parcels_per_day'])} locker-bound parcels a day are generated. {f0(m2['reachable_parcels'])}
of them come from homes within 500 m of a locker, but the {m2['lockers']} lockers can take only
{f0(m2['total_capacity'])} a day. The LP routes {f0(m2['served_by_lockers'])} parcels a day
({pc(m2['share_served'] * 100)}) to lockers, with an average walk of {m2['avg_walk_m']:.0f} m.
{m2['binding']} of {m2['lockers']} capacity constraints are binding.</p>
{img('models/m2_demand_vs_capacity.png', 'Demand vs capacity', 'Parcels per day within 500 m of each open locker, against the capacity of one 80-compartment bank.', '8')}

<h3>6.2 Shadow prices, RHS ranging and reduced costs (Ch2)</h3>
<ul>
<li><b>Shadow price.</b> The dual value of a capacity constraint is what one more parcel per day of
capacity is worth: here, the walking metres saved, which is about \\(M-d_{{ij}}\\). Every binding
locker has a high shadow price, so one extra compartment anywhere moves a parcel off door delivery.
The <b>range of feasibility</b> (RHS ranging, Ch2.4) shows how far capacity can grow before the
shadow price changes.</li>
<li><b>Reduced cost.</b> For unused routes, the reduced cost (\\(C_j-Z_j\\), the improvement index of
the stepping-stone method) is how much shorter a route would have to be before the LP uses it. The
smallest are about {rc.reduced_cost.min():.0f}–{rc.reduced_cost.head(3).max():.0f} m: near-ties
between neighbouring lockers.</li>
</ul>
{table(t_alloc, "Most valuable capacity: the ten lockers with the highest shadow prices", "9")}
{table(t_rng, "RHS ranging for the five highest shadow prices", "10")}

<h3>6.3 How much capacity, and where? A module-allocation MILP (Ch3.2)</h3>
<p>Making the number of 40-compartment modules \\(n_j\\) at each locker an integer variable (at least 2,
i.e. 80 compartments), with a total module budget, gives a <b>mixed-integer program</b>. Capacity
then becomes \\(\\sum_i f_{{ij}} \\le (40/\\bar T)\\,n_j\\).</p>
{table(t_mod, "Optimal placement of extra modules", "11")}
{table(t_capsc, "For comparison: uniform bank sizes everywhere", "12")}
<ul>
<li><b>Targeting beats spreading.</b> 104 extra modules placed by the MILP serve
{pc(mod104.share_served_pct)} of locker parcels. Spreading the same number evenly (160 compartments
per bank) serves {pc(uni160.share_served_pct)}.</li>
<li><b>Capacity should go where demand is.</b> The MILP concentrates modules at the
{int(mod104.lockers_expanded)} busiest sites, up to {int(mod104.max_modules)} modules
({int(mod104.max_modules) * 40} compartments) in one bank.</li>
</ul>
{img('models/m2_capacity_scenarios.png', 'Capacity scenarios', 'Share of locker-bound parcels served as every bank grows.', '9')}

<h3>6.4 Worked example: the course methods on a Kennedy Town sub-problem (Ch4)</h3>
<p>To connect with the hand methods in Ch4, we built a small balanced instance: the 6 largest Kennedy
Town nodes and 4 nearby lockers, with costs in walking metres and an even capacity split. We solved
it with the <b>Northwest-Corner rule</b>, <b>Vogel's Approximation Method</b> and the exact LP.</p>
{table(t_demo_cost, "Cost matrix (walking metres; 2000 = beyond 500 m), supply and demand", "13")}
<p>The total costs are Northwest Corner {f0(demo['cost_northwest'])},
<b>VAM {f0(demo['cost_vogel'])}</b> and optimum {f0(demo['cost_optimal'])}. As the lecture notes say,
VAM often finds the optimum outright, and here it does. NW ignores costs and is
{demo['cost_northwest'] / demo['cost_optimal']:.1f}× worse. The LP's dual values on the supply and
demand rows (sign reversed) are exactly the MODI multipliers:
\\(u_i\\) = ({', '.join(f"{abs(x) if x == 0 else -x:.0f}" for x in demo['u_rows'])}) and
\\(v_j\\) = ({', '.join(f"{-x:.0f}" for x in demo['v_cols'])}). Every used cell satisfies
\\(c_{{ij}} = u_i + v_j\\), and every unused cell has a non-negative improvement index
\\(c_{{ij}} - u_i - v_j\\) (the smallest is {min_ii:.0f} m). By the MODI test, the solution
is optimal.</p>
<details><summary>Show the NW, VAM and optimal allocations</summary>
{table(alloc_tab(demo['northwest']), 'Northwest-Corner allocation', '14a')}
{table(alloc_tab(demo['vogel']), "Vogel's Approximation allocation", '14b')}
{table(alloc_tab(demo['optimal']), 'Optimal allocation (LP)', '14c')}
</details>
</section>
""")

S.append(r"""
<section id="sim"><h2>7. Stochastic simulation of overflow (Ch7)</h2>
<h3>7.1 Model</h3>
<p>Each locker bank is a <b>loss system</b>. Parcels arrive, each occupies one compartment until
collected, and a parcel arriving when all \(K\) compartments are full is rejected and redirected.
The arrival rate \(\lambda_j\) of locker \(j\) is the locker-bound demand of every household within
300 m whose nearest open locker is \(j\).</p>
<p>We simulate 1,000 days, after a 30-day warm-up, following the five Monte Carlo steps of Ch7.</p>
<ol><li><b>Probability distributions.</b> Daily arrivals are Poisson(\(\lambda_j\)), or Negative
Binomial with the same mean and variance \(m+m^2/5\) to capture e-commerce bursts. Dwell time
is 1 day (60%), 2 days (30%) or 3 days (10%), as in the plan.</li>
<li><b>Cumulative distributions.</b></li>
<li><b>Random-number intervals</b> for each variable.</li>
<li><b>Random numbers</b> are generated by computer.</li>
<li><b>Trials.</b> Each morning collected parcels free compartments, new parcels fill free compartments,
and any excess is rejected.</li></ol>
<h3>7.2 Analytic check: the birth–death loss model</h3>
<p>The generalised Poisson queuing model (Ch7 supplement) with \(\lambda_n=\lambda\) for \(n&lt;K\),
\(\lambda_K=0\) and \(\mu_n=n\mu\) (each occupied compartment is "served" by its owner collecting) has the
steady state</p>
<div class="math">$$p_n=\frac{a^n/n!}{\sum_{k=0}^{K} a^k/k!},\qquad a=\lambda\,E[T],\qquad
P(\text{reject}) = p_K \ \ (\text{Erlang B}).$$</div>
<p>This blocking probability is <i>insensitive</i> to the shape of the dwell distribution: only its
mean matters. This gives an independent check of the simulation.</p>
""")

S.append(f"""
<h3>7.3 A lecture-style Monte Carlo table</h3>
<p>The busiest locker, <b>{m3['busiest']['name']}</b>, has \\(\\lambda\\) = {m3['busiest']['lam']:.0f}
parcels a day. Below are the cumulative distributions, the random-number intervals, and the
first ten simulated days.</p>
<div class="two"><div>{table(t_ch7a, 'Daily arrivals: Poisson, grouped', '15')}</div>
<div>{table(t_ch7d, 'Dwell time', '16')}</div></div>
{table(t_ch7s, 'First ten simulated days', '17')}

<h3>7.4 Results</h3>
{img('models/m3_unmet_by_scenario.png', 'Unmet demand by scenario', 'Share of parcels rejected because the bank is full, by capacity configuration and demand level (Poisson arrivals).', '10')}
{table(t_scen, "Simulation results, all scenarios (1,000 days each)", "18")}
<ul>
<li><b>Standard banks overflow badly.</b> With 80 compartments everywhere (the plan's "medium" locker),
{pc(scen.iloc[0].unmet_pct)} of parcels are turned away, and {int(scen.iloc[0].lockers_full_over_half_the_days)}
of {m3['lockers']} banks are full on most days.</li>
<li><b>Each bank can be sized precisely.</b> Sizing every bank with the Erlang formula (confirmed by
simulation) for ≤2% rejection needs <b>{f0(req['normal'])} compartments</b> in total. Bursty
(Negative Binomial) arrivals raise this to {f0(req['negbin'])}.</li>
<li><b>Monte Carlo agrees with the analytic model.</b> The simulation matches the Erlang formula
closely: mean absolute difference {m3['erlang_mean_abs_err_pct']:.1f} points, maximum
{m3['erlang_max_abs_err_pct']:.1f}.</li>
</ul>
{img('models/m3_erlang_check.png', 'Erlang check', 'Each point is a locker: simulated rejection vs the Erlang B blocking probability.', '11')}
<div class="callout"><p><b>Practical sizing rule.</b> Because the simulation agrees with the
analytic formula, operators can size a bank with an Erlang B table. Look up the daily parcels
\\(\\lambda\\) times the mean dwell, then pick the smallest \\(K\\) whose blocking is below the
service target. No simulation is needed site by site.</p></div>

<h3>7.5 Peak surge stress test (Double 11) and demand uncertainty</h3>
{img('models/m3_double11.png', 'Double 11', 'One week at double demand on banks sized for normal days (bursty arrivals).', '12')}
<ul>
<li><b>Normal-day sizing fails at peak.</b> On banks sized for normal days, a one-week doubling pushes
rejections to {pc(d11_rej * 100, 0)} of parcels during the surge.</li>
<li><b>Peak capacity needs nearly double.</b> Sizing for a +50% surge needs {f0(req['surge_x1_5'])}
compartments, and for +100% {f0(req['surge_x2'])}.</li>
<li><b>Peaks should be met with temporary capacity.</b> Permanent banks should be sized for normal
days, with temporary pop-up modules or extended staffed-counter hours at the hotspots for Double 11
and similar sales.</li>
</ul>
{img('models/m3_sizing_top.png', 'Sizing of busiest sites', 'Compartments needed at the 25 busiest locker sites, normal vs Double 11.', '13')}
{table(t_per, "Busiest lockers: load, overflow and modules needed", "19")}
{table(t_dsens, "Total compartments needed for ≤2% rejection under different demand assumptions", "20")}
</section>
""")

S.append(r"""
<section id="goal"><h2>8. Goal programming: efficiency vs equity (Ch3.3)</h2>
<p>The MCLP maximises total coverage, which favours the dense, flat west. A planner may also want a
minimum service level in every area. We add deviational variables and solve with preemptive
priorities (P1 first; its optimum is then fixed while P2 is minimised, and so on):</p>
<div class="math">$$\begin{aligned}
&G_1\ (\text{equity}):& \textstyle\sum_{i\in g} h_i y_i + d_g^- &\ge 0.70\,H_g\quad\forall\ \text{areas } g\\
&G_2\ (\text{district}):& \textstyle\sum_i h_i y_i + d_D^- &\ge 0.90\,H\\
&G_3\ (\text{budget}):& \textstyle\sum_j x_j - d_B^+ &\le P^*
\end{aligned}$$</div>
""")
S.append(f"""
{table(t_gp, "Goal programming under different priority orders (coverage within 300 m)", "21")}
<ul>
<li><b>The coverage-only plan ignores the hillside.</b> The MCLP plan ({P_star} lockers) leaves The
Peak at 0% and the Mid-Levels at {pc(gp_ref['Mid-Levels %'])}.</li>
<li><b>Equity first costs {int(gp_eq.new_lockers) - P_star} extra lockers.</b> Meeting the equity
goal first needs <b>{int(gp_eq.new_lockers)} lockers</b>, {int(gp_eq.estate_lockers)} of them
in-building lockers on the hillside, and also reaches the 90% district goal.</li>
<li><b>At a fixed budget, equity costs district coverage.</b> Holding to about {P_star} lockers while
putting equity first gives every area at least 70%, but district coverage falls from
{pc(gp_ref.district_pct)} to {pc(gp_eb.district_pct)}.</li>
<li><b>The trade-off is now explicit.</b> Management can choose a priority order knowing exactly what
it costs.</li>
</ul>
</section>
""")

S.append(f"""
<section id="decision"><h2>9. Decision analysis: how much to build (Ch6)</h2>
<p><b>Alternatives:</b> build 0, 10, {P_star}, 35 or 50 new 160-compartment banks (MCLP sites).
<b>States of nature:</b> locker adoption is low (15% of parcels), base (30%) or high (45%), with
probabilities 0.25 / 0.50 / 0.25.</p>
<p><b>Payoff:</b> annual net benefit = 365 × saving per locker parcel × extra parcels served −
annual cost per bank × banks.</p>
<ul><li>Parcels served account for capacity through the Erlang loss model.</li>
<li>Saving per parcel: HK${m5['saving_per_parcel']:.0f}. Lockers save roughly 20% of last-mile cost
(McKinsey 2024), applied to a door delivery cost of about HK$15–25.</li>
<li>Annual cost per bank: HK${f0(m5['cost_per_bank'])}. This is a published break-even scenario of
about €15k up front + €3.5k a year, annualised over 5 years, plus rent.</li>
<li>Both figures are <b>assumptions</b> and are varied below.</li></ul>
{table(t_pay, "Payoff table (HK$ m per year)", "22")}
{table(t_crit, "Decision criteria (HK$ m per year; regret = opportunity loss)", "23")}
<p><b>Results:</b></p>
<ul>
<li>Maximax → {best['maximax']}</li><li>Maximin → {best['maximin']}</li>
<li>Hurwicz (α = 0.7) → {best['hurwicz_0.7']}</li>
<li>Equally likely → {best['equally_likely']}</li>
<li>Minimax regret → {best['max_regret']}</li>
<li><b>EMV → {best['EMV']}</b> ({hk(emv.max())}/yr)</li>
</ul>
<p>With perfect information the expected value would be {hk(m5['EVwPI'])}, so <b>EVPI =
{hk(m5['EVPI'])}</b> a year. That is the most worth paying for a market study or pilot to learn
the true adoption rate, and it is small because the same decision is near-optimal in most states.</p>
{img('models/m5_breakeven.png', 'Break-even', 'EMV of each alternative as the saving per parcel varies.', '14')}
<p>The recommendation depends mainly on the value of a locker parcel:</p>
<ul>{''.join(f"<li>{b} is EMV-optimal for savings of " + (f"about HK${r['min']:.1f}" if r['min'] == r['max'] else f"HK${r['min']:.1f}–{r['max']:.1f}") + " per parcel</li>" for b, r in be_ranges.iterrows())}</ul>
<p>Below about HK$2 per parcel, no expansion pays.</p>
{img('models/m5_prob_sensitivity.png', 'Probability sensitivity', 'EMV of each alternative as the probability of high adoption changes (Ch6-style EMV lines).', '15')}
</section>
""")

top_sites = "; ".join(f"{r['name']} (λ ≈ {r['lambda_per_day']:.0f}/day)" for _, r in
                      top_hot.iterrows())
S.append(f"""
<section id="insights"><h2>10. Managerial insights and recommendations</h2>
<ol class="rec">
<li><b>Expand in phases, following the MCLP curve.</b>
<ul><li><i>Phase 1:</i> {pt['80']} new lockers take coverage to 80%. These are the steep part of the
curve, mostly at supermarkets, convenience stores, MTR exits and existing pick-up shops in Kennedy
Town, Shek Tong Tsui, Sai Ying Pun and Central.</li>
<li><i>Phase 2:</i> extend to the elbow at <b>{P_star}</b> ({pc(Pst_pct)}).</li>
<li><i>Phase 3:</i> if equity matters (or under the base economics, where EMV favours about 35), add
in-building lockers on the Mid-Levels and The Peak, up to about {int(gp_eq.new_lockers)}.</li></ul></li>
<li><b>Reuse existing pick-up shops.</b> {n_shop} of the {P_star} recommended sites are existing
staffed counters (SF stations, Cainiao 驛站, JD counters at 7-Eleven) that could add a 24-hour
locker bank. That means a partner and premises already in place, and no new site acquisition.</li>
<li><b>Fix the existing layout before adding more of the same.</b> An optimal 20-locker layout would
reach {pc(cur['optimal_same_n_pct'])} against today's {pc(cur['pct'])}. Several SF lockers in
Sheung Wan and Sai Ying Pun overlap. Operators should relocate redundant banks to uncovered streets
before buying new ones.</li>
<li><b>Size banks for demand, not by default.</b> A standard 80-compartment bank is far too small
where the plan places lockers, because the busiest sites see 300–480 parcels a day. Use the Erlang B
sizing rule. The network needs about {f0(req['normal'])} compartments for ≤2% overflow. Put modules
where the transportation LP's shadow prices and the module MILP point. The busiest sites are {top_sites}.</li>
<li><b>Plan for peaks with temporary capacity.</b> Double 11-type surges need about
{f0(req['surge_x2'] - req['normal'])} extra compartments for one or two weeks. Pop-up modules, longer
staffed-counter hours and nudging residents to collect within 24 hours are cheaper than permanent
over-sizing. Shorter dwell times (the empirical 84%-within-24-hours pattern) cut the requirement to
{f0(req['empirical_dwell'])} compartments.</li>
<li><b>Plan hilly districts in walking time, not distance.</b> On slopes, a 300 m distance standard
overstates service. Only {m1['time_vs_distance_overlap']} of {P_star} sites survive a switch to a
time standard, and the Mid-Levels look much worse.</li>
<li><b>The investment decision is robust.</b> Most criteria agree on {best['EMV']}, and EVPI is small.
The key number to pin down is the operator's real saving per locker parcel, because that drives
the break-even.</li>
</ol>
</section>
""")

S.append(f"""
<section id="limits"><h2>11. Assumptions and limitations</h2>
<ul>
<li><b>Demand.</b> 0.35 parcels per household per day is about 350 M parcels a year ÷ 2.78 M households
÷ 365. The volume is a secondary source, while the household count is official (THS Report 82).
The 30% locker share is an assumption (industry estimates 25–35%). Both are varied in Table 20 and
§9. Parcel demand is allocated by floor area within census areas, and propensity to shop online is
assumed equal across areas.</li>
<li><b>Capacity.</b> Existing lockers are assumed to have 80 compartments (sizes are not published).
Compartment size mix (S/M/L) is not modelled.</li>
<li><b>Behaviour.</b> Residents use the nearest locker within the radius, and coverage is binary at the
radius. A gravity or choice model with distance decay would be a natural extension.</li>
<li><b>Network.</b> OpenStreetMap may miss some lifts, stairs and passages, which would make walking
distances too long. Possible gaps are listed for field checks. The terrain model is ±5 m.</li>
<li><b>Economics.</b> Monetary values in §9 are illustrative, anchored to published scenarios, and
should be replaced with operator figures. Resident time savings and the environmental benefits of
consolidated delivery are not included, so the payoffs are conservative.</li>
<li><b>Data.</b> Pick-up point lists are third-party compilations of operator data (verified
2026-10-05). Cainiao's own figures suggest it has more lockers than are listed. Alfred24 was out of
scope.</li>
</ul>
</section>
""")

S.append(f"""
<section id="appendix"><h2>Appendix: reproducibility and data sources</h2>
<p>Everything is generated by code in the project repository:</p>
<ul>
<li><code>pipeline/01–06</code> build the data (download, demand nodes, supply sites, walking
distance and time matrix, maps, quality checks).</li>
<li><code>models/m1–m5</code> run the models (location ILPs, transportation LP and module MILP,
simulation, goal programming, decision analysis).</li>
<li><code>report/build_report.py</code> writes this page.</li>
<li><code>models/run_all.py</code> reruns everything.</li>
</ul>
<p>The solver is HiGHS via SciPy. The simulation uses a fixed random seed for reproducibility.</p>
<p><b>Main data sources:</b></p>
<ul>
<li>C&amp;SD 2021 Population Census (Small Subunit Groups; District Council Districts)</li>
<li>C&amp;SD Thematic Household Survey Report No. 82 (2024)</li>
<li>Lands Department building footprints and 5 m Digital Terrain Model</li>
<li>Buildings Department building records</li>
<li>Hongkong Post iPostal stations</li>
<li>SF / Cainiao / JD pick-up point lists</li>
<li>HKSAR Address Lookup Service</li>
<li>© OpenStreetMap contributors (pedestrian network, points of interest)</li>
</ul>
<p><b>Literature for parameters:</b> Urban Freight Lab locker dwell-time study (84% collected within
24 h); Australia Post locker dwell study (arXiv 2409.05061); McKinsey (2024) on out-of-home delivery
economics; sciforum parcel-locker break-even model. All assumptions are listed in
<code>pipeline/common.py</code> and <code>models/*.py</code>.</p>
<p>An interactive map of the data is in <code>figures/study_area_map.html</code>.</p>
</section>
""")

PAGE = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Parcel Locker Network Optimisation</title>
<style>{CSS}</style>
<script>window.MathJax={{tex:{{inlineMath:[['\\\\(','\\\\)']]}},svg:{{fontCache:'global'}}}};</script>
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-mml-chtml.min.js"></script>
</head><body>
<button class="toggle" onclick="var r=document.documentElement;r.dataset.theme=(r.dataset.theme==='dark'||(!r.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches))?'light':'dark'">Light / dark</button>
<div class="wrap"><nav class="toc"><b>Contents</b>{toc_html}</nav><main>
{''.join(S)}
</main></div></body></html>"""

OUT.parent.mkdir(exist_ok=True)
OUT.write_text(PAGE, encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")

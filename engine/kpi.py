"""
BLOCK 11 - PERFORMANCE COMPARISON (control-tower KPI layer)
==============================================================================
11.4 Dashboard view - the cross-module KPI set that sits on the front page of
     the control tower, rolled up from every block of the pipeline.

Block 11.1 / 11.2 / 11.3 (repeat scheduling for all rules, compare KPI results,
identify the best rule by criterion) live in scheduling.py, next to the
simulation that produces them.
"""

import numpy as np
import pandas as pd


def _short(item, limit=12):
    """Trim a long item code for a compact KPI label, keeping the distinctive end."""
    s = str(item)
    return s if len(s) <= limit else s.split("-", 1)[-1][:limit]


def build_kpi_summary(result):
    """Roll every module up into one flat KPI dictionary for the dashboard."""
    k = {}

    # ---- Block 3: forecasting --------------------------------------------
    sel = result.get("forecast_selection")
    if sel is not None and not sel.empty:
        k["forecast_avg_mape"] = round(sel["MAPE"].mean(), 2)
        k["forecast_best_mape"] = round(sel["MAPE"].min(), 2)
        k["forecast_worst_mape"] = round(sel["MAPE"].max(), 2)
        k["forecast_alerts"] = int(sel["AlertFlag"].sum())
        k["forecast_models"] = ", ".join(
            f"{_short(r['Item'])}:{r['SelectedMethod']}" for _, r in sel.iterrows())

    # ---- Block 4: inventory ----------------------------------------------
    params = result.get("inventory_params")
    if params is not None and not params.empty:
        k["inventory_value"] = round(params["InventoryValue"].sum(), 0)
        k["inventory_items"] = len(params)
        k["items_below_safety"] = int((params["OnHand"] < params["SafetyStock_Master"]).sum())
        pos = params["PeriodsOfSupply"].replace([np.inf, -np.inf], np.nan).dropna()
        k["avg_periods_of_supply"] = round(pos.mean(), 1) if len(pos) else 0.0

    # ---- Block 5: MPS ------------------------------------------------------
    mps = result.get("mps")
    if mps is not None:
        k["mps_feasible"] = bool(result.get("mps_feasible", False))
        k["mps_iterations"] = int(result.get("mps_iterations", 0))
        k["mps_total_units"] = int(mps["MPSQty"].sum())
        k["mps_periods_with_production"] = int((mps["MPSQty"] > 0).sum())
    cap = result.get("capacity_plan")
    if cap is not None and not cap.empty:
        k["rccp_peak_utilisation"] = round(cap["Utilisation"].max() * 100, 1)
        busiest = cap.groupby("WorkCentre")["Utilisation"].mean().idxmax()
        k["rccp_bottleneck"] = busiest

    # ---- Blocks 6 / 7: BOM and MRP ----------------------------------------
    mrp = result.get("mrp")
    if mrp is not None and not mrp.empty:
        k["mrp_items_planned"] = int(mrp["Item"].nunique())
        k["mrp_planned_orders"] = int((mrp["PlannedOrderReceipt"] > 0).sum())
    rel = result.get("releases")
    if rel is not None and not rel.empty:
        k["mrp_late_releases"] = int(rel["LateRelease"].sum())

    # ---- Block 8: order release -------------------------------------------
    summ = result.get("release_summary") or {}
    k["orders_released"] = summ.get("released", 0)
    k["orders_on_hold"] = summ.get("on_hold", 0)
    k["purchase_reqs"] = summ.get("purchase_reqs", 0)
    k["purchase_value"] = summ.get("purchase_value", 0.0)
    k["expedite_count"] = summ.get("expedite_count", 0)

    # ---- Blocks 10 / 11: scheduling ---------------------------------------
    comparison = result.get("rule_comparison")
    if comparison is not None and not comparison.empty:
        default = result.get("selected_rule", comparison.index[0])
        row = comparison.loc[default]
        k["sched_rule"] = default
        k["sched_jobs"] = int(row["Jobs"])
        k["sched_makespan"] = float(row["Makespan"])
        k["sched_makespan_periods"] = float(row["MakespanPeriods"])
        k["sched_otd"] = float(row["OnTimeDeliveryPct"])
        k["sched_avg_flow"] = float(row["AvgFlowTime"])
        k["sched_avg_tardiness"] = float(row["AvgTardiness"])
        k["sched_tardy_jobs"] = int(row["TardyJobs"])
        k["sched_avg_util"] = float(row["AvgUtilisation"])
        k["sched_bottleneck"] = row["Bottleneck"]
        k["sched_wip"] = float(row["AvgWIP"])

    # ---- Block 12: exceptions ---------------------------------------------
    exc = result.get("exceptions")
    if exc is not None and not exc.empty:
        k["exceptions_total"] = len(exc)
        counts = exc["Severity"].value_counts().to_dict()
        k["exceptions_critical"] = counts.get("Critical", 0)
        k["exceptions_high"] = counts.get("High", 0)
        k["exceptions_medium"] = counts.get("Medium", 0)
        k["exceptions_low"] = counts.get("Low", 0)
    else:
        k["exceptions_total"] = 0
        k["exceptions_critical"] = 0

    return k


def plan_health_score(kpis):
    """A single 0-100 headline score for the control tower.

    Deliberately simple and fully transparent - each component is stated so a
    viewer can see exactly why the plan scores what it does.
    """
    components = []

    # forecast accuracy: 100% at MAPE 0, 0% at MAPE 30
    mape = kpis.get("forecast_avg_mape")
    if mape is not None:
        components.append(("Forecast accuracy",
                           max(0.0, min(1.0, 1 - mape / 30.0)), 0.20))

    # MPS feasibility
    components.append(("MPS feasibility", 1.0 if kpis.get("mps_feasible") else 0.0, 0.20))

    # material readiness: share of candidate orders actually released
    rel, hold = kpis.get("orders_released", 0), kpis.get("orders_on_hold", 0)
    total = rel + hold
    components.append(("Material readiness", rel / total if total else 1.0, 0.20))

    # schedule performance: on-time delivery
    otd = kpis.get("sched_otd")
    if otd is not None:
        components.append(("On-time delivery", otd / 100.0, 0.25))

    # exception load: 0 exceptions = 1.0, 25+ critical/high = 0.0
    severe = kpis.get("exceptions_critical", 0) + kpis.get("exceptions_high", 0)
    components.append(("Exception load", max(0.0, 1 - severe / 25.0), 0.15))

    total_weight = sum(w for _, _, w in components)
    score = sum(v * w for _, v, w in components) / total_weight * 100

    breakdown = pd.DataFrame([
        {"Component": n, "Score": round(v * 100, 1), "Weight": f"{w/total_weight*100:.0f}%",
         "Contribution": round(v * w / total_weight * 100, 1)}
        for n, v, w in components
    ])
    return round(score, 1), breakdown


def kpi_definitions():
    """Formula reference shown on the dashboard - every KPI defined in words."""
    return pd.DataFrame([
        ("Makespan", "Time from the first operation start to the last operation finish",
         "max(completion) - min(start)", "Lower is better"),
        ("Flow time", "Time a job spends in the shop, from release to completion",
         "completion - arrival", "Lower is better"),
        ("Waiting time", "Time a job spends queueing rather than being worked on",
         "flow time - processing time", "Lower is better"),
        ("Lateness", "Signed difference between completion and due date",
         "completion - due", "Negative is early"),
        ("Tardiness", "Lateness, but early jobs count as zero",
         "max(0, completion - due)", "Lower is better"),
        ("On-time delivery %", "Share of jobs finished on or before the due date",
         "on-time jobs / total jobs x 100", "Higher is better"),
        ("Utilisation", "Share of available machine time actually spent processing",
         "busy hours / (makespan x machines)", "Higher is better, to a point"),
        ("Throughput", "Output rate of the shop",
         "units completed / elapsed periods", "Higher is better"),
        ("Average WIP", "Average number of jobs on the shop floor (Little's Law)",
         "sum(flow times) / makespan", "Lower is better"),
        ("Takt time", "Average time between successive units of output",
         "makespan / total units", "Match to demand rate"),
        ("MAPE", "Mean absolute percentage error of the forecast",
         "mean(|actual - forecast| / actual) x 100", "Lower is better"),
        ("Tracking signal", "Test for persistent forecast bias",
         "cumulative error / MAD", "Keep within +/- 4"),
        ("EOQ", "Order quantity that minimises ordering plus holding cost",
         "sqrt(2 x D x S / H)", "Reference quantity"),
        ("Reorder point", "Stock level that triggers a replenishment order",
         "avg demand x lead time + safety stock", "Reference level"),
        ("PAB", "Projected available balance in the master schedule",
         "PAB(t-1) + MPS(t) - max(forecast, orders)", "Keep above safety stock"),
        ("ATP", "Available to promise - what sales may still commit",
         "MPS receipt - orders before the next receipt", "Higher is better"),
    ], columns=["KPI", "Meaning", "Formula", "Direction"])

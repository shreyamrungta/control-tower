"""
BLOCK 13 - SCENARIO SIMULATION & REPORTING
==============================================================================
13.1 Run what-if / disruption scenarios   demand surge, supplier delay,
                                          machine breakdown, rush order
13.2 Analyse impact                       inventory, MPS, MRP, schedule, KPIs
13.3 Document results                     baseline vs scenario, charts, insights

This module also owns the OVERRIDE ENGINE used by Block 12.4: approving a
manager's recommended action and running a what-if scenario are the same
operation with different inputs, so they share one mechanism.

Supported overrides
-------------------
    lead_time         {item: periods}          supplier or production lead time
    safety_stock      {item: units}
    on_hand           {item: units}
    supplier          {item: supplier_code}
    lot_size          {item: [rule, value]}
    capacity          {wc: {field: value | "+1" | "x0.5"}}
    demand_multiplier {item: factor}           applied across the horizon
    demand_shock      {item: {periods: [..], multiplier: f}}
    rush_order        [{item, period, quantity}]
    supplier_delay    {supplier_code: extra_periods}
    forecast_method   {item: method_name}
    dispatch_rule     "EDD"

Modelling note on machine breakdown
-----------------------------------
A breakdown is applied period-by-period in the rough-cut capacity plan, where
capacity is held per period. The shop-floor simulation holds machines as a
fixed pool, so there the outage is applied as a sustained reduction in machine
count for the run. The capacity plan therefore shows the precise timing of the
outage and the simulation shows its severity - stated plainly rather than
papered over.
"""

import copy

import pandas as pd


# ===========================================================================
# THE OVERRIDE ENGINE
# ===========================================================================
def apply_data_overrides(data, overrides, horizon=12, apply_timed_derate=True):
    """Return a modified copy of the input data set. Never mutates the original.

    apply_timed_derate=False returns the capacity table WITHOUT the
    horizon-average derate for timed outages, which is what the rough-cut
    capacity plan needs - it applies those outages precisely, period by period.
    """
    if not overrides:
        return data

    d = {k: (v.copy() if isinstance(v, pd.DataFrame) else copy.deepcopy(v))
         for k, v in data.items()}
    inv = d["inventory"]

    for item, lt in (overrides.get("lead_time") or {}).items():
        inv.loc[inv["Item"] == item, "LeadTime"] = int(lt)
        if "supplier" in d:
            d["supplier"].loc[d["supplier"]["Item"] == item, "LeadTime"] = int(lt)

    for item, ss in (overrides.get("safety_stock") or {}).items():
        inv.loc[inv["Item"] == item, "SafetyStock"] = float(ss)

    for item, oh in (overrides.get("on_hand") or {}).items():
        inv.loc[inv["Item"] == item, "OnHand"] = float(oh)

    for item, sup in (overrides.get("supplier") or {}).items():
        inv.loc[inv["Item"] == item, "Supplier"] = sup

    for item, spec in (overrides.get("lot_size") or {}).items():
        rule, value = spec if isinstance(spec, (list, tuple)) else (spec, 0)
        inv.loc[inv["Item"] == item, "LotSizeRule"] = rule
        inv.loc[inv["Item"] == item, "LotSizeValue"] = float(value)

    # ---- supplier-wide delay ---------------------------------------------
    for sup, extra in (overrides.get("supplier_delay") or {}).items():
        affected = inv["Supplier"] == sup
        inv.loc[affected, "LeadTime"] = inv.loc[affected, "LeadTime"] + int(extra)
        if "supplier" in d:
            s = d["supplier"]
            s.loc[s["Supplier"] == sup, "LeadTime"] = s.loc[s["Supplier"] == sup, "LeadTime"] + int(extra)

    # ---- work centre capacity --------------------------------------------
    # A TIMED outage (periods + capacity_pct) is applied precisely, period by
    # period, in the rough-cut capacity plan (see capacity_by_period). The
    # shop-floor simulation holds machines as a fixed pool with no calendar, so
    # there the same outage is applied as the equivalent HORIZON-AVERAGE derate.
    # Doing it in exactly one place per model is what stops the outage being
    # counted twice.
    cap = d["capacity"]
    for wc, changes in (overrides.get("capacity") or {}).items():
        mask = cap["WorkCentre"] == wc
        if not mask.any():
            continue

        for field, value in changes.items():
            if field in ("periods", "capacity_pct"):
                continue
            current = cap.loc[mask, field].iloc[0]
            cap.loc[mask, field] = _apply_delta(current, value)

        # equivalent sustained derate for the simulation
        periods = changes.get("periods")
        pct = changes.get("capacity_pct")
        if apply_timed_derate and periods and pct is not None and horizon > 0:
            n_out = len([p for p in periods if 1 <= p <= horizon])
            derate = (horizon - n_out + n_out * float(pct)) / horizon
            cap.loc[mask, "Efficiency"] = cap.loc[mask, "Efficiency"] * derate

        # recompute derived availability
        row = cap.loc[mask].iloc[0]
        cap.loc[mask, "AvailableHoursPerPeriod"] = round(
            float(row["NumMachines"]) * float(row["HoursPerShift"]) *
            float(row["ShiftsPerDay"]) * float(row["DaysPerWeek"]) *
            float(row["Efficiency"]), 2)

    d["inventory"] = inv
    d["capacity"] = cap
    return d


def _apply_delta(current, value):
    """Support absolute values, '+1' / '-1' deltas and 'x0.5' multipliers."""
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("+") or v.startswith("-"):
            return type(current)(float(current) + float(v))
        if v.lower().startswith("x") or v.startswith("*"):
            return type(current)(float(current) * float(v[1:]))
    return value


def apply_forecast_overrides(forecast_df, orders_df, overrides):
    """Demand-side overrides applied AFTER forecasting (they are future events)."""
    if not overrides:
        return forecast_df, orders_df

    fc = forecast_df.copy()
    co = orders_df.copy()

    for item, factor in (overrides.get("demand_multiplier") or {}).items():
        mask = fc["Item"] == item
        fc.loc[mask, "Forecast"] = (fc.loc[mask, "Forecast"] * float(factor)).round().astype(int)

    for item, spec in (overrides.get("demand_shock") or {}).items():
        periods = spec.get("periods", [])
        mult = float(spec.get("multiplier", 1.0))
        mask = (fc["Item"] == item) & (fc["Period"].isin(periods))
        fc.loc[mask, "Forecast"] = (fc.loc[mask, "Forecast"] * mult).round().astype(int)

    rush = overrides.get("rush_order") or []
    if rush:
        new_rows = []
        start = len(co) + 1
        for i, r in enumerate(rush):
            new_rows.append({
                "OrderID": f"RUSH-{start + i:04d}",
                "Item": r["item"], "Period": int(r["period"]),
                "Quantity": int(r["quantity"]),
                "Customer": r.get("customer", "Rush Order"),
                "Priority": "High",
            })
        co = pd.concat([co, pd.DataFrame(new_rows)], ignore_index=True)

    return fc, co


def capacity_by_period(capacity_df, overrides, horizon):
    """Period-specific available hours, honouring timed machine breakdowns."""
    base = capacity_df.set_index("WorkCentre")["AvailableHoursPerPeriod"].to_dict()
    out = {(wc, p): float(h) for wc, h in base.items() for p in range(1, horizon + 1)}

    for wc, changes in ((overrides or {}).get("capacity") or {}).items():
        periods = changes.get("periods")
        pct = changes.get("capacity_pct")
        if periods and pct is not None:
            for p in periods:
                if (wc, p) in out:
                    out[(wc, p)] = out[(wc, p)] * float(pct)
    return out


# ===========================================================================
# 13.1  THE SCENARIO LIBRARY
# ===========================================================================
# `build_scenarios` constructs the library from whatever factory is loaded, so
# the disruptions are always about ITS busiest product, ITS least reliable
# supplier and ITS bottleneck.
# ---------------------------------------------------------------------------
def build_scenarios(data, result=None):
    """Generate a scenario library that fits the loaded dataset.

    Nothing here is hard-coded to a particular factory: the surge lands on the
    highest-volume product, the delay hits the least reliable supplier that is
    actually used, and the breakdown takes out the work centre with the least
    available capacity (or the measured bottleneck, when a plan is supplied).
    """
    inv = data["inventory"]
    bom = data["bom"]
    cap = data["capacity"]
    demand = data.get("demand")

    components = set(bom["ComponentItem"].astype(str)) if not bom.empty else set()
    finished = [i for i in inv["Item"].astype(str) if i not in components]
    if not finished:
        finished = inv["Item"].astype(str).tolist()

    # --- busiest and second product, by historical volume -------------------
    volume = {}
    if demand is not None and not demand.empty:
        volume = demand.groupby("Item")["Demand"].sum().to_dict()
    ranked = sorted(finished, key=lambda i: -volume.get(i, 0))
    top = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else top
    small = ranked[-1] if ranked else top

    # --- least reliable supplier that something is actually bought from -----
    worst_sup, worst_name, worst_rel = None, "", 1.0
    sup = data.get("supplier")
    if sup is not None and not sup.empty and "Reliability" in sup.columns:
        purchased = set(inv[inv["SourceType"] == "P"]["Item"].astype(str))
        used = sup[sup["Item"].astype(str).isin(purchased)] if purchased else sup
        if not used.empty:
            row = used.sort_values("Reliability").iloc[0]
            worst_sup = row["Supplier"]
            worst_name = row.get("SupplierName", worst_sup)
            worst_rel = float(row["Reliability"])

    # --- the constraint -----------------------------------------------------
    bottleneck, bn_desc = None, ""
    if result is not None and not result.get("capacity_plan", pd.DataFrame()).empty:
        bottleneck = (result["capacity_plan"].groupby("WorkCentre")["Utilisation"]
                      .mean().idxmax())
    elif not cap.empty:
        bottleneck = cap.sort_values("AvailableHoursPerPeriod").iloc[0]["WorkCentre"]
    if bottleneck is not None and "Description" in cap.columns:
        match = cap[cap["WorkCentre"] == bottleneck]
        if not match.empty:
            bn_desc = str(match.iloc[0]["Description"])

    horizon_mid = [3, 4, 5, 6]
    rush_qty = int(max(50, round((volume.get(small, 1200) / max(len(demand["Period"].unique()), 1)
                                  if demand is not None and not demand.empty else 200) * 2.5)))

    lib = {
        "Baseline": {
            "description": "The approved plan with no disruption applied.",
            "category": "Reference", "overrides": {},
        },
    }

    if top:
        lib[f"Demand Surge - {top}"] = {
            "description": (f"Demand for {top}, the highest-volume product, rises 40% "
                            f"in periods 3 to 6. Tests whether the master schedule, the "
                            f"material plan and the shop floor can absorb a mid-horizon "
                            f"spike."),
            "category": "Demand",
            "overrides": {"demand_shock": {top: {"periods": horizon_mid,
                                                 "multiplier": 1.40}}},
        }
    if second:
        lib[f"Demand Collapse - {second}"] = {
            "description": (f"Demand for {second} falls 35% across the horizon. Tests "
                            f"how much inventory and capacity is left stranded."),
            "category": "Demand",
            "overrides": {"demand_multiplier": {second: 0.65}},
        }
    if worst_sup:
        lib[f"Supplier Delay - {worst_sup}"] = {
            "description": (f"{worst_name} ({worst_sup}), the least reliable supplier at "
                            f"{worst_rel*100:.0f}% on-time, slips by 3 periods. Every "
                            f"part sourced from them arrives late."),
            "category": "Supply",
            "overrides": {"supplier_delay": {worst_sup: 3}},
        }
    if bottleneck:
        label = f"{bottleneck}{' (' + bn_desc + ')' if bn_desc else ''}"
        lib[f"Machine Breakdown - {bottleneck}"] = {
            "description": (f"{label} — the tightest work centre — runs at 50% for "
                            f"periods 4 and 5. Work that has to pass through it backs up "
                            f"behind the outage."),
            "category": "Capacity",
            "overrides": {"capacity": {bottleneck: {"periods": [4, 5],
                                                    "capacity_pct": 0.5}}},
        }
        lib[f"Capacity Investment - {bottleneck}"] = {
            "description": (f"An additional machine is approved at {label}. Quantifies "
                            f"what the constraint is actually costing in throughput and "
                            f"on-time delivery."),
            "category": "Improvement",
            "overrides": {"capacity": {bottleneck: {"NumMachines": "+1"}}},
        }
    if small:
        lib[f"Rush Order - {small}"] = {
            "description": (f"An unplanned order for {rush_qty:,} units of {small} lands "
                            f"in period 2, on top of the existing order book."),
            "category": "Demand",
            "overrides": {"rush_order": [{"item": small, "period": 2,
                                          "quantity": rush_qty,
                                          "customer": "Unplanned Contract"}]},
        }
    if top and worst_sup and bottleneck:
        lib["Combined Disruption - Worst Case"] = {
            "description": (f"Everything at once: a 40% surge on {top}, {worst_sup} "
                            f"slipping three periods, and {bottleneck} degraded. The "
                            f"stress test for the whole control tower."),
            "category": "Stress Test",
            "overrides": {
                "demand_shock": {top: {"periods": horizon_mid, "multiplier": 1.40}},
                "supplier_delay": {worst_sup: 3},
                "capacity": {bottleneck: {"periods": [4, 5], "capacity_pct": 0.5}},
            },
        }
    return lib


# No static scenario library exists any more: a hard-coded list would name one
# factory's products and work centres, and would be meaningless - or misleading -
# for any other dataset. `build_scenarios(data)` is the only way to get one.


def scenario_catalogue(library):
    lib = library
    return pd.DataFrame([
        {"Scenario": name, "Category": s["category"], "Description": s["description"]}
        for name, s in lib.items()
    ])


# ===========================================================================
# 13.2 / 13.3  IMPACT ANALYSIS
# ===========================================================================
COMPARE_KPIS = [
    ("mps_feasible",        "MPS feasible",             "bool"),
    ("mps_iterations",      "MPS revision iterations",  "lower"),
    ("mps_total_units",     "Total MPS units",          "info"),
    ("rccp_peak_utilisation", "Peak capacity utilisation %", "lower"),
    ("inventory_value",     "Inventory value",          "lower"),
    ("orders_released",     "Orders released",          "higher"),
    ("orders_on_hold",      "Orders on hold",           "lower"),
    ("purchase_value",      "Purchase order value",     "lower"),
    ("mrp_late_releases",   "Past-due order releases",  "lower"),
    ("sched_makespan",      "Makespan (hours)",         "lower"),
    ("sched_avg_flow",      "Average flow time (hours)", "lower"),
    ("sched_otd",           "On-time delivery %",       "higher"),
    ("sched_tardy_jobs",    "Tardy jobs",               "lower"),
    ("sched_avg_util",      "Average utilisation %",    "higher"),
    ("sched_wip",           "Average WIP",              "lower"),
    ("exceptions_total",    "Total exceptions",         "lower"),
    ("exceptions_critical", "Critical exceptions",      "lower"),
]


def compare_to_baseline(baseline_kpis, scenario_kpis, scenario_name=""):
    """13.2 - a signed, interpreted difference table."""
    rows = []
    for key, label, direction in COMPARE_KPIS:
        base = baseline_kpis.get(key)
        scen = scenario_kpis.get(key)
        if base is None or scen is None:
            continue

        if direction == "bool":
            verdict = ("No change" if base == scen
                       else ("Improved" if scen else "Worse"))
            rows.append({"KPI": label, "Baseline": base, "Scenario": scen,
                         "Change": "-", "ChangePct": None, "Impact": verdict})
            continue

        delta = float(scen) - float(base)
        pct = (delta / float(base) * 100) if base else None

        if direction == "info" or abs(delta) < 1e-9:
            impact = "No change" if abs(delta) < 1e-9 else "Info"
        elif direction == "lower":
            impact = "Improved" if delta < 0 else "Worse"
        else:
            impact = "Improved" if delta > 0 else "Worse"

        rows.append({
            "KPI": label,
            "Baseline": round(float(base), 2),
            "Scenario": round(float(scen), 2),
            "Change": round(delta, 2),
            "ChangePct": round(pct, 1) if pct is not None else None,
            "Impact": impact,
        })

    df = pd.DataFrame(rows)
    if scenario_name:
        # the value column is already called "Scenario", so the label column
        # that identifies WHICH scenario has to be named distinctly
        df.insert(0, "ScenarioName", scenario_name)
    return df


def summarise_impact(comparison_df, scenario_name):
    """13.3 - a short written verdict a manager can read in five seconds."""
    if comparison_df.empty:
        return "No comparable KPIs."

    worse = comparison_df[comparison_df["Impact"] == "Worse"]
    better = comparison_df[comparison_df["Impact"] == "Improved"]

    parts = [f"**{scenario_name}**: "]
    if worse.empty and better.empty:
        parts.append("no measurable impact on the plan.")
        return "".join(parts)

    if not worse.empty:
        top = worse.reindex(worse["ChangePct"].abs().sort_values(ascending=False).index).head(3)
        items = ", ".join(
            f"{r['KPI']} {'+' if r['Change'] > 0 else ''}{r['Change']:g}"
            + (f" ({r['ChangePct']:+.0f}%)" if pd.notna(r["ChangePct"]) else "")
            for _, r in top.iterrows())
        parts.append(f"{len(worse)} KPI(s) deteriorate - worst: {items}. ")
    if not better.empty:
        top = better.reindex(better["ChangePct"].abs().sort_values(ascending=False).index).head(2)
        items = ", ".join(
            f"{r['KPI']} {'+' if r['Change'] > 0 else ''}{r['Change']:g}"
            for _, r in top.iterrows())
        parts.append(f"{len(better)} KPI(s) improve - best: {items}.")
    return "".join(parts)

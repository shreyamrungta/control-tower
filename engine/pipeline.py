"""
END-TO-END PIPELINE
==============================================================================
Runs every block of the flow chart in order and returns one result object that
the dashboard, the agents and the scenario engine all read from.

    Block 2   import & validate the nine input files
    Block 3   demand forecasting
    Block 4   inventory planning
    Block 5   master production scheduling (with the 5.5 feasibility loop)
    Block 6   BOM explosion / gross requirements
    Block 7   material requirements planning
    Block 8   production order release
    Block 9   routing & capacity preparation
    Block 10  shop-floor scheduling simulation
    Block 11  performance comparison across dispatching rules
    Block 12  exception detection
    Block 13  (driven separately by scenarios.py, which calls back into here)

The whole pipeline is a pure function of (data, overrides), which is what makes
what-if analysis and approved manager decisions trivially re-runnable.
"""

import time

import pandas as pd

from . import io_utils
from .bom import build_bom_tree
from .config import HORIZON_PERIODS, DISPATCH_RULES
from .exceptions import (consolidate, detect_forecast_exceptions,
                         detect_supplier_exceptions, detect_capacity_exceptions)
from .forecasting import run_forecasting
from .inventory import run_inventory_planning
from .kpi import build_kpi_summary, plan_health_score
from .mps import run_mps
from .mrp import run_mrp, mrp_output_table
from .order_release import run_order_release, build_job_list
from .scenarios import (apply_data_overrides, apply_forecast_overrides,
                        capacity_by_period)
from .scheduling import (run_all_rules, compare_rules, best_rule_by_criterion,
                         scheduling_exceptions)


def run_pipeline(data=None, overrides=None, horizon=HORIZON_PERIODS,
                 schedule_window=(1, 8), rules=None, selected_rule=None,
                 verbose=False):
    """Run all thirteen blocks. Returns a dict of every intermediate result."""
    t0 = time.time()
    overrides = overrides or {}
    rules = rules or DISPATCH_RULES
    log = []

    def step(msg):
        log.append(msg)
        if verbose:
            print(f"  {msg}")

    # ---- Block 2: import + overrides --------------------------------------
    if data is None:
        data = io_utils.load_all()
    base_data = data
    data = apply_data_overrides(data, overrides, horizon)
    step(f"Block 2  imported {len(data['inventory'])} items, "
         f"{len(data['bom'])} BOM links, {len(data['capacity'])} work centres")

    # ---- Block 3: forecasting ---------------------------------------------
    forecast, accuracy, selection, fit = run_forecasting(
        data["demand"], horizon, forced_methods=overrides.get("forecast_method"))
    forecast, orders = apply_forecast_overrides(forecast, data["orders"], overrides)
    step(f"Block 3  forecast {forecast['Item'].nunique()} products, "
         f"avg MAPE {selection['MAPE'].mean():.1f}%")

    # ---- Block 6 (structure first - MRP and inventory both need it) -------
    bom_tree = build_bom_tree(data)

    # ---- Block 4: inventory planning --------------------------------------
    inv_params, inv_projection, inv_exceptions = run_inventory_planning(
        data, bom_tree, forecast, selection, horizon)
    step(f"Block 4  inventory value {inv_params['InventoryValue'].sum():,.0f}, "
         f"{len(inv_exceptions)} exception(s)")

    # ---- Block 5: MPS with the feasibility loop ---------------------------
    # rough-cut capacity applies a timed outage precisely, so it starts from the
    # capacity table WITHOUT the sustained derate the simulation uses
    rccp_capacity = apply_data_overrides(
        base_data, overrides, horizon, apply_timed_derate=False)["capacity"]
    cap_periods = capacity_by_period(rccp_capacity, overrides, horizon)
    mps_result = run_mps(data, bom_tree, forecast, inv_params, horizon,
                         capacity_periods=cap_periods, orders_df=orders)
    step(f"Block 5  MPS {'feasible' if mps_result['feasible'] else 'INFEASIBLE'} "
         f"after {mps_result['iterations']} iteration(s)")

    # ---- Block 6: gross requirements --------------------------------------
    sched = mps_result["mps"][mps_result["mps"]["MPSQty"] > 0][
        ["Item", "Period", "MPSQty"]].rename(columns={"MPSQty": "Qty"})
    gross_requirements = bom_tree.explode_gross_requirements(sched)
    step(f"Block 6  exploded MPS into {len(gross_requirements)} gross requirement rows "
         f"across {bom_tree.max_level() + 1} BOM levels")

    # ---- Block 7: MRP ------------------------------------------------------
    mrp_result = run_mrp(data, bom_tree, mps_result["mps"], inv_params,
                         horizon=horizon)
    step(f"Block 7  planned {len(mrp_result['releases'])} order releases, "
         f"{int(mrp_result['releases']['LateRelease'].sum()) if not mrp_result['releases'].empty else 0} past due")

    # ---- Block 8: order release -------------------------------------------
    prod_orders, purchase_reqs, release_summary = run_order_release(
        data, bom_tree, mrp_result, horizon)
    step(f"Block 8  released {release_summary.get('released', 0)}, "
         f"held {release_summary.get('on_hold', 0)}, "
         f"{release_summary.get('purchase_reqs', 0)} purchase requisitions")

    # ---- Block 9: executable job list -------------------------------------
    jobs, ops = build_job_list(prod_orders, data["routing"], data["capacity"],
                               schedule_window)
    step(f"Block 9  job list: {len(jobs)} jobs / {len(ops)} operations "
         f"in periods {schedule_window[0]}-{schedule_window[1]}")

    # ---- Blocks 10 & 11: simulate every rule and compare ------------------
    rule_results = run_all_rules(jobs, ops, data["capacity"])
    comparison = compare_rules(rule_results)
    best_by_criterion = best_rule_by_criterion(comparison)

    if selected_rule is None:
        selected_rule = overrides.get("dispatch_rule")
    if selected_rule is None and not comparison.empty:
        # default to the rule with the best on-time delivery, tie-broken by flow time
        selected_rule = comparison.sort_values(
            ["OnTimeDeliveryPct", "AvgFlowTime"], ascending=[False, True]).index[0]
    step(f"Block 10 simulated {len(rule_results)} dispatching rules; "
         f"selected {selected_rule}")

    # ---- Block 12: consolidate every exception ----------------------------
    all_exceptions = consolidate(
        detect_forecast_exceptions(selection),
        inv_exceptions,
        detect_capacity_exceptions(mps_result["capacity_plan"]),
        mrp_result["exceptions"],
        detect_supplier_exceptions(data["supplier"], mrp_result["releases"]),
        scheduling_exceptions({selected_rule: rule_results[selected_rule]})
        if selected_rule in rule_results else None,
    )
    step(f"Block 12 {len(all_exceptions)} exception(s) detected")

    # ---- assemble ----------------------------------------------------------
    result = {
        "data": data,
        "base_data": base_data,
        "overrides": overrides,
        "bom_tree": bom_tree,
        "horizon": horizon,
        "schedule_window": schedule_window,

        "forecast": forecast,
        "forecast_accuracy": accuracy,
        "forecast_selection": selection,
        "forecast_fit": fit,
        "orders": orders,

        "inventory_params": inv_params,
        "inventory_projection": inv_projection,
        "inventory_exceptions": inv_exceptions,

        "mps": mps_result["mps"],
        "mps_demand_input": mps_result["demand_input"],
        "capacity_plan": mps_result["capacity_plan"],
        "capacity_detail": mps_result["capacity_detail"],
        "mps_issues": mps_result["issues"],
        "mps_history": mps_result["history"],
        "mps_feasible": mps_result["feasible"],
        "mps_iterations": mps_result["iterations"],

        "gross_requirements": gross_requirements,

        "mrp": mrp_result["mrp"],
        "mrp_output": mrp_output_table(mrp_result["mrp"]),
        "releases": mrp_result["releases"],
        "pegging": mrp_result["pegging"],
        "mrp_exceptions": mrp_result["exceptions"],

        "production_orders": prod_orders,
        "purchase_reqs": purchase_reqs,
        "release_summary": release_summary,

        "jobs": jobs,
        "operations": ops,
        "rule_results": rule_results,
        "rule_comparison": comparison,
        "best_by_criterion": best_by_criterion,
        "selected_rule": selected_rule,
        "gantt": rule_results[selected_rule]["gantt"] if selected_rule in rule_results else pd.DataFrame(),
        "job_results": rule_results[selected_rule]["jobs"] if selected_rule in rule_results else pd.DataFrame(),
        "work_centre_results": rule_results[selected_rule]["wc"] if selected_rule in rule_results else pd.DataFrame(),

        "exceptions": all_exceptions,
        "log": log,
        "runtime_seconds": round(time.time() - t0, 2),
    }

    result["kpis"] = build_kpi_summary(result)
    score, breakdown = plan_health_score(result["kpis"])
    result["health_score"] = score
    result["health_breakdown"] = breakdown
    step(f"Done in {result['runtime_seconds']}s - plan health score {score}/100")

    return result


# ===========================================================================
def export_outputs(result, output_dir=None):
    """Write every result table to the outputs folder as CSV."""
    exports = {
        "Forecast_Output.csv":        result["forecast"],
        "Forecast_Accuracy.csv":      result["forecast_accuracy"],
        "Forecast_Selection.csv":     result["forecast_selection"],
        "Inventory_Parameters.csv":   result["inventory_params"],
        "Inventory_Projection.csv":   result["inventory_projection"],
        "MPS_Output.csv":             result["mps"],
        "Capacity_Plan.csv":          result["capacity_plan"],
        "Gross_Requirements.csv":     result["gross_requirements"],
        "MRP_Output.csv":             result["mrp_output"],
        "Planned_Order_Releases.csv": result["releases"],
        "Production_Orders.csv":      result["production_orders"],
        "Purchase_Requisitions.csv":  result["purchase_reqs"],
        "Job_List.csv":               result["jobs"],
        "Schedule_Gantt.csv":         result["gantt"],
        "Job_Results.csv":            result["job_results"],
        "WorkCentre_Results.csv":     result["work_centre_results"],
        "Rule_Comparison.csv":        result["rule_comparison"].reset_index()
                                      if not result["rule_comparison"].empty else pd.DataFrame(),
        "Best_Rule_By_Criterion.csv": result["best_by_criterion"],
        "Exception_Register.csv":     result["exceptions"],
        "KPI_Summary.csv":            pd.DataFrame([result["kpis"]]),
    }
    written = []
    for name, df in exports.items():
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            continue
        written.append(io_utils.save(df, name, output_dir or io_utils.OUTPUT_DIR))
    return written

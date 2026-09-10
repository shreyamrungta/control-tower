"""
Command-line runner for the Integrated Manufacturing Operations Control Tower.

    python run_pipeline.py                 run the baseline plan and export CSVs
    python run_pipeline.py --scenarios     also run every what-if scenario
    python run_pipeline.py --scenario "Supplier Delay - Derailleurs"
"""

import argparse
import sys

import pandas as pd

from engine.pipeline import run_pipeline, export_outputs
from engine.scenarios import build_scenarios, compare_to_baseline, summarise_impact

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)

BAR = "=" * 78


def banner(title):
    print(f"\n{BAR}\n{title}\n{BAR}")


def print_kpis(result):
    k = result["kpis"]
    rows = [
        ("Plan health score",        f"{result['health_score']}/100"),
        ("Forecast average MAPE",    f"{k.get('forecast_avg_mape')}%"),
        ("Models selected",          k.get("forecast_models", "-")),
        ("Inventory value",          f"{k.get('inventory_value', 0):,.0f}"),
        ("MPS feasible",             "Yes" if k.get("mps_feasible") else "No"),
        ("MPS revision iterations",  k.get("mps_iterations")),
        ("Rough-cut peak utilisation", f"{k.get('rccp_peak_utilisation')}%"),
        ("Capacity bottleneck",      k.get("rccp_bottleneck", "-")),
        ("Planned order releases",   k.get("mrp_planned_orders")),
        ("Past-due releases",        k.get("mrp_late_releases", 0)),
        ("Orders released / on hold", f"{k.get('orders_released')} / {k.get('orders_on_hold')}"),
        ("Purchase requisitions",    f"{k.get('purchase_reqs')} worth {k.get('purchase_value', 0):,.0f}"),
        ("Dispatching rule selected", k.get("sched_rule", "-")),
        ("Jobs scheduled",           k.get("sched_jobs")),
        ("Makespan",                 f"{k.get('sched_makespan')} h "
                                     f"({k.get('sched_makespan_periods')} periods)"),
        ("On-time delivery",         f"{k.get('sched_otd')}%"),
        ("Average flow time",        f"{k.get('sched_avg_flow')} h"),
        ("Average WIP",              k.get("sched_wip")),
        ("Shop-floor bottleneck",    k.get("sched_bottleneck", "-")),
        ("Exceptions (critical)",    f"{k.get('exceptions_total')} ({k.get('exceptions_critical')})"),
    ]
    width = max(len(r[0]) for r in rows)
    for label, value in rows:
        print(f"  {label:<{width}} : {value}")


def main():
    ap = argparse.ArgumentParser(description="Run the control tower pipeline")
    ap.add_argument("--scenarios", action="store_true", help="run every what-if scenario")
    ap.add_argument("--scenario", type=str, help="run one named scenario")
    ap.add_argument("--rule", type=str, help="force a dispatching rule")
    ap.add_argument("--window", type=str, default="1-8",
                    help="scheduling window, e.g. 1-8")
    ap.add_argument("--no-export", action="store_true", help="skip writing CSVs")
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.window.split("-"))

    banner("INTEGRATED MANUFACTURING OPERATIONS CONTROL TOWER - BASELINE RUN")
    baseline = run_pipeline(schedule_window=(lo, hi), selected_rule=args.rule,
                            verbose=True)

    banner("KEY PERFORMANCE INDICATORS")
    print_kpis(baseline)

    banner("DISPATCHING RULE COMPARISON (Block 11.2)")
    cols = ["Jobs", "Makespan", "AvgFlowTime", "AvgWaitingTime", "AvgTardiness",
            "MaxTardiness", "TardyJobs", "OnTimeDeliveryPct", "AvgUtilisation",
            "AvgWIP", "ThroughputUnitsPerPeriod"]
    cmp = baseline["rule_comparison"]
    print(cmp[[c for c in cols if c in cmp.columns]].to_string())

    banner("BEST RULE BY CRITERION (Block 11.3)")
    print(baseline["best_by_criterion"].to_string(index=False))

    banner("EXCEPTION REGISTER (Block 12.1)")
    exc = baseline["exceptions"]
    if exc.empty:
        print("  No exceptions.")
    else:
        print(exc["Severity"].value_counts().to_string())
        print()
        print(exc[["ExceptionID", "Module", "Item", "ExceptionType",
                   "Severity", "Period"]].head(20).to_string(index=False))

    if not args.no_export:
        written = export_outputs(baseline)
        banner(f"EXPORTED {len(written)} RESULT FILES")
        for p in written:
            print(f"  {p.split('/')[-1]}")

    # ---- Block 13 ---------------------------------------------------------
    library = build_scenarios(baseline["data"], baseline)
    to_run = []
    if args.scenario:
        if args.scenario not in library:
            print(f"\nUnknown scenario '{args.scenario}'. Available:")
            for s in library:
                print(f"  - {s}")
            sys.exit(1)
        to_run = [args.scenario]
    elif args.scenarios:
        to_run = [s for s in library if s != "Baseline"]

    if to_run:
        banner("BLOCK 13 - SCENARIO SIMULATION")
        summary_rows = []
        for name in to_run:
            spec = library[name]
            print(f"\n--- {name} ---")
            print(f"    {spec['description']}")
            res = run_pipeline(overrides=spec["overrides"],
                               schedule_window=(lo, hi), selected_rule=args.rule)
            comp = compare_to_baseline(baseline["kpis"], res["kpis"], name)
            changed = comp[comp["Impact"] != "No change"]
            print()
            print(changed[["KPI", "Baseline", "Scenario", "Change",
                           "ChangePct", "Impact"]].to_string(index=False)
                  if not changed.empty else "    No measurable change.")
            print()
            print("    " + summarise_impact(comp, name).replace("**", ""))
            summary_rows.append({
                "Scenario": name,
                "Category": spec["category"],
                "HealthScore": res["health_score"],
                "MPSFeasible": res["kpis"].get("mps_feasible"),
                "OTD%": res["kpis"].get("sched_otd"),
                "Makespan": res["kpis"].get("sched_makespan"),
                "OnHold": res["kpis"].get("orders_on_hold"),
                "Exceptions": res["kpis"].get("exceptions_total"),
            })

        banner("SCENARIO SUMMARY")
        base_row = {
            "Scenario": "Baseline", "Category": "Reference",
            "HealthScore": baseline["health_score"],
            "MPSFeasible": baseline["kpis"].get("mps_feasible"),
            "OTD%": baseline["kpis"].get("sched_otd"),
            "Makespan": baseline["kpis"].get("sched_makespan"),
            "OnHold": baseline["kpis"].get("orders_on_hold"),
            "Exceptions": baseline["kpis"].get("exceptions_total"),
        }
        print(pd.DataFrame([base_row] + summary_rows).to_string(index=False))

    print()


if __name__ == "__main__":
    main()

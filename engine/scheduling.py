"""
BLOCK 10 - SHOP-FLOOR SCHEDULING (SIMULATION)
==============================================================================
10.1 Select dispatching rule     FCFS / SPT / EDD / LPT / CR
10.2 Run scheduling simulation   sequence jobs at each work centre,
                                 honouring setup, wait and process times
10.3 Generate Gantt chart data   start & completion, idle, setup, wait
10.4 Calculate performance measures
10.5 Store results for the selected rule

Model
-----
A discrete-event job-shop simulation on a continuous HOUR axis.

  * Each work centre owns N identical parallel machines.
  * A job is one production order; its operations must run in routing order.
  * A job cannot start before its release period begins, and is DUE at the
    START of its due period - a sub-assembly has to be on the shelf when the
    parent order begins, not at the end of that week.

Calendar scaling - why processing times are stretched
-----------------------------------------------------
The simulation runs on a continuous clock of HOURS_PER_PERIOD hours per period,
but a work centre does not actually work all of those hours: WC03 has one
machine on a single 10-hour shift, so it offers 45 productive hours in an
80-hour week, not 80. Running the raw times against a 24/7 clock would make
every work centre look far emptier than it is and would contradict the
rough-cut capacity plan in Block 5.

So each operation is stretched by

    calendar factor = (HOURS_PER_PERIOD x NumMachines) / AvailableHoursPerPeriod

which makes one simulated period of machine time represent exactly the
AvailableHoursPerPeriod that Machine_Capacity.csv declares - shifts, working
days and efficiency all included. Utilisation from the simulation is then
directly comparable with utilisation from the capacity plan.

The event loop advances to the next moment at which either a machine frees up
or a job becomes ready, then assigns every machine it can at that instant,
choosing among the waiting operations with the active dispatching rule.
"""

import numpy as np
import pandas as pd

from .config import HOURS_PER_PERIOD, DISPATCH_RULES, UTILISATION_WARN

EPS = 1e-9


# ===========================================================================
# 10.1  DISPATCHING RULES
# ===========================================================================
def _select(candidates, rule, now, ctx):
    """Choose the next operation from the queue at one work centre.

    `candidates` is a list of job ids; ctx carries the data each rule needs.
    Ties are always broken by job id so that every run is reproducible.
    """
    if rule == "SPT":
        key = lambda j: (ctx["op_time"][j], j)
    elif rule == "LPT":
        key = lambda j: (-ctx["op_time"][j], j)
    elif rule == "EDD":
        key = lambda j: (ctx["due"][j], j)
    elif rule == "CR":
        # critical ratio = time remaining / work remaining; smallest first.
        # A ratio below 1 means the job is already behind.
        def key(j):
            work = max(ctx["remaining"][j], EPS)
            return ((ctx["due"][j] - now) / work, j)
    else:                                    # FCFS
        key = lambda j: (ctx["ready"][j], ctx["arrival"][j], j)
    return min(candidates, key=key)


# ===========================================================================
# 10.2  THE SIMULATION
# ===========================================================================
def simulate(jobs_df, ops_df, capacity_df, rule="FCFS",
             hours_per_period=HOURS_PER_PERIOD):
    """Run one dispatching rule. Returns (gantt_df, job_df, kpis, wc_df)."""
    if jobs_df.empty or ops_df.empty:
        return pd.DataFrame(), pd.DataFrame(), {}, pd.DataFrame()

    cap = capacity_df.set_index("WorkCentre").to_dict("index")
    machines = {wc: [0.0] * int(c["NumMachines"]) for wc, c in cap.items()}

    # calendar scaling - see the module docstring
    factor = {}
    for wc, c in cap.items():
        avail = float(c["AvailableHoursPerPeriod"])
        factor[wc] = (hours_per_period * int(c["NumMachines"]) / avail) if avail > 0 else 1.0

    # ---- job state --------------------------------------------------------
    job_ops, arrival, due = {}, {}, {}
    for jid, g in ops_df.groupby("JobID"):
        g = g.sort_values("OpSeq")
        seq = []
        for _, o in g.iterrows():
            k = factor.get(o["WorkCentre"], 1.0)
            seq.append({
                "OpSeq": int(o["OpSeq"]), "Operation": o["Operation"],
                "WorkCentre": o["WorkCentre"], "Item": o["Item"],
                "Quantity": float(o["Quantity"]),
                "SetupHrs": float(o["SetupHrs"]) * k,
                "RunHrs": float(o["RunHrs"]) * k,
                "ProcessHrs": float(o["ProcessHrs"]) * k,
            })
        job_ops[jid] = seq
        arrival[jid] = (int(g.iloc[0]["ReleasePeriod"]) - 1) * hours_per_period
        # due at the START of the due period: the parent needs it then
        due[jid] = (int(g.iloc[0]["DuePeriod"]) - 1) * hours_per_period

    next_op = {j: 0 for j in job_ops}
    ready = dict(arrival)
    remaining = {j: sum(o["ProcessHrs"] for o in ops) for j, ops in job_ops.items()}

    gantt = []
    t = min(arrival.values())
    guard = 0
    max_iterations = 100_000

    while any(next_op[j] < len(job_ops[j]) for j in job_ops):
        guard += 1
        if guard > max_iterations:
            raise RuntimeError("Scheduling simulation failed to converge")

        # ---- assign everything possible at the current instant ------------
        assigned = True
        while assigned:
            assigned = False
            for wc, free_times in machines.items():
                for mi in range(len(free_times)):
                    if free_times[mi] > t + EPS:
                        continue
                    queue = [j for j in job_ops
                             if next_op[j] < len(job_ops[j])
                             and job_ops[j][next_op[j]]["WorkCentre"] == wc
                             and ready[j] <= t + EPS]
                    if not queue:
                        continue

                    ctx = {
                        "op_time": {j: job_ops[j][next_op[j]]["ProcessHrs"] for j in queue},
                        "due": due, "ready": ready, "arrival": arrival,
                        "remaining": remaining,
                    }
                    jid = _select(queue, rule, t, ctx)
                    op = job_ops[jid][next_op[jid]]

                    start, end = t, t + op["ProcessHrs"]
                    gantt.append({
                        "JobID": jid, "Item": op["Item"], "Quantity": op["Quantity"],
                        "OpSeq": op["OpSeq"], "Operation": op["Operation"],
                        "WorkCentre": wc, "Machine": f"{wc}-M{mi+1}",
                        "Start": round(start, 3), "End": round(end, 3),
                        "SetupHrs": round(op["SetupHrs"], 3),
                        "RunHrs": round(op["RunHrs"], 3),
                        "ProcessHrs": round(op["ProcessHrs"], 3),
                        "WaitHrs": round(start - ready[jid], 3),
                        "DueHrs": due[jid], "Rule": rule,
                    })

                    machines[wc][mi] = end
                    ready[jid] = end
                    remaining[jid] -= op["ProcessHrs"]
                    next_op[jid] += 1
                    assigned = True

        # ---- advance the clock to the next event --------------------------
        future = [f for ft in machines.values() for f in ft if f > t + EPS]
        future += [ready[j] for j in job_ops
                   if next_op[j] < len(job_ops[j]) and ready[j] > t + EPS]
        if not future:
            break
        t = min(future)

    gantt_df = pd.DataFrame(gantt).sort_values(["Start", "WorkCentre"]).reset_index(drop=True)
    job_df = _job_results(gantt_df, arrival, due, rule)
    wc_df = _work_centre_results(gantt_df, machines, capacity_df)
    kpis = _kpis(job_df, gantt_df, wc_df, rule, hours_per_period)
    return gantt_df, job_df, kpis, wc_df


# ===========================================================================
# 10.4  PERFORMANCE MEASURES
# ===========================================================================
def _job_results(gantt_df, arrival, due, rule):
    rows = []
    for jid, g in gantt_df.groupby("JobID"):
        completion = g["End"].max()
        proc = g["ProcessHrs"].sum()
        wait = g["WaitHrs"].sum()
        flow = completion - arrival[jid]
        lateness = completion - due[jid]
        rows.append({
            "JobID": jid,
            "Item": g.iloc[0]["Item"],
            "Quantity": g.iloc[0]["Quantity"],
            "Arrival": round(arrival[jid], 2),
            "DueHrs": round(due[jid], 2),
            "Completion": round(completion, 2),
            "FlowTime": round(flow, 2),
            "ProcessTime": round(proc, 2),
            "WaitingTime": round(wait, 2),
            "Lateness": round(lateness, 2),
            "Tardiness": round(max(0.0, lateness), 2),
            "OnTime": lateness <= EPS,
            "Rule": rule,
        })
    return pd.DataFrame(rows).sort_values("Completion").reset_index(drop=True)


def _work_centre_results(gantt_df, machines, capacity_df):
    if gantt_df.empty:
        return pd.DataFrame()
    makespan = gantt_df["End"].max()
    rows = []
    for _, c in capacity_df.iterrows():
        wc = c["WorkCentre"]
        g = gantt_df[gantt_df["WorkCentre"] == wc]
        n = int(c["NumMachines"])
        busy = g["ProcessHrs"].sum()
        setup = g["SetupHrs"].sum()
        capacity_hrs = makespan * n
        rows.append({
            "WorkCentre": wc,
            "Description": c["Description"],
            "Machines": n,
            "Operations": len(g),
            "BusyHours": round(busy, 2),
            "SetupHours": round(setup, 2),
            "IdleHours": round(max(0.0, capacity_hrs - busy), 2),
            "CapacityHours": round(capacity_hrs, 2),
            "Utilisation": round(busy / capacity_hrs, 4) if capacity_hrs else 0.0,
            "SetupRatio": round(setup / busy, 4) if busy else 0.0,
            "Bottleneck": False,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df.loc[df["Utilisation"].idxmax(), "Bottleneck"] = True
    return df


def _kpis(job_df, gantt_df, wc_df, rule, hours_per_period):
    if job_df.empty:
        return {}
    makespan = gantt_df["End"].max()
    total_units = job_df["Quantity"].sum()
    n = len(job_df)

    return {
        "Rule": rule,
        "Jobs": n,
        "Units": int(total_units),
        "Makespan": round(makespan, 2),
        "MakespanPeriods": round(makespan / hours_per_period, 2),
        "AvgFlowTime": round(job_df["FlowTime"].mean(), 2),
        "MaxFlowTime": round(job_df["FlowTime"].max(), 2),
        "AvgWaitingTime": round(job_df["WaitingTime"].mean(), 2),
        "AvgLateness": round(job_df["Lateness"].mean(), 2),
        "AvgTardiness": round(job_df["Tardiness"].mean(), 2),
        "MaxTardiness": round(job_df["Tardiness"].max(), 2),
        "TardyJobs": int((job_df["Tardiness"] > EPS).sum()),
        "OnTimeDeliveryPct": round(100.0 * job_df["OnTime"].sum() / n, 1),
        "AvgUtilisation": round(100.0 * wc_df["Utilisation"].mean(), 1) if not wc_df.empty else 0.0,
        "MaxUtilisation": round(100.0 * wc_df["Utilisation"].max(), 1) if not wc_df.empty else 0.0,
        "Bottleneck": wc_df.loc[wc_df["Bottleneck"], "WorkCentre"].iloc[0] if not wc_df.empty else "-",
        "ThroughputJobsPerPeriod": round(n / (makespan / hours_per_period), 2) if makespan else 0.0,
        "ThroughputUnitsPerPeriod": round(total_units / (makespan / hours_per_period), 1) if makespan else 0.0,
        "AvgWIP": round(job_df["FlowTime"].sum() / makespan, 2) if makespan else 0.0,
        "TaktTimeHrs": round(makespan / total_units, 4) if total_units else 0.0,
        "TotalSetupHours": round(gantt_df["SetupHrs"].sum(), 2),
    }


# ===========================================================================
# 10.5 / 11.1  RUN EVERY RULE AND STORE THE RESULTS
# ===========================================================================
def run_all_rules(jobs_df, ops_df, capacity_df, rules=None,
                  hours_per_period=HOURS_PER_PERIOD):
    """11.1 - repeat the scheduling simulation for every dispatching rule."""
    rules = rules or DISPATCH_RULES
    results = {}
    for rule in rules:
        gantt, jobs, kpis, wc = simulate(jobs_df, ops_df, capacity_df,
                                         rule, hours_per_period)
        results[rule] = {"gantt": gantt, "jobs": jobs, "kpis": kpis, "wc": wc}
    return results


def compare_rules(results):
    """11.2 - one row per rule, every KPI side by side."""
    rows = [r["kpis"] for r in results.values() if r["kpis"]]
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("Rule")


# criterion -> (column, want_minimum)
CRITERIA = {
    "Makespan":            ("Makespan", True),
    "Average Flow Time":   ("AvgFlowTime", True),
    "Average Tardiness":   ("AvgTardiness", True),
    "Maximum Tardiness":   ("MaxTardiness", True),
    "Tardy Jobs":          ("TardyJobs", True),
    "On-Time Delivery %":  ("OnTimeDeliveryPct", False),
    "Average WIP":         ("AvgWIP", True),
    "Throughput":          ("ThroughputUnitsPerPeriod", False),
    "Average Utilisation": ("AvgUtilisation", False),
    "Average Waiting Time": ("AvgWaitingTime", True),
}


def best_rule_by_criterion(comparison_df):
    """11.3 - identify the winning rule for each criterion."""
    if comparison_df.empty:
        return pd.DataFrame()
    rows = []
    for label, (col, minimise) in CRITERIA.items():
        if col not in comparison_df.columns:
            continue
        series = comparison_df[col]
        winner = series.idxmin() if minimise else series.idxmax()
        rows.append({
            "Criterion": label,
            "BestRule": winner,
            "BestValue": series.loc[winner],
            "WorstRule": series.idxmax() if minimise else series.idxmin(),
            "WorstValue": series.max() if minimise else series.min(),
            "Direction": "lower is better" if minimise else "higher is better",
        })
    return pd.DataFrame(rows)


def scheduling_exceptions(results, wc_warn=UTILISATION_WARN):
    """12.1 - scheduling-side exceptions across the compared rules."""
    rows = []
    for rule, r in results.items():
        if not r["kpis"]:
            continue
        k = r["kpis"]
        if k["TardyJobs"] > 0:
            rows.append({
                "Module": "Scheduling", "Item": rule,
                "ExceptionType": "Late Jobs",
                "Severity": "High" if k["OnTimeDeliveryPct"] < 90 else "Medium",
                "Period": 0,
                "Message": (f"{rule}: {k['TardyJobs']} of {k['Jobs']} jobs late, "
                            f"OTD {k['OnTimeDeliveryPct']}%, max tardiness "
                            f"{k['MaxTardiness']:.1f} h"),
                "RecommendedAction": "Switch to EDD, add capacity, or re-phase the MPS",
            })
        if not r["wc"].empty:
            for _, w in r["wc"][r["wc"]["Utilisation"] >= wc_warn].iterrows():
                rows.append({
                    "Module": "Scheduling", "Item": w["WorkCentre"],
                    "ExceptionType": "Capacity Bottleneck",
                    "Severity": "High", "Period": 0,
                    "Message": (f"{rule}: {w['WorkCentre']} ({w['Description']}) at "
                                f"{w['Utilisation']*100:.0f}% utilisation"),
                    "RecommendedAction": "Add a shift, offload work, or re-balance the routing",
                })
    return pd.DataFrame(rows)

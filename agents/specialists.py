"""
MULTI-AGENT LAYER - the specialist agents
==============================================================================
Six domain agents, one per area of the flow chart, each watching its own slice
of the plan:

    ForecastAgent   Block 3   accuracy, bias, model suitability
    InventoryAgent  Block 4   cover, stockout risk, working capital
    SupplyAgent     Block 7/8 supplier risk, purchase exposure, expedites
    CapacityAgent   Block 5   bottleneck identification, overload
    ScheduleAgent   Block 10/11 dispatching rule choice, tardiness
    ExceptionAgent  Block 12  cross-module root-cause analysis

Every figure quoted comes from an engine table - the agents interpret, they do
not calculate.
"""

import pandas as pd

from .base import Agent
from engine.config import MAPE_ALERT_THRESHOLD, TRACKING_SIGNAL_LIMIT


# ===========================================================================
class ForecastAgent(Agent):
    name = "ForecastAgent"
    role = "Watches demand forecast quality and model selection (Block 3)"

    def observe(self):
        self.observations["selection"] = self.bb.table("forecast_selection")
        self.observations["accuracy"] = self.bb.table("forecast_accuracy")

    def reason(self):
        sel = self.observations["selection"]
        acc = self.observations["accuracy"]
        out = []
        if sel is None or sel.empty:
            return out

        out.append(self.finding(
            "Forecast models selected",
            "; ".join(f"{r['Item']} -> {r['SelectedMethod']} (MAPE {r['MAPE']}%)"
                      for _, r in sel.iterrows()),
            severity="Info", module="Forecast"))

        for _, r in sel.iterrows():
            if r["MAPE"] > MAPE_ALERT_THRESHOLD:
                out.append(self.finding(
                    f"{r['Item']} forecast is unreliable",
                    f"Even the best of the five models ({r['SelectedMethod']}) leaves "
                    f"MAPE at {r['MAPE']}%, above the {MAPE_ALERT_THRESHOLD}% tolerance. "
                    f"Safety stock is doing the work the forecast should be doing.",
                    severity="High", module="Forecast", item=r["Item"],
                    mape=r["MAPE"]))

            if abs(r["TrackingSignal"]) > TRACKING_SIGNAL_LIMIT:
                direction = "under" if r["TrackingSignal"] > 0 else "over"
                # is a trend-capable model available and close behind?
                alt = None
                if acc is not None and not acc.empty:
                    cand = acc[(acc["Item"] == r["Item"]) &
                               (acc["Method"].isin(["Holt", "Holt-Winters"]))]
                    if not cand.empty:
                        best = cand.sort_values("MAPE").iloc[0]
                        alt = (best["Method"], best["MAPE"],
                               abs(best["TrackingSignal"]))
                detail = (f"Tracking signal is {r['TrackingSignal']} against a limit of "
                          f"+/-{TRACKING_SIGNAL_LIMIT}, so {r['SelectedMethod']} is "
                          f"persistently {direction}-forecasting rather than erring "
                          f"randomly. Bias accumulates into the MPS period after period.")
                if alt:
                    detail += (f" {alt[0]} scores a slightly worse MAPE ({alt[1]}%) but a "
                               f"tracking signal of only {alt[2]}, so it trades a little "
                               f"accuracy for a far less biased plan.")
                out.append(self.finding(
                    f"{r['Item']} forecast is biased, not just noisy",
                    detail, severity="Medium", module="Forecast", item=r["Item"],
                    tracking_signal=r["TrackingSignal"],
                    alternative=alt[0] if alt else None))
        return out

    def act(self):
        acts = []
        for f in self.bb.findings_by(agent=self.name):
            alt = f.evidence.get("alternative")
            if alt and f.item:
                acts.append(self.action(
                    f"Force {alt} for {f.item}",
                    f"{f.item} is selected on MAPE alone, which rewards a model that "
                    f"hugs recent history but lags a trend. {alt} tracks the trend and "
                    f"removes the bias feeding into the master schedule.",
                    overrides={"forecast_method": {f.item: alt}},
                    rerun_from="forecast",
                    expected_effect="Lower tracking signal; MPS quantities rise to meet "
                                    "the real trend instead of chronically undershooting",
                    confidence="High"))
        return acts


# ===========================================================================
class InventoryAgent(Agent):
    name = "InventoryAgent"
    role = "Watches stock cover, stockout risk and working capital (Block 4)"

    def observe(self):
        self.observations["params"] = self.bb.table("inventory_params")
        self.observations["exceptions"] = self.bb.table("inventory_exceptions")

    def reason(self):
        p = self.observations["params"]
        exc = self.observations["exceptions"]
        out = []
        if p is None or p.empty:
            return out

        value = p["InventoryValue"].sum()
        top = p.nlargest(3, "InventoryValue")
        out.append(self.finding(
            "Working capital concentration",
            f"Total inventory is valued at {value:,.0f}. The three largest holdings are "
            + ", ".join(f"{r['Item']} ({r['InventoryValue']:,.0f}, "
                        f"{r['PeriodsOfSupply']:.1f} periods of cover)"
                        for _, r in top.iterrows())
            + f", together {top['InventoryValue'].sum()/value*100:.0f}% of the total.",
            severity="Info", module="Inventory", total_value=value))

        if exc is not None and not exc.empty:
            crit = exc[exc["Severity"] == "Critical"]
            for _, r in crit.iterrows():
                row = p[p["Item"] == r["Item"]]
                lt = int(row["LeadTime"].iloc[0]) if not row.empty else 0
                out.append(self.finding(
                    f"{r['Item']} will run out",
                    f"{r['Message']}. With a {lt}-period lead time, a replenishment "
                    f"ordered today would not arrive until period {lt + 1}, so this "
                    f"cannot be recovered by ordering alone.",
                    severity="Critical", module="Inventory", item=r["Item"],
                    lead_time=lt))

        # items whose statistical safety stock far exceeds the master value
        under = p[(p["SafetyStock_Statistical"] > p["SafetyStock_Master"] * 1.5) &
                  (p["AvgPeriodDemand"] > 0)]
        if not under.empty:
            out.append(self.finding(
                "Safety stock is set below the service-level requirement",
                f"{len(under)} item(s) hold less safety stock than a 95% service level "
                f"requires given their demand variability and lead time: "
                + ", ".join(f"{r['Item']} ({r['SafetyStock_Master']:.0f} held vs "
                            f"{r['SafetyStock_Statistical']:.0f} required)"
                            for _, r in under.head(4).iterrows()) + ".",
                severity="Medium", module="Inventory",
                items=under["Item"].tolist()))

        excess = p[(p["SafetyStock_Master"] > 0) &
                   (p["OnHand"] > 3 * p["SafetyStock_Master"])]
        if not excess.empty:
            tied = excess["InventoryValue"].sum()
            out.append(self.finding(
                "Cash tied up in over-stocked items",
                f"{len(excess)} item(s) hold more than three times their safety stock, "
                f"tying up {tied:,.0f} in working capital.",
                severity="Low", module="Inventory", value=tied))
        return out

    def act(self):
        acts = []
        p = self.observations["params"]
        for f in self.bb.findings_by(agent=self.name, severity="Critical"):
            row = p[p["Item"] == f.item]
            if row.empty:
                continue
            lt = int(row["LeadTime"].iloc[0])
            if lt > 1:
                acts.append(self.action(
                    f"Expedite {f.item} (lead time {lt} -> {max(1, lt//2)})",
                    f"{f.item} stocks out before a normal replenishment can arrive. "
                    f"Expedited freight is the only lever that closes the gap in time.",
                    overrides={"lead_time": {f.item: max(1, lt // 2)}},
                    rerun_from="mrp",
                    expected_effect="Removes the past-due release and unblocks the "
                                    "production orders waiting on this component",
                    confidence="High"))
        return acts


# ===========================================================================
class SupplyAgent(Agent):
    name = "SupplyAgent"
    role = "Watches supplier risk and purchase exposure (Blocks 7-8)"

    def observe(self):
        self.observations["supplier"] = self.bb.table("data")["supplier"]
        self.observations["purchase"] = self.bb.table("purchase_reqs")
        self.observations["releases"] = self.bb.table("releases")
        self.observations["bom_tree"] = self.bb.table("bom_tree")

    def reason(self):
        sup = self.observations["supplier"]
        pur = self.observations["purchase"]
        tree = self.observations["bom_tree"]
        out = []

        if pur is not None and not pur.empty:
            by_sup = (pur.groupby("Supplier")
                      .agg(Value=("Value", "sum"), Orders=("ReqID", "count"))
                      .sort_values("Value", ascending=False))
            top = by_sup.iloc[0]
            share = top["Value"] / by_sup["Value"].sum() * 100
            name = sup[sup["Supplier"] == by_sup.index[0]]["SupplierName"].iloc[0] \
                if not sup[sup["Supplier"] == by_sup.index[0]].empty else by_sup.index[0]
            out.append(self.finding(
                "Purchase exposure is concentrated",
                f"{by_sup.index[0]} ({name}) carries {top['Value']:,.0f} across "
                f"{int(top['Orders'])} requisitions - {share:.0f}% of total purchase "
                f"value of {by_sup['Value'].sum():,.0f}.",
                severity="Info", module="Supply", supplier=by_sup.index[0]))

            expedite = pur[pur["Status"].str.startswith("Past Due")]
            if not expedite.empty:
                out.append(self.finding(
                    "Purchase orders are already past due",
                    f"{len(expedite)} requisition(s) worth {expedite['Value'].sum():,.0f} "
                    f"needed to be placed before period 1: "
                    + ", ".join(f"{r['Item']} ({int(r['Quantity']):,} units from "
                                f"{r['Supplier']})" for _, r in expedite.iterrows())
                    + ". These cannot be recovered inside the normal lead time.",
                    severity="Critical", module="Supply",
                    items=expedite["Item"].tolist()))

        # unreliable supplier on a component that feeds several parents
        if sup is not None and not sup.empty and tree is not None:
            ordered = set(pur["Item"]) if pur is not None and not pur.empty else set()
            risky = sup[(sup["Reliability"] < 0.85) & (sup["Item"].isin(ordered))]
            for _, r in risky.iterrows():
                n_parents = len(tree.parents.get(r["Item"], []))
                sev = "High" if (r["Reliability"] < 0.82 and n_parents >= 2) else "Medium"
                out.append(self.finding(
                    f"{r['Item']} depends on an unreliable supplier",
                    f"{r['SupplierName']} delivers on time only {r['Reliability']*100:.0f}% "
                    f"of the time on a {int(r['LeadTime'])}-period lead time, and "
                    f"{r['Item']} feeds {n_parents} parent item(s). A single missed "
                    f"delivery stops more than one assembly line.",
                    severity=sev, module="Supply", item=r["Item"],
                    reliability=r["Reliability"], parents=n_parents,
                    supplier=r["Supplier"]))
        return out

    def act(self):
        acts = []
        sup = self.observations["supplier"]
        for f in self.bb.findings_by(agent=self.name):
            if not f.item or "reliability" not in f.evidence:
                continue
            others = sup[(sup["Supplier"] != f.evidence["supplier"])]
            if others.empty:
                continue
            best = others.sort_values(["Reliability", "LeadTime"],
                                      ascending=[False, True]).iloc[0]
            acts.append(self.action(
                f"Dual-source {f.item} with {best['Supplier']}",
                f"{best['SupplierName']} runs at {best['Reliability']*100:.0f}% "
                f"reliability on a {int(best['LeadTime'])}-period lead time, against the "
                f"incumbent's {f.evidence['reliability']*100:.0f}%.",
                overrides={"supplier": {f.item: best["Supplier"]},
                           "lead_time": {f.item: int(best["LeadTime"])}},
                rerun_from="mrp",
                expected_effect="Cuts supply risk on a component feeding "
                                f"{f.evidence.get('parents', 1)} parents",
                confidence="Medium"))
        return acts


# ===========================================================================
class CapacityAgent(Agent):
    name = "CapacityAgent"
    role = "Watches the capacity plan and identifies the true bottleneck (Block 5)"

    def observe(self):
        self.observations["plan"] = self.bb.table("capacity_plan")
        self.observations["history"] = self.bb.table("mps_history")
        self.observations["capacity"] = self.bb.table("data")["capacity"]

    def reason(self):
        plan = self.observations["plan"]
        hist = self.observations["history"]
        cap = self.observations["capacity"]
        out = []
        if plan is None or plan.empty:
            return out

        util = plan.groupby("WorkCentre")["Utilisation"].agg(["mean", "max"])
        bottleneck = util["mean"].idxmax()
        row = cap[cap["WorkCentre"] == bottleneck].iloc[0]
        out.append(self.finding(
            f"{bottleneck} is the constraint",
            f"{bottleneck} ({row['Description']}) averages "
            f"{util.loc[bottleneck, 'mean']*100:.0f}% utilisation and peaks at "
            f"{util.loc[bottleneck, 'max']*100:.0f}%, the highest of the six work "
            f"centres. It runs {int(row['NumMachines'])} machine(s) on "
            f"{int(row['ShiftsPerDay'])} shift(s), giving "
            f"{row['AvailableHoursPerPeriod']:.0f} hours per period. Throughput of the "
            f"whole plant is set here.",
            severity="Info", module="Capacity", item=bottleneck,
            mean_util=util.loc[bottleneck, "mean"],
            peak_util=util.loc[bottleneck, "max"]))

        over = plan[plan["Overloaded"]]
        if not over.empty:
            for wc, g in over.groupby("WorkCentre"):
                worst = g.loc[g["OverloadHours"].idxmax()]
                out.append(self.finding(
                    f"{wc} cannot deliver the master schedule",
                    f"{wc} is overloaded in {len(g)} period(s). The worst is period "
                    f"{int(worst['Period'])}, which needs {worst['RequiredHours']:.0f} "
                    f"hours against {worst['AvailableHours']:.0f} available - a shortfall "
                    f"of {worst['OverloadHours']:.0f} hours "
                    f"({worst['Utilisation']*100:.0f}% utilisation).",
                    severity="Critical", module="Capacity", item=wc,
                    overload_hours=worst["OverloadHours"],
                    period=int(worst["Period"])))

        if hist is not None and not hist.empty and len(hist) > 1:
            first, last = hist.iloc[0], hist.iloc[-1]
            verdict = "reached a feasible plan" if last["Feasible"] else "could not reach feasibility"
            out.append(self.finding(
                "Master schedule required level-loading",
                f"The first-pass MPS had {int(first['CapacityOverloads'])} capacity "
                f"overload(s) at a peak utilisation of {first['PeakUtilisation']*100:.0f}%. "
                f"After {len(hist)} revision iteration(s), pulling production into earlier "
                f"periods with spare capacity, the schedule {verdict} at "
                f"{last['PeakUtilisation']*100:.0f}% peak utilisation.",
                severity="Info" if last["Feasible"] else "Critical",
                module="Capacity", iterations=len(hist)))

        idle = util[util["mean"] < 0.45]
        if not idle.empty and len(idle) < len(util):
            out.append(self.finding(
                "Load is unbalanced across the shop",
                f"{', '.join(idle.index)} average below 45% utilisation while "
                f"{bottleneck} runs at {util.loc[bottleneck, 'mean']*100:.0f}%. "
                f"Capacity exists, but not where the routing needs it.",
                severity="Low", module="Capacity"))
        return out

    def act(self):
        acts = []
        for f in self.bb.findings_by(agent=self.name, severity="Critical"):
            if f.module != "Capacity" or not f.item:
                continue
            acts.append(self.action(
                f"Add a shift at {f.item}",
                f"{f.item} is short {f.evidence.get('overload_hours', 0):.0f} hours in "
                f"period {f.evidence.get('period', '?')}. An extra shift adds capacity "
                f"without capital expenditure.",
                overrides={"capacity": {f.item: {"ShiftsPerDay": "+1"}}},
                rerun_from="mps",
                expected_effect="Removes the overload and shortens the MPS revision loop",
                confidence="High"))
            acts.append(self.action(
                f"Add a machine at {f.item}",
                f"A permanent capacity increase at {f.item}, the structural constraint "
                f"on plant throughput.",
                overrides={"capacity": {f.item: {"NumMachines": "+1"}}},
                rerun_from="mps",
                expected_effect="Raises sustainable throughput; requires capital approval",
                confidence="Medium"))
        return acts


# ===========================================================================
class ScheduleAgent(Agent):
    name = "ScheduleAgent"
    role = "Watches shop-floor performance and dispatching rule choice (Blocks 10-11)"

    def observe(self):
        self.observations["comparison"] = self.bb.table("rule_comparison")
        self.observations["best"] = self.bb.table("best_by_criterion")
        self.observations["jobs"] = self.bb.table("job_results")
        self.observations["wc"] = self.bb.table("work_centre_results")
        self.observations["selected"] = self.bb.table("selected_rule")

    def reason(self):
        cmp = self.observations["comparison"]
        jobs = self.observations["jobs"]
        selected = self.observations["selected"]
        out = []
        if cmp is None or cmp.empty:
            return out

        # the classic trade-off: SPT for flow time, EDD/FCFS for due dates
        flow_best = cmp["AvgFlowTime"].idxmin()
        otd_best = cmp["OnTimeDeliveryPct"].idxmax()
        out.append(self.finding(
            "No dispatching rule wins on every measure",
            f"{flow_best} gives the shortest average flow time "
            f"({cmp.loc[flow_best, 'AvgFlowTime']:.1f} h vs "
            f"{cmp.loc[selected, 'AvgFlowTime']:.1f} h for {selected}) and the lowest "
            f"WIP, but {otd_best} delivers the best on-time performance at "
            f"{cmp.loc[otd_best, 'OnTimeDeliveryPct']:.1f}%. "
            f"{flow_best} achieves speed by running short jobs first, which is exactly "
            f"what pushes long jobs past their due dates.",
            severity="Info", module="Scheduling",
            flow_best=flow_best, otd_best=otd_best))

        row = cmp.loc[selected]
        if row["TardyJobs"] > 0:
            late = jobs[jobs["Tardiness"] > 0] if jobs is not None else pd.DataFrame()
            worst = late.nlargest(3, "Tardiness") if not late.empty else pd.DataFrame()
            detail = (f"Under {selected}, {int(row['TardyJobs'])} of {int(row['Jobs'])} "
                      f"jobs finish late ({row['OnTimeDeliveryPct']:.1f}% on time). "
                      f"The worst overrun is {row['MaxTardiness']:.1f} hours.")
            if not worst.empty:
                detail += (" Late jobs: "
                           + ", ".join(f"{r['JobID']} ({r['Item']}, "
                                       f"{r['Tardiness']:.0f} h late)"
                                       for _, r in worst.iterrows()) + ".")
            out.append(self.finding(
                "Jobs are finishing late",
                detail,
                severity="High" if row["OnTimeDeliveryPct"] < 90 else "Medium",
                module="Scheduling", tardy=int(row["TardyJobs"]),
                otd=row["OnTimeDeliveryPct"]))

        wc = self.observations["wc"]
        if wc is not None and not wc.empty:
            b = wc[wc["Bottleneck"]].iloc[0]
            out.append(self.finding(
                "Shop-floor bottleneck confirmed",
                f"In the detailed simulation, {b['WorkCentre']} ({b['Description']}) runs "
                f"at {b['Utilisation']*100:.0f}% with {b['SetupHours']:.0f} hours lost to "
                f"setup - {b['SetupRatio']*100:.0f}% of its busy time. Cutting setup here "
                f"buys capacity for free.",
                severity="Info", module="Scheduling", item=b["WorkCentre"],
                setup_ratio=b["SetupRatio"]))

        # rough-cut vs detailed utilisation gap - a genuinely useful insight
        rccp_peak = self.bb.kpi("rccp_peak_utilisation")
        sim_avg = self.bb.kpi("sched_avg_util")
        if rccp_peak and sim_avg and rccp_peak > sim_avg * 1.8:
            out.append(self.finding(
                "Rough-cut capacity overstates the peak",
                f"Rough-cut capacity planning peaks at {rccp_peak:.0f}% while the detailed "
                f"simulation averages {sim_avg:.0f}%. Rough-cut loads every component into "
                f"the same period as its parent; MRP then spreads that work backwards by "
                f"each item's lead time. The rough-cut figure is the right one for sizing "
                f"the MPS, but it is not what the shop floor actually experiences.",
                severity="Info", module="Scheduling"))
        return out

    def act(self):
        cmp = self.observations["comparison"]
        selected = self.observations["selected"]
        acts = []
        if cmp is None or cmp.empty:
            return acts

        otd_best = cmp["OnTimeDeliveryPct"].idxmax()
        if otd_best != selected and cmp.loc[otd_best, "OnTimeDeliveryPct"] > cmp.loc[selected, "OnTimeDeliveryPct"]:
            acts.append(self.action(
                f"Switch dispatching rule to {otd_best}",
                f"{otd_best} lifts on-time delivery from "
                f"{cmp.loc[selected, 'OnTimeDeliveryPct']:.1f}% to "
                f"{cmp.loc[otd_best, 'OnTimeDeliveryPct']:.1f}%.",
                overrides={"dispatch_rule": otd_best},
                rerun_from="scheduling",
                expected_effect=f"OTD +{cmp.loc[otd_best, 'OnTimeDeliveryPct'] - cmp.loc[selected, 'OnTimeDeliveryPct']:.1f} pp",
                confidence="High"))

        flow_best = cmp["AvgFlowTime"].idxmin()
        if flow_best != selected:
            acts.append(self.action(
                f"Switch dispatching rule to {flow_best}",
                f"If the priority is throughput and WIP rather than due dates, "
                f"{flow_best} cuts average flow time to "
                f"{cmp.loc[flow_best, 'AvgFlowTime']:.1f} h and WIP to "
                f"{cmp.loc[flow_best, 'AvgWIP']:.2f}.",
                overrides={"dispatch_rule": flow_best},
                rerun_from="scheduling",
                expected_effect="Lower WIP and flow time, at some cost to on-time delivery",
                confidence="Medium"))
        return acts


# ===========================================================================
class ExceptionAgent(Agent):
    """Reads what every other agent found and traces problems to their source."""

    name = "ExceptionAgent"
    role = "Cross-module root-cause analysis (Block 12)"

    def observe(self):
        self.observations["exceptions"] = self.bb.table("exceptions")
        self.observations["orders"] = self.bb.table("production_orders")
        self.observations["pegging"] = self.bb.table("pegging")
        self.observations["bom_tree"] = self.bb.table("bom_tree")

    def reason(self):
        exc = self.observations["exceptions"]
        orders = self.observations["orders"]
        tree = self.observations["bom_tree"]
        out = []
        if exc is None or exc.empty:
            return out

        counts = exc["Severity"].value_counts().to_dict()
        out.append(self.finding(
            "Exception register",
            f"{len(exc)} open exception(s): {counts.get('Critical', 0)} critical, "
            f"{counts.get('High', 0)} high, {counts.get('Medium', 0)} medium, "
            f"{counts.get('Low', 0)} low, spanning "
            f"{exc['Module'].nunique()} module(s).",
            severity="Info", module="Exceptions"))

        # --- root cause: which held orders trace back to which component ---
        if orders is not None and not orders.empty:
            held = orders[orders["Status"] == "On Hold"]
            if not held.empty:
                blockers = {}
                for _, o in held.iterrows():
                    note = str(o["Notes"])
                    for part in note.replace("Missing:", "").replace("Awaiting:", "").split(","):
                        name = part.strip().split(" (")[0].strip()
                        if name and name not in ("All components available", ""):
                            blockers[name] = blockers.get(name, 0) + 1
                if blockers:
                    ranked = sorted(blockers.items(), key=lambda kv: -kv[1])
                    top_name, top_count = ranked[0]
                    n_parents = len(tree.parents.get(top_name, [])) if tree else 0
                    out.append(self.finding(
                        "Held orders trace to a small number of components",
                        f"{len(held)} production order(s) are on hold. The single biggest "
                        f"blocker is {top_name}, which alone holds up {top_count} order(s)"
                        + (f" because it feeds {n_parents} parent items" if n_parents > 1 else "")
                        + ". Full blocker list: "
                        + ", ".join(f"{k} ({v} orders)" for k, v in ranked[:5]) + ".",
                        severity="Critical" if top_count > 5 else "High",
                        module="Exceptions", item=top_name,
                        blockers=dict(ranked[:5]), held_orders=len(held)))

        # --- the false-comfort check ---------------------------------------
        otd = self.bb.kpi("sched_otd")
        on_hold = self.bb.kpi("orders_on_hold", 0)
        released = self.bb.kpi("orders_released", 0)
        if otd is not None and otd >= 99 and on_hold > released * 0.3:
            out.append(self.finding(
                "On-time delivery is flattering the plan",
                f"On-time delivery reads {otd:.0f}%, but {on_hold} of "
                f"{on_hold + released} candidate orders never reached the shop floor - "
                f"they are held waiting for material. The schedule looks healthy because "
                f"the work that would have strained it was never released. Judge this "
                f"plan on held orders, not on OTD.",
                severity="High", module="Exceptions"))
        return out

    def act(self):
        acts = []
        for f in self.bb.findings_by(agent=self.name):
            if "blockers" not in f.evidence:
                continue
            for item in list(f.evidence["blockers"])[:2]:
                acts.append(self.action(
                    f"Clear the {item} constraint",
                    f"{item} is blocking {f.evidence['blockers'][item]} production "
                    f"order(s). Releasing this one component unblocks more work than any "
                    f"other single intervention.",
                    overrides={"lead_time": {item: 1}},
                    rerun_from="mrp",
                    expected_effect=f"Releases up to {f.evidence['blockers'][item]} "
                                    f"held orders",
                    confidence="Medium"))
        return acts


ALL_SPECIALISTS = [ForecastAgent, InventoryAgent, SupplyAgent,
                   CapacityAgent, ScheduleAgent, ExceptionAgent]

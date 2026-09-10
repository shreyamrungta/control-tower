"""
MULTI-AGENT LAYER - query agent and coordinator
==============================================================================
QueryAgent   answers plain-English questions against the engine's result tables
Coordinator  runs the whole crew, synthesises a management briefing, and ranks
             the proposed actions so a planner sees the highest-leverage move
             first

The coordinator is what makes this a multi-agent system rather than six
independent scripts: it runs the specialists, lets them all write to a shared
blackboard, then reasons over the combined picture - including spotting when
two agents' findings point at the same underlying cause.
"""

import difflib
import re

import pandas as pd

from .base import Agent, Blackboard
from .scenario_agent import ScenarioAgent, build_vocabulary, _find_entity
from .specialists import ALL_SPECIALISTS


# ===========================================================================
class QueryAgent(Agent):
    name = "QueryAgent"
    role = "Answers questions about the plan from the engine's result tables"

    def observe(self):
        # the question vocabulary is this dataset's own item and work centre names
        self.observations["vocab"] = build_vocabulary(self.bb.table("data"))

    def ask(self, question):
        """Return {answer, table, source} for a plain-English question."""
        q = question.lower().strip()
        r = self.bb.result

        items_vocab, wc_vocab, _sup_vocab = self.observations.get(
            "vocab") or build_vocabulary(r["data"])
        item = _find_entity(q, items_vocab)
        wc = _find_entity(q, wc_vocab)
        # also allow a raw item code typed exactly, tolerating spacing and
        # hyphenation differences ("wheel 26", "WHEEL-26", "wheel26")
        if not item:
            squashed = re.sub(r"[\s\-_]", "", q)
            for code in r["data"]["inventory"]["Item"]:
                if code.lower() in q or re.sub(r"[\s\-_]", "", code.lower()) in squashed:
                    item = code
                    break

        # ---- bottleneck ---------------------------------------------------
        if "bottleneck" in q or "constraint" in q:
            wcr = r.get("work_centre_results")
            plan = r.get("capacity_plan")
            if wcr is not None and not wcr.empty:
                b = wcr[wcr["Bottleneck"]].iloc[0]
                rccp = (plan.groupby("WorkCentre")["Utilisation"].mean().idxmax()
                        if plan is not None and not plan.empty else "-")
                answer = (
                    f"{b['WorkCentre']} ({b['Description']}) is the bottleneck. In the "
                    f"shop-floor simulation it runs at {b['Utilisation']*100:.0f}% "
                    f"utilisation across {b['Machines']} machine(s), handling "
                    f"{int(b['Operations'])} operations with {b['SetupHours']:.0f} hours "
                    f"lost to setup. Rough-cut capacity planning independently points to "
                    f"{rccp} as the busiest work centre.")
                return {"answer": answer, "table": wcr, "source": "work_centre_results"}

        # ---- on hold / blocked --------------------------------------------
        if "on hold" in q or "blocked" in q or "held" in q or "why" in q and "hold" in q:
            po = r.get("production_orders")
            if po is not None and not po.empty:
                held = po[po["Status"] == "On Hold"]
                if item:
                    held = held[held["Item"] == item]
                by_reason = held["ConstraintType"].value_counts().to_dict()
                answer = (
                    f"{len(held)} production order(s) are on hold"
                    + (f" for {item}" if item else "")
                    + ". Reasons: "
                    + ", ".join(f"{k} ({v})" for k, v in by_reason.items()) + ".")
                return {"answer": answer, "table": held, "source": "production_orders"}

        # ---- forecast -----------------------------------------------------
        if "forecast" in q or "demand" in q or "mape" in q or "model" in q:
            sel = r.get("forecast_selection")
            fc = r.get("forecast")
            if sel is not None and not sel.empty:
                if item:
                    row = sel[sel["Item"] == item]
                    if not row.empty:
                        row = row.iloc[0]
                        series = fc[fc["Item"] == item]
                        answer = (
                            f"{item} is forecast with {row['SelectedMethod']} "
                            f"({row['Parameters']}), chosen as the lowest-MAPE model at "
                            f"{row['MAPE']}%. MAD is {row['MAD']}, RMSE {row['RMSE']}, "
                            f"bias {row['Bias']}, tracking signal {row['TrackingSignal']}. "
                            f"Forecast demand over the horizon totals "
                            f"{series['Forecast'].sum():,.0f} units, averaging "
                            f"{series['Forecast'].mean():.0f} per period. "
                            f"Status: {row['Alert']}.")
                        return {"answer": answer, "table": series, "source": "forecast"}
                answer = ("Selected models: " +
                          "; ".join(f"{r_['Item']} -> {r_['SelectedMethod']} "
                                    f"(MAPE {r_['MAPE']}%)" for _, r_ in sel.iterrows())
                          + f". Average MAPE {sel['MAPE'].mean():.1f}%.")
                return {"answer": answer, "table": sel, "source": "forecast_selection"}

        # ---- where used / BOM (checked before MRP: "where is X used" is a
        #      structure question, not a stock question) ---------------------
        wants_structure = ("where used" in q or "used in" in q or "bom" in q
                           or "component" in q or "goes into" in q
                           or "structure" in q or "made of" in q
                           or ("where" in q and "used" in q)
                           or ("what" in q and "used in" in q))
        if item and wants_structure:
            tree = r.get("bom_tree")
            if tree is not None:
                wu = tree.where_used(item)
                if not wu.empty:
                    answer = (f"{item} is used in " +
                              ", ".join(f"{r_['Parent']} ({r_['QtyPer']:g} per unit)"
                                        for _, r_ in wu.iterrows()) +
                              f". Its low-level code is {tree.low_level_code.get(item)}, "
                              f"so MRP nets it after every one of those parents has been "
                              f"planned.")
                    return {"answer": answer, "table": wu, "source": "bom_tree"}
                answer = (f"{item} is a top-level item - nothing consumes it. "
                          f"Its own structure is:")
                return {"answer": answer, "table": tree.indented_bom(item),
                        "source": "bom_tree"}

        # ---- MRP for an item ----------------------------------------------
        if item and ("mrp" in q or "requirement" in q or "planned order" in q
                     or "stock" in q or "inventory" in q or "when" in q
                     or "position" in q or "on hand" in q):
            mrp = r.get("mrp")
            if mrp is not None and not mrp.empty:
                g = mrp[mrp["Item"] == item]
                if not g.empty:
                    rel = g[g["PlannedOrderRelease"] > 0]
                    inv = r["data"]["inventory"].set_index("Item").loc[item]
                    answer = (
                        f"{item} ({inv['Description']}): on hand {inv['OnHand']:,.0f}, "
                        f"safety stock {inv['SafetyStock']:,.0f}, lead time "
                        f"{int(inv['LeadTime'])} period(s), lot rule "
                        f"{inv['LotSizeRule']}. Gross requirements over the horizon total "
                        f"{g['GrossRequirement'].sum():,.0f} units. "
                        + (f"{len(rel)} planned order release(s) in period(s) "
                           f"{', '.join(str(int(p)) for p in rel['Period'])}, totalling "
                           f"{rel['PlannedOrderRelease'].sum():,.0f} units."
                           if not rel.empty else "No planned order releases."))
                    return {"answer": answer, "table": g, "source": "mrp"}

        # ---- dispatching rules --------------------------------------------
        if "rule" in q or "dispatch" in q or "sequenc" in q or "spt" in q or "edd" in q:
            cmp = r.get("rule_comparison")
            best = r.get("best_by_criterion")
            if cmp is not None and not cmp.empty:
                sel = r.get("selected_rule")
                answer = (
                    f"{len(cmp)} dispatching rules were simulated on the same "
                    f"{int(cmp.iloc[0]['Jobs'])} jobs. {sel} is currently selected "
                    f"({cmp.loc[sel, 'OnTimeDeliveryPct']:.1f}% on-time, "
                    f"{cmp.loc[sel, 'AvgFlowTime']:.1f} h average flow time). "
                    f"{cmp['AvgFlowTime'].idxmin()} gives the shortest flow time and "
                    f"{cmp['OnTimeDeliveryPct'].idxmax()} the best on-time delivery.")
                return {"answer": answer, "table": best, "source": "rule_comparison"}

        # ---- capacity / utilisation ---------------------------------------
        if wc or "capacity" in q or "utilisation" in q or "utilization" in q:
            plan = r.get("capacity_plan")
            if plan is not None and not plan.empty:
                if wc:
                    g = plan[plan["WorkCentre"] == wc]
                    cap = r["data"]["capacity"].set_index("WorkCentre").loc[wc]
                    over = g[g["Overloaded"]]
                    answer = (
                        f"{wc} ({cap['Description']}) offers "
                        f"{cap['AvailableHoursPerPeriod']:.0f} hours per period from "
                        f"{int(cap['NumMachines'])} machine(s) on "
                        f"{int(cap['ShiftsPerDay'])} shift(s) at "
                        f"{cap['Efficiency']*100:.0f}% efficiency. Average utilisation "
                        f"{g['Utilisation'].mean()*100:.0f}%, peak "
                        f"{g['Utilisation'].max()*100:.0f}%. "
                        + (f"Overloaded in {len(over)} period(s)." if not over.empty
                           else "Never overloaded."))
                    return {"answer": answer, "table": g, "source": "capacity_plan"}
                summary = (plan.groupby("WorkCentre")
                           .agg(AvgUtil=("Utilisation", "mean"),
                                PeakUtil=("Utilisation", "max"),
                                Overloads=("Overloaded", "sum")).round(3))
                return {"answer": "Capacity utilisation by work centre:",
                        "table": summary.reset_index(), "source": "capacity_plan"}

        # ---- exceptions ---------------------------------------------------
        if "exception" in q or "problem" in q or "issue" in q or "risk" in q:
            exc = r.get("exceptions")
            if exc is not None and not exc.empty:
                if item:
                    exc = exc[exc["Item"] == item]
                counts = exc["Severity"].value_counts().to_dict()
                answer = (f"{len(exc)} exception(s)"
                          + (f" for {item}" if item else "") + ": "
                          + ", ".join(f"{k} {v}" for k, v in counts.items()) + ".")
                return {"answer": answer, "table": exc, "source": "exceptions"}

        # ---- KPI catch-all -------------------------------------------------
        if any(w in q for w in ("kpi", "performance", "how is", "status", "summary",
                                "health", "overall")):
            k = r["kpis"]
            answer = (
                f"Plan health {r['health_score']}/100. Forecast MAPE "
                f"{k.get('forecast_avg_mape')}%. MPS "
                f"{'feasible' if k.get('mps_feasible') else 'INFEASIBLE'} after "
                f"{k.get('mps_iterations')} iteration(s), peak capacity "
                f"{k.get('rccp_peak_utilisation')}%. {k.get('orders_released')} orders "
                f"released, {k.get('orders_on_hold')} on hold. Schedule: "
                f"{k.get('sched_rule')}, {k.get('sched_otd')}% on time, makespan "
                f"{k.get('sched_makespan')} h, bottleneck {k.get('sched_bottleneck')}. "
                f"{k.get('exceptions_total')} open exception(s).")
            return {"answer": answer, "table": None, "source": "kpis"}

        return {
            "answer": ("I can answer questions about the forecast, inventory and MRP "
                       "position of any item, the BOM and where-used structure, work "
                       "centre capacity and the bottleneck, held orders and why they "
                       "are held, dispatching rule performance, and the exception "
                       "register. Try naming an item or a work centre."),
            "table": None, "source": None,
        }


# ===========================================================================
class Coordinator:
    """Runs the crew and synthesises what they collectively found."""

    def __init__(self, result):
        self.bb = Blackboard(result)
        self.agents = [cls(self.bb) for cls in ALL_SPECIALISTS]
        self.scenario_agent = ScenarioAgent(self.bb)
        self.query_agent = QueryAgent(self.bb)
        self.query_agent.observe()

    # -- running the crew --------------------------------------------------
    def run(self):
        for agent in self.agents:
            findings, actions = agent.run()
            self.bb.send(agent.name, "Coordinator",
                         f"{len(findings)} finding(s), {len(actions)} proposed action(s)")
        return self.bb

    # -- outputs ------------------------------------------------------------
    def findings_frame(self):
        return pd.DataFrame([
            {"Severity": f.severity, "Agent": f.agent, "Module": f.module,
             "Item": f.item, "Finding": f.title, "Detail": f.detail}
            for f in sorted(self.bb.findings, key=lambda x: x.rank)
        ])

    def actions_frame(self):
        return pd.DataFrame([
            {"Priority": i + 1, "Agent": a.agent, "Action": a.label,
             "Rationale": a.rationale, "ExpectedEffect": a.expected_effect,
             "Confidence": a.confidence, "RerunFrom": a.rerun_from,
             "Overrides": a.overrides}
            for i, a in enumerate(self._ranked_actions())
        ])

    def _ranked_actions(self):
        """Rank actions by the severity of the findings that motivated them."""
        sev_of_agent = {}
        for f in self.bb.findings:
            sev_of_agent[f.agent] = min(sev_of_agent.get(f.agent, 9), f.rank)
        conf = {"High": 0, "Medium": 1, "Low": 2}
        return sorted(self.bb.actions,
                      key=lambda a: (sev_of_agent.get(a.agent, 9),
                                     conf.get(a.confidence, 3), a.label))

    def briefing(self):
        """A short written management briefing synthesising every agent's view."""
        r = self.bb.result
        k = r["kpis"]
        lines = []

        # ---- headline -----------------------------------------------------
        lines.append(
            f"**Plan health {r['health_score']}/100.** The master schedule is "
            f"{'feasible' if k.get('mps_feasible') else 'NOT feasible'} after "
            f"{k.get('mps_iterations')} level-loading iteration(s), with "
            f"{k.get('orders_released')} production orders released, "
            f"{k.get('orders_on_hold')} held, and {k.get('exceptions_total')} open "
            f"exception(s) of which {k.get('exceptions_critical')} are critical.")

        # ---- what the crew agrees on --------------------------------------
        critical = self.bb.findings_by(severity="Critical")
        high = self.bb.findings_by(severity="High")

        if critical:
            lines.append("\n**Critical findings**")
            for f in critical[:5]:
                lines.append(f"- *{f.title}* ({f.agent}) - {f.detail}")
        if high:
            lines.append("\n**Also needs attention**")
            for f in high[:4]:
                lines.append(f"- *{f.title}* ({f.agent}) - {f.detail}")

        # ---- convergence: two agents pointing at the same thing -----------
        by_item = {}
        for f in self.bb.findings:
            if f.item and f.rank <= 2:
                by_item.setdefault(f.item, set()).add(f.agent)
        agreed = {i: a for i, a in by_item.items() if len(a) > 1}
        if agreed:
            lines.append("\n**Corroborated across agents**")
            for item, agents in list(agreed.items())[:3]:
                lines.append(
                    f"- {item} was independently flagged by {len(agents)} agents "
                    f"({', '.join(sorted(agents))}), which raises confidence that it is "
                    f"a real constraint rather than an artefact of one calculation.")

        # ---- recommended sequence -----------------------------------------
        actions = self._ranked_actions()
        if actions:
            lines.append("\n**Recommended actions, highest leverage first**")
            for i, a in enumerate(actions[:5], 1):
                lines.append(
                    f"{i}. **{a.label}** ({a.confidence} confidence) - {a.rationale} "
                    f"Expected effect: {a.expected_effect or 'see simulation'}.")
            lines.append(
                "\nEach action is executable: approving it re-runs the pipeline from "
                f"the affected block and re-plans the factory.")
        return "\n".join(lines)

    # -- delegation ---------------------------------------------------------
    def ask(self, question):
        return self.query_agent.ask(question)

    def what_if(self, text, schedule_window=None):
        return self.scenario_agent.run_request(text, schedule_window)

    def run_scenario(self, name, schedule_window=None):
        return self.scenario_agent.run_named_scenario(name, schedule_window)

    def conversation(self):
        return pd.DataFrame([
            {"Time": m.timestamp, "From": m.sender, "To": m.recipient,
             "Message": m.content}
            for m in self.bb.messages
        ])

    def roster(self):
        rows = [{"Agent": a.name, "Role": a.role,
                 "Findings": len([f for f in self.bb.findings if f.agent == a.name]),
                 "Actions": len([x for x in self.bb.actions if x.agent == a.name])}
                for a in self.agents]
        rows.append({"Agent": self.scenario_agent.name, "Role": self.scenario_agent.role,
                     "Findings": 0, "Actions": 0})
        rows.append({"Agent": self.query_agent.name, "Role": self.query_agent.role,
                     "Findings": 0, "Actions": 0})
        return pd.DataFrame(rows)

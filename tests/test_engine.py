"""
ENGINE TEST SUITE
==============================================================================
These tests check that the PLANNING MATHEMATICS is right, not merely that the
code runs. Each one asserts an identity that must hold in any correct MRP II
system, so a regression in the arithmetic fails the build.

    python -m unittest discover -s tests -v
"""

import math
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import io_utils
from engine.bom import build_bom_tree
from engine.forecasting import (accuracy_metrics, holt_linear, moving_average,
                                run_forecasting, single_exponential_smoothing,
                                weighted_moving_average)
from engine.inventory import run_inventory_planning
from engine.lotsizing import apply_lot_size
from engine.mps import run_mps
from engine.mrp import run_mrp
from engine.order_release import build_job_list, run_order_release
from engine.pipeline import run_pipeline
from engine.scenarios import apply_data_overrides, build_scenarios
from engine.scheduling import simulate, run_all_rules

TOL = 0.01


class SharedPlan(unittest.TestCase):
    """One pipeline run shared by every test that needs a full plan."""

    @classmethod
    def setUpClass(cls):
        cls.data = io_utils.load_all()
        cls.R = run_pipeline(data=cls.data)


# ===========================================================================
class TestLotSizing(unittest.TestCase):

    def test_no_order_when_nothing_needed(self):
        for rule in ("LFL", "FOQ", "EOQ"):
            self.assertEqual(apply_lot_size(0, rule, 100, 250), 0.0)
            self.assertEqual(apply_lot_size(-50, rule, 100, 250), 0.0)

    def test_lot_for_lot_orders_exactly_the_requirement(self):
        self.assertAlmostEqual(apply_lot_size(137.5, "LFL", 0), 137.5)

    def test_foq_rounds_up_to_whole_multiples(self):
        self.assertEqual(apply_lot_size(1, "FOQ", 500), 500)
        self.assertEqual(apply_lot_size(500, "FOQ", 500), 500)
        self.assertEqual(apply_lot_size(501, "FOQ", 500), 1000)
        self.assertEqual(apply_lot_size(1400, "FOQ", 500), 1500)

    def test_eoq_rounds_up_to_whole_multiples(self):
        self.assertEqual(apply_lot_size(300, "EOQ", 0, eoq=250), 500)

    def test_minimum_order_quantity_is_respected(self):
        self.assertEqual(apply_lot_size(10, "LFL", 0, min_order_qty=200), 200)


# ===========================================================================
class TestForecasting(unittest.TestCase):

    def test_moving_average_matches_hand_calculation(self):
        y = [10, 20, 30, 40, 50]
        fitted, horizon = moving_average(y, 3)
        self.assertTrue(np.isnan(fitted[0]) and np.isnan(fitted[2]))
        self.assertAlmostEqual(fitted[3], (10 + 20 + 30) / 3)     # forecast for period 4
        self.assertAlmostEqual(fitted[4], (20 + 30 + 40) / 3)
        self.assertAlmostEqual(horizon[0], (30 + 40 + 50) / 3)

    def test_weighted_moving_average_weights_the_recent_period_most(self):
        y = [10, 20, 30, 40]
        fitted, horizon = weighted_moving_average(y, (0.5, 0.3, 0.2))
        # forecast for period 4 = 0.5*30 + 0.3*20 + 0.2*10
        self.assertAlmostEqual(fitted[3], 0.5 * 30 + 0.3 * 20 + 0.2 * 10)
        self.assertAlmostEqual(horizon[0], 0.5 * 40 + 0.3 * 30 + 0.2 * 20)

    def test_ses_with_alpha_one_is_the_naive_forecast(self):
        y = [12, 19, 7, 25, 30]
        fitted, horizon = single_exponential_smoothing(y, alpha=1.0)
        for t in range(1, len(y)):
            self.assertAlmostEqual(fitted[t], y[t - 1], places=6)
        self.assertAlmostEqual(horizon[0], y[-1], places=6)

    def test_holt_extrapolates_a_perfect_linear_trend(self):
        y = list(range(10, 110, 10))            # exactly +10 per period
        fitted, horizon = holt_linear(y, alpha=0.8, beta=0.8)
        self.assertAlmostEqual(horizon[0], 110, delta=1.0)
        self.assertAlmostEqual(horizon[1], 120, delta=2.0)

    def test_accuracy_metrics_match_hand_calculation(self):
        actual = np.array([100.0, 100.0, 100.0])
        fitted = np.array([np.nan, 90.0, 120.0])
        m = accuracy_metrics(actual, fitted)
        self.assertEqual(m["N"], 2)
        self.assertAlmostEqual(m["MAD"], (10 + 20) / 2)
        self.assertAlmostEqual(m["MSE"], (100 + 400) / 2)
        self.assertAlmostEqual(m["RMSE"], math.sqrt(250), places=2)
        self.assertAlmostEqual(m["MAPE"], (0.10 + 0.20) / 2 * 100)
        self.assertAlmostEqual(m["Bias"], (10 - 20) / 2)

    def test_perfect_forecast_scores_zero_error(self):
        y = np.array([5.0, 5.0, 5.0, 5.0])
        m = accuracy_metrics(y, y)
        self.assertEqual(m["MAD"], 0)
        self.assertEqual(m["MAPE"], 0)

    def test_a_model_is_selected_for_every_product(self):
        data = io_utils.load_all()
        fc, acc, sel, fit = run_forecasting(data["demand"])
        self.assertEqual(len(sel), data["demand"]["Item"].nunique())
        self.assertTrue((fc["Forecast"] >= 0).all(), "forecasts must be non-negative")
        # the selected model must be the lowest-MAPE model available
        for _, r in sel.iterrows():
            best = acc[acc["Item"] == r["Item"]]["MAPE"].min()
            self.assertAlmostEqual(r["MAPE"], best, places=6)


# ===========================================================================
class TestBOM(SharedPlan):

    def test_bom_is_acyclic_and_levels_are_consistent(self):
        tree = self.R["bom_tree"]
        for parent, kids in tree.children.items():
            for child, _, _ in kids:
                self.assertGreater(
                    tree.low_level_code[child], tree.low_level_code[parent],
                    f"{child} must sit below its parent {parent}")

    def test_low_level_code_is_the_deepest_appearance(self):
        tree = self.R["bom_tree"]
        # SPOKE is used by three wheels, all at level 1, so it must be level 2
        self.assertEqual(tree.low_level_code["SPOKE"], 2)
        self.assertEqual(tree.low_level_code["ALU-TUBE-STK"], 3)
        for item in ("BIKE-MTB", "BIKE-ROAD", "BIKE-KIDS"):
            self.assertEqual(tree.low_level_code[item], 0)

    def test_quantity_per_compounds_scrap_at_every_level(self):
        tree = self.R["bom_tree"]
        ind = tree.indented_bom("BIKE-MTB")
        spoke = ind[ind["Item"] == "SPOKE"]["QtyPer"].iloc[0]
        # 2 wheels/bike at 1% assembly scrap, 32 spokes/wheel at 3% spoke scrap
        expected = (2 / 0.99) * (32 / 0.97)
        self.assertAlmostEqual(spoke, expected, places=3)

    def test_explosion_conserves_quantity(self):
        """A component's gross requirement must equal the sum over its parents.

        The gross-requirements report rounds to 2 decimal places for legibility -
        it is a report, not a ledger, and Block 7 re-explodes at full precision -
        so the tolerance here is that rounding unit, not machine epsilon.
        """
        tree = self.R["bom_tree"]
        sched = pd.DataFrame([{"Item": "BIKE-MTB", "Period": 1, "Qty": 100}])
        expl = tree.explode_gross_requirements(sched)
        got = {r["Item"]: r["GrossRequirement"] for _, r in expl.iterrows()}
        expected_wheel = 100 * 2 / 0.99
        self.assertAlmostEqual(got["WHEEL-26"], expected_wheel, delta=0.01)
        self.assertAlmostEqual(got["SPOKE"], expected_wheel * 32 / 0.97, delta=0.01)


# ===========================================================================
class TestMPS(SharedPlan):

    def test_pab_recursion_holds_for_every_row(self):
        """PAB(t) = PAB(t-1) + MPS(t) - gross demand(t)."""
        for item, g in self.R["mps"].groupby("Item"):
            g = g.sort_values("Period")
            for _, r in g.iterrows():
                expected = r["OpeningPAB"] + r["MPSQty"] - r["GrossDemand"]
                self.assertAlmostEqual(
                    r["PAB"], expected, delta=TOL,
                    msg=f"{item} period {r['Period']}: PAB identity violated")

    def test_opening_pab_chains_from_the_previous_period(self):
        inv = self.R["data"]["inventory"].set_index("Item")
        for item, g in self.R["mps"].groupby("Item"):
            g = g.sort_values("Period").reset_index(drop=True)
            self.assertAlmostEqual(g.loc[0, "OpeningPAB"],
                                   float(inv.loc[item, "OnHand"]), delta=TOL)
            for i in range(1, len(g)):
                self.assertAlmostEqual(g.loc[i, "OpeningPAB"], g.loc[i - 1, "PAB"],
                                       delta=TOL)

    def test_gross_demand_is_the_greater_of_forecast_and_orders(self):
        for _, r in self.R["mps"].iterrows():
            self.assertAlmostEqual(r["GrossDemand"],
                                   max(r["Forecast"], r["CustomerOrders"]), delta=TOL)

    def test_atp_is_never_negative_after_the_look_back_correction(self):
        """A negative ATP means earlier stock could not cover a later order.
        With this data set every shortfall is coverable, so none should remain."""
        self.assertTrue((self.R["mps"]["ATP"] >= -TOL).all(),
                        "ATP look-back correction left a negative value")

    def test_atp_never_exceeds_total_supply(self):
        for item, g in self.R["mps"].groupby("Item"):
            supply = g["MPSQty"].sum() + g.sort_values("Period").iloc[0]["OpeningPAB"]
            self.assertLessEqual(g["ATP"].sum(), supply + TOL)

    def test_feasible_plan_has_no_capacity_overload(self):
        if self.R["mps_feasible"]:
            self.assertEqual(int(self.R["capacity_plan"]["Overloaded"].sum()), 0)

    def test_revision_loop_never_makes_things_worse(self):
        h = self.R["mps_history"]
        self.assertLessEqual(h.iloc[-1]["CapacityOverloads"],
                             h.iloc[0]["CapacityOverloads"],
                             "level-loading increased the number of overloads")


# ===========================================================================
class TestMRP(SharedPlan):

    def test_netting_identity_holds_for_every_row(self):
        """Projected available = previous + SR + PORcpt - GR."""
        inv = self.R["data"]["inventory"].set_index("Item")
        for item, g in self.R["mrp"].groupby("Item"):
            g = g.sort_values("Period").reset_index(drop=True)
            prev = float(inv.loc[item, "OnHand"])
            for _, r in g.iterrows():
                expected = (prev + r["ScheduledReceipt"]
                            + r["PlannedOrderReceipt"] - r["GrossRequirement"])
                self.assertAlmostEqual(
                    r["ProjectedAvailable"], expected, delta=TOL,
                    msg=f"{item} period {int(r['Period'])}: netting identity violated")
                prev = r["ProjectedAvailable"]

    def test_net_requirement_is_the_shortfall_below_safety_stock(self):
        inv = self.R["data"]["inventory"].set_index("Item")
        for item, g in self.R["mrp"].groupby("Item"):
            ss = float(inv.loc[item, "SafetyStock"])
            g = g.sort_values("Period").reset_index(drop=True)
            prev = float(inv.loc[item, "OnHand"])
            for _, r in g.iterrows():
                available = prev + r["ScheduledReceipt"] - r["GrossRequirement"]
                expected_nr = max(0.0, ss - available)
                self.assertAlmostEqual(r["NetRequirement"], expected_nr, delta=TOL)
                prev = r["ProjectedAvailable"]

    def test_planned_receipt_respects_the_lot_size_rule(self):
        inv = self.R["data"]["inventory"].set_index("Item")
        eoq = self.R["inventory_params"].set_index("Item")["EOQ"].to_dict()
        for _, r in self.R["mrp"].iterrows():
            if r["PlannedOrderReceipt"] <= 0:
                continue
            m = inv.loc[r["Item"]]
            expected = apply_lot_size(r["NetRequirement"], m["LotSizeRule"],
                                      float(m["LotSizeValue"]),
                                      float(eoq.get(r["Item"], 0)))
            self.assertAlmostEqual(r["PlannedOrderReceipt"], expected, delta=TOL)

    def test_receipt_always_covers_the_net_requirement(self):
        d = self.R["mrp"]
        bad = d[(d["NetRequirement"] > 0) &
                (d["PlannedOrderReceipt"] < d["NetRequirement"] - TOL)]
        self.assertTrue(bad.empty, f"{len(bad)} row(s) under-ordered")

    def test_releases_are_offset_backwards_by_the_lead_time(self):
        rel = self.R["releases"]
        for _, r in rel.iterrows():
            expected = max(1, int(r["DuePeriod"]) - int(r["LeadTime"]))
            self.assertEqual(int(r["ReleasePeriod"]), expected)
            if int(r["DuePeriod"]) - int(r["LeadTime"]) < 1:
                self.assertTrue(r["LateRelease"],
                                "a clamped release must be flagged as past due")

    def test_release_quantities_reconcile_with_receipts(self):
        for item, g in self.R["mrp"].groupby("Item"):
            self.assertAlmostEqual(g["PlannedOrderRelease"].sum(),
                                   g["PlannedOrderReceipt"].sum(), delta=TOL,
                                   msg=f"{item}: releases do not match receipts")

    def test_dependent_demand_is_driven_by_parent_releases(self):
        """A child's gross requirement in period p = sum over parents of that
        parent's planned release in p, times qty-per adjusted for scrap."""
        tree, mrp = self.R["bom_tree"], self.R["mrp"]
        rel = {(r["Item"], int(r["Period"])): r["PlannedOrderRelease"]
               for _, r in mrp.iterrows()}
        gr = {(r["Item"], int(r["Period"])): r["GrossRequirement"]
              for _, r in mrp.iterrows()}

        for child in tree.all_items:
            parents = tree.parents.get(child)
            if not parents:
                continue                      # independent demand, driven by the MPS
            for p in range(1, self.R["horizon"] + 1):
                expected = 0.0
                for parent, _ in parents:
                    qty = rel.get((parent, p), 0.0)
                    if qty:
                        for c, required in tree.explode(parent, qty):
                            if c == child:
                                expected += required
                self.assertAlmostEqual(
                    gr.get((child, p), 0.0), expected, delta=0.05,
                    msg=f"{child} period {p}: dependent demand does not reconcile")

    def test_items_are_processed_in_low_level_code_order(self):
        tree = self.R["bom_tree"]
        order = tree.items_by_level()
        codes = [tree.low_level_code[i] for i in order]
        self.assertEqual(codes, sorted(codes))


# ===========================================================================
class TestInventory(SharedPlan):

    def test_eoq_matches_the_square_root_formula(self):
        inv = self.R["data"]["inventory"].set_index("Item")
        for _, r in self.R["inventory_params"].iterrows():
            m = inv.loc[r["Item"]]
            D = r["AnnualDemand"]
            H = float(m["HoldingCostRate"]) * float(m["UnitCost"])
            S = float(m["OrderingCost"])
            expected = math.sqrt(2 * D * S / H) if D > 0 and H > 0 else 0
            self.assertAlmostEqual(r["EOQ"], round(expected), delta=1)

    def test_reorder_point_equals_lead_time_demand_plus_safety_stock(self):
        for _, r in self.R["inventory_params"].iterrows():
            expected = r["AvgPeriodDemand"] * r["LeadTime"] + r["SafetyStock_Master"]
            self.assertAlmostEqual(r["ReorderPoint"], round(expected), delta=1)

    def test_projection_balance_chains_correctly(self):
        for item, g in self.R["inventory_projection"].groupby("Item"):
            g = g.sort_values("Period").reset_index(drop=True)
            for _, r in g.iterrows():
                self.assertAlmostEqual(
                    r["Closing"], r["Opening"] + r["Receipts"] - r["Demand"],
                    delta=TOL, msg=f"{item} period {r['Period']}")
            for i in range(1, len(g)):
                self.assertAlmostEqual(g.loc[i, "Opening"], g.loc[i - 1, "Closing"],
                                       delta=TOL)


# ===========================================================================
class TestScheduling(SharedPlan):

    def test_no_machine_runs_two_operations_at_once(self):
        for rule, res in self.R["rule_results"].items():
            g = res["gantt"]
            if g.empty:
                continue
            for machine, ops in g.groupby("Machine"):
                ops = ops.sort_values("Start")
                ends = ops["End"].tolist()
                starts = ops["Start"].tolist()
                for i in range(1, len(ops)):
                    self.assertGreaterEqual(
                        starts[i] + TOL, ends[i - 1],
                        f"{rule}: {machine} double-booked at {starts[i]}")

    def test_operations_respect_the_routing_sequence(self):
        for rule, res in self.R["rule_results"].items():
            g = res["gantt"]
            if g.empty:
                continue
            for job, ops in g.groupby("JobID"):
                ops = ops.sort_values("OpSeq")
                for i in range(1, len(ops)):
                    self.assertGreaterEqual(
                        ops.iloc[i]["Start"] + TOL, ops.iloc[i - 1]["End"],
                        f"{rule}: {job} operation {ops.iloc[i]['OpSeq']} "
                        f"started before its predecessor finished")

    def test_no_job_starts_before_it_is_released(self):
        for rule, res in self.R["rule_results"].items():
            jobs, g = res["jobs"], res["gantt"]
            if g.empty:
                continue
            first_start = g.groupby("JobID")["Start"].min()
            for _, j in jobs.iterrows():
                self.assertGreaterEqual(first_start[j["JobID"]] + TOL, j["Arrival"])

    def test_every_operation_is_scheduled_exactly_once(self):
        n_ops = len(self.R["operations"])
        for rule, res in self.R["rule_results"].items():
            if res["gantt"].empty:
                continue
            self.assertEqual(len(res["gantt"]), n_ops,
                             f"{rule}: scheduled {len(res['gantt'])} of {n_ops} operations")

    def test_kpi_identities(self):
        for rule, res in self.R["rule_results"].items():
            jobs, k = res["jobs"], res["kpis"]
            if jobs.empty:
                continue
            for _, j in jobs.iterrows():
                self.assertAlmostEqual(j["FlowTime"], j["Completion"] - j["Arrival"],
                                       delta=TOL)
                self.assertAlmostEqual(j["Lateness"], j["Completion"] - j["DueHrs"],
                                       delta=TOL)
                self.assertAlmostEqual(j["Tardiness"], max(0.0, j["Lateness"]),
                                       delta=TOL)
            self.assertAlmostEqual(k["Makespan"], res["gantt"]["End"].max(), delta=TOL)
            self.assertAlmostEqual(k["AvgFlowTime"], jobs["FlowTime"].mean(), delta=TOL)
            self.assertEqual(k["TardyJobs"], int((jobs["Tardiness"] > TOL).sum()))
            self.assertAlmostEqual(
                k["OnTimeDeliveryPct"],
                100.0 * jobs["OnTime"].sum() / len(jobs), delta=0.1)

    def test_every_rule_schedules_the_same_work(self):
        """Rules change the SEQUENCE, never the total work content."""
        totals = {r: res["gantt"]["ProcessHrs"].sum()
                  for r, res in self.R["rule_results"].items()
                  if not res["gantt"].empty}
        values = list(totals.values())
        for v in values[1:]:
            self.assertAlmostEqual(v, values[0], delta=TOL,
                                   msg=f"total processing time differs by rule: {totals}")

    def test_spt_sequences_shorter_operations_earlier_than_lpt(self):
        """A behavioural check that the dispatching rules actually differ."""
        res = self.R["rule_results"]
        if "SPT" not in res or "LPT" not in res or res["SPT"]["gantt"].empty:
            self.skipTest("no schedule to compare")
        spt = res["SPT"]["jobs"]["FlowTime"].mean()
        lpt = res["LPT"]["jobs"]["FlowTime"].mean()
        self.assertLessEqual(spt, lpt + TOL,
                             "SPT should not have a longer average flow time than LPT")

    def test_utilisation_is_within_bounds(self):
        for rule, res in self.R["rule_results"].items():
            if res["wc"].empty:
                continue
            self.assertTrue((res["wc"]["Utilisation"] >= -TOL).all())
            self.assertTrue((res["wc"]["Utilisation"] <= 1 + TOL).all(),
                            f"{rule}: utilisation above 100%")


# ===========================================================================
class TestOrderRelease(SharedPlan):

    def test_only_make_items_become_production_orders(self):
        inv = self.R["data"]["inventory"].set_index("Item")
        for _, o in self.R["production_orders"].iterrows():
            self.assertEqual(inv.loc[o["Item"], "SourceType"], "M")

    def test_only_buy_items_become_purchase_requisitions(self):
        inv = self.R["data"]["inventory"].set_index("Item")
        for _, o in self.R["purchase_reqs"].iterrows():
            self.assertEqual(inv.loc[o["Item"], "SourceType"], "P")

    def test_every_release_becomes_an_order_or_a_requisition(self):
        self.assertEqual(
            len(self.R["releases"]),
            len(self.R["production_orders"]) + len(self.R["purchase_reqs"]))

    def test_only_released_orders_reach_the_job_list(self):
        released = set(self.R["production_orders"]
                       [self.R["production_orders"]["Status"] == "Released"]["OrderID"])
        for jid in self.R["jobs"]["JobID"]:
            self.assertIn(jid, released)

    def test_held_orders_always_carry_a_constraint_type(self):
        held = self.R["production_orders"][
            self.R["production_orders"]["Status"] == "On Hold"]
        self.assertTrue((held["ConstraintType"] != "None").all())


# ===========================================================================
class TestScenarios(SharedPlan):

    def test_overrides_never_mutate_the_original_data(self):
        before = self.data["inventory"].copy()
        apply_data_overrides(self.data, {"lead_time": {"DERAILLEUR": 1},
                                         "safety_stock": {"CHAIN": 99}})
        pd.testing.assert_frame_equal(before, self.data["inventory"])

    def test_lead_time_override_is_applied(self):
        d = apply_data_overrides(self.data, {"lead_time": {"DERAILLEUR": 1}})
        self.assertEqual(
            int(d["inventory"].set_index("Item").loc["DERAILLEUR", "LeadTime"]), 1)

    def test_capacity_delta_syntax(self):
        base = int(self.data["capacity"].set_index("WorkCentre").loc["WC03", "NumMachines"])
        d = apply_data_overrides(self.data, {"capacity": {"WC03": {"NumMachines": "+1"}}})
        row = d["capacity"].set_index("WorkCentre").loc["WC03"]
        self.assertEqual(int(row["NumMachines"]), base + 1)
        # derived availability must be recomputed, not left stale
        expected = (row["NumMachines"] * row["HoursPerShift"] * row["ShiftsPerDay"]
                    * row["DaysPerWeek"] * row["Efficiency"])
        self.assertAlmostEqual(row["AvailableHoursPerPeriod"], round(expected, 2), delta=0.01)

    def test_timed_outage_is_not_counted_twice(self):
        """The timed capacity cut belongs to rough-cut planning; the sustained
        derate belongs to the simulation. Applying both to one table would
        double-count the outage."""
        ov = {"capacity": {"WC03": {"periods": [4, 5], "capacity_pct": 0.5}}}
        with_derate = apply_data_overrides(self.data, ov, 12, apply_timed_derate=True)
        without = apply_data_overrides(self.data, ov, 12, apply_timed_derate=False)
        a = with_derate["capacity"].set_index("WorkCentre").loc["WC03", "AvailableHoursPerPeriod"]
        b = without["capacity"].set_index("WorkCentre").loc["WC03", "AvailableHoursPerPeriod"]
        base = self.data["capacity"].set_index("WorkCentre").loc["WC03", "AvailableHoursPerPeriod"]
        self.assertAlmostEqual(b, base, delta=0.01)
        # 2 of 12 periods at 50% -> horizon average factor of 0.9167
        self.assertAlmostEqual(a / base, (10 + 2 * 0.5) / 12, delta=0.001)

    def test_a_capacity_cut_makes_the_plan_no_better(self):
        lib = build_scenarios(self.data, self.R)
        name = next(n for n in lib if n.startswith("Machine Breakdown"))
        res = run_pipeline(data=self.data, overrides=lib[name]["overrides"])
        self.assertLessEqual(res["health_score"], self.R["health_score"] + 0.01)

    def test_adding_capacity_at_the_bottleneck_helps(self):
        res = run_pipeline(data=self.data,
                           overrides={"capacity": {"WC03": {"NumMachines": "+1"}}})
        self.assertLessEqual(res["mps_iterations"], self.R["mps_iterations"],
                             "more capacity should need no more level-loading")

    def test_every_library_scenario_runs(self):
        for name, spec in build_scenarios(self.data, self.R).items():
            with self.subTest(scenario=name):
                res = run_pipeline(data=self.data, overrides=spec["overrides"])
                self.assertIn("kpis", res)
                self.assertGreater(len(res["mrp"]), 0)


# ===========================================================================
class TestPipeline(SharedPlan):

    def test_all_expected_outputs_are_present(self):
        for key in ("forecast", "inventory_params", "mps", "capacity_plan",
                    "gross_requirements", "mrp", "releases", "production_orders",
                    "jobs", "rule_comparison", "exceptions", "kpis"):
            self.assertIn(key, self.R)

    def test_health_score_is_in_range(self):
        self.assertGreaterEqual(self.R["health_score"], 0)
        self.assertLessEqual(self.R["health_score"], 100)

    def test_pipeline_is_deterministic(self):
        again = run_pipeline(data=self.data)
        self.assertEqual(again["health_score"], self.R["health_score"])
        pd.testing.assert_frame_equal(
            again["mrp"].reset_index(drop=True),
            self.R["mrp"].reset_index(drop=True))

    def test_every_exception_has_a_recommended_action(self):
        exc = self.R["exceptions"]
        if exc.empty:
            self.skipTest("no exceptions")
        self.assertTrue(exc["RecommendedAction"].notna().all())
        self.assertTrue((exc["RecommendedAction"].str.len() > 0).all())

    def test_severity_values_are_from_the_allowed_set(self):
        allowed = {"Critical", "High", "Medium", "Low"}
        self.assertTrue(set(self.R["exceptions"]["Severity"]).issubset(allowed))


# ===========================================================================
class TestAgents(SharedPlan):

    def test_agents_produce_findings_without_error(self):
        from agents import Coordinator
        c = Coordinator(self.R)
        c.run()
        self.assertGreater(len(c.bb.findings), 0)
        self.assertFalse(c.findings_frame().empty)

    def test_query_agent_answers_core_questions(self):
        from agents import Coordinator
        c = Coordinator(self.R)
        c.run()
        for q in ("which work centre is the bottleneck?",
                  "why are orders on hold?",
                  "where is SPOKE used?",
                  "how is the plan doing overall?"):
            with self.subTest(question=q):
                a = c.ask(q)
                self.assertIsInstance(a["answer"], str)
                self.assertGreater(len(a["answer"]), 20)
                self.assertNotIn("I can answer questions about", a["answer"],
                                 f"query agent failed to route: {q}")

    def test_scenario_agent_parses_the_documented_phrasings(self):
        """The vocabulary is derived from the data, so these phrases work because
        the dataset describes WC03 as 'Painting & Curing', not because a synonym
        list was hand-written for bicycles."""
        from agents.scenario_agent import build_vocabulary, interpret
        vocab = build_vocabulary(self.data)
        im = self.data["inventory"].set_index("Item").to_dict("index")
        cases = [
            ("what if BIKE-MTB demand rises 40% in weeks 3 to 6", "demand_shock"),
            ("what if SUP-06 slips by 3 weeks", "supplier_delay"),
            ("what if painting runs at 50% in week 4 and 5", "capacity"),
            ("what if we add a machine at painting", "capacity"),
            ("what if there is a rush order of 500 BIKE-KIDS in period 2", "rush_order"),
            ("what if BIKE-ROAD demand collapses by 30%", "demand_multiplier"),
        ]
        for text, key in cases:
            with self.subTest(text=text):
                p = interpret(text, im, vocab)
                self.assertTrue(p["confident"], f"failed to parse: {text}")
                self.assertIn(key, p["overrides"])

    def test_a_description_word_finds_the_work_centre(self):
        """'paint' must reach WC03 because its description says 'Painting'."""
        from agents.scenario_agent import build_vocabulary, interpret
        vocab = build_vocabulary(self.data)
        p = interpret("what if the paint line runs at 50% in week 4",
                      self.data["inventory"].set_index("Item").to_dict("index"), vocab)
        self.assertTrue(p["confident"])
        self.assertAlmostEqual(p["overrides"]["capacity"]["WC03"]["capacity_pct"], 0.5)

    def test_no_vocabulary_is_hard_coded(self):
        """Deleting the curated tables is the point - assert they are gone."""
        import agents.scenario_agent as sa
        for name in ("ITEM_ALIASES", "WORK_CENTRE_ALIASES", "SUPPLIER_ALIASES"):
            self.assertFalse(hasattr(sa, name),
                             f"{name} still exists - vocabulary must come from data")

    def test_unparseable_request_is_reported_not_guessed(self):
        from agents.scenario_agent import interpret
        p = interpret("make the factory better somehow")
        self.assertFalse(p["confident"])
        self.assertEqual(p["overrides"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""
DATA IMPORT / EXPORT TEST SUITE
==============================================================================
Covers the part of the system a user actually touches: bringing their own data.

The properties that matter:
  * a file with UNFAMILIAR column names is mapped automatically
  * anything not supplied is filled with a documented default
  * a file missing an ESSENTIAL field is refused without damaging the dataset
  * a good import really does change the plan, and reset really does undo it
  * the whole pipeline runs on a factory that is nothing like the sample

    python -m unittest tests.test_data_io -v
"""

import io
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import data_io
from engine import io_utils
from engine.pipeline import run_pipeline
from engine.scenarios import build_scenarios


class Upload(io.StringIO):
    """Stands in for a Streamlit upload."""
    def __init__(self, text, name="upload.csv"):
        super().__init__(text)
        self.name = name


def upload(key, csv_text, name="upload.csv", mapping=None):
    return data_io.accept_upload(key, Upload(csv_text, name), mapping)


# ===========================================================================
class TestWorkingCopy(unittest.TestCase):

    def setUp(self):
        data_io.reset_to_sample()

    @classmethod
    def tearDownClass(cls):
        data_io.reset_to_sample()

    def test_working_copy_is_seeded_with_all_nine_files(self):
        for f in data_io.FILES:
            self.assertTrue(
                os.path.exists(os.path.join(data_io.ACTIVE_DIR, f["file"])),
                f"{f['file']} missing from the working copy")

    def test_nothing_is_custom_after_a_reset(self):
        for f in data_io.FILES:
            self.assertFalse(data_io.is_custom(f["key"]))

    def test_fingerprint_changes_only_when_data_changes(self):
        a = data_io.fingerprint()
        self.assertEqual(a, data_io.fingerprint(), "fingerprint is not stable")
        d = data_io.read_active("demand")
        d.loc[0, "Demand"] = int(d.loc[0, "Demand"]) + 1
        self.assertTrue(upload("demand", d.to_csv(index=False))[0])
        self.assertNotEqual(a, data_io.fingerprint())

    def test_blank_template_has_every_field(self):
        for f in data_io.FILES:
            if not f["essential"]:
                continue
            cols = list(data_io.blank_template(f["key"]).columns)
            for field in f["essential"] + list(f["optional"]):
                self.assertIn(field, cols)


# ===========================================================================
class TestColumnMapping(unittest.TestCase):
    """A file whose columns are named nothing like ours must still import."""

    def test_unfamiliar_column_names_are_matched(self):
        cols = ["Part No", "Part Name", "Stock On Hand", "Replenishment Lead Time",
                "Std Cost", "Vendor Code", "Notes"]
        m = data_io.suggest_mapping("inventory", cols)
        self.assertEqual(m["Item"], "Part No")
        self.assertEqual(m["OnHand"], "Stock On Hand")
        self.assertEqual(m["LeadTime"], "Replenishment Lead Time")
        self.assertEqual(m["UnitCost"], "Std Cost")
        self.assertEqual(m["Supplier"], "Vendor Code")
        self.assertEqual(m["Description"], "Part Name")

    def test_a_column_is_never_used_for_two_fields(self):
        m = data_io.suggest_mapping("inventory", ["Item", "OnHand", "LeadTime"])
        used = [v for v in m.values() if v]
        self.assertEqual(len(used), len(set(used)))

    def test_bom_synonyms_are_matched(self):
        m = data_io.suggest_mapping("bom", ["Assembly", "Child Item", "Usage Quantity"])
        self.assertEqual(m["ParentItem"], "Assembly")
        self.assertEqual(m["ComponentItem"], "Child Item")
        self.assertEqual(m["QtyPer"], "Usage Quantity")

    def test_work_centre_synonyms_are_matched(self):
        m = data_io.suggest_mapping("capacity", ["Resource", "No Of Machines", "OEE"])
        self.assertEqual(m["WorkCentre"], "Resource")
        self.assertEqual(m["NumMachines"], "No Of Machines")
        self.assertEqual(m["Efficiency"], "OEE")

    def test_unmatched_optional_fields_come_back_as_none(self):
        m = data_io.suggest_mapping("inventory", ["Item", "OnHand", "LeadTime"])
        self.assertIsNone(m["SafetyStock"])
        self.assertIsNone(m["ScrapPct"])



# ===========================================================================
class TestMessyRealWorldFormats(unittest.TestCase):
    """What an actual ERP or Excel export contains, rather than a tidy CSV."""

    def _import(self, key, csv):
        raw = pd.read_csv(io.StringIO(csv))
        mapping = data_io.suggest_mapping(key, list(raw.columns))
        return data_io.preview_mapping(key, raw, mapping)

    def test_dates_become_numbered_periods(self):
        """A weekly date column is what most systems export."""
        clean, notes, problems = self._import("demand",
            "Item,Date,Qty\n"
            "A,2026-01-05,120\nA,2026-01-12,130\nA,2026-01-19,118\n"
            "B,2026-01-05,44\nB,2026-01-12,51\nB,2026-01-19,49")
        self.assertEqual(problems, [])
        self.assertEqual(list(clean["Period"]), [1, 2, 3, 1, 2, 3],
                         "the same date must map to the same period for every item")
        self.assertTrue(any("Period" in n for n in notes))

    def test_period_labels_are_understood(self):
        for label in ("Week 1", "W01", "P1", "Period 1"):
            with self.subTest(label=label):
                clean, _n, problems = self._import(
                    "demand", f"Item,Period,Demand\nA,{label},100")
                self.assertEqual(problems, [])
                self.assertEqual(int(clean.loc[0, "Period"]), 1)

    def test_thousands_separators_are_stripped(self):
        clean, _n, problems = self._import(
            "demand", 'Item,Period,Demand\nA,1,"1,200"\nA,2,"12,350"')
        self.assertEqual(problems, [])
        self.assertEqual(list(clean["Demand"]), [1200, 12350])

    def test_currency_symbols_are_stripped(self):
        clean, _n, problems = self._import(
            "inventory", "Item,OnHand,LeadTime,UnitCost\nA,100,2,$12.50\nB,50,1,£9")
        self.assertEqual(problems, [])
        self.assertAlmostEqual(clean.loc[0, "UnitCost"], 12.50)
        self.assertAlmostEqual(clean.loc[1, "UnitCost"], 9.0)

    def test_parenthesised_negative_is_read_as_negative(self):
        clean, _n, _p = self._import(
            "inventory", "Item,OnHand,LeadTime\nA,(50),2")
        self.assertEqual(clean.loc[0, "OnHand"], -50)

    def test_whitespace_in_headers_is_tolerated(self):
        raw, err = data_io.read_any(Upload(
            "Item ,  Period,Demand  \nA,1,100\nA,2,110"))
        self.assertIsNone(err)
        m = data_io.suggest_mapping("demand", list(raw.columns))
        self.assertIsNotNone(m["Item"])
        self.assertIsNotNone(m["Demand"])

    def test_blank_rows_are_dropped(self):
        raw, err = data_io.read_any(Upload(
            "Item,Period,Demand\nA,1,100\n,,\nA,2,110"))
        self.assertIsNone(err)
        self.assertEqual(len(raw), 2)

    def test_unicode_and_spaces_in_item_codes(self):
        clean, _n, problems = self._import(
            "inventory", "Item,OnHand,LeadTime\nWidget Alpha,100,2\nBrücke-01,50,1")
        self.assertEqual(problems, [])
        self.assertIn("Brücke-01", list(clean["Item"]))

    def test_percentage_column_written_with_a_sign(self):
        clean, _n, problems = self._import(
            "capacity",
            "WorkCentre,NumMachines,HoursPerShift,ShiftsPerDay,DaysPerWeek,Efficiency\n"
            "WC-A,2,8,2,5,85%")
        self.assertEqual(problems, [])
        self.assertAlmostEqual(clean.loc[0, "Efficiency"], 0.85, places=4)

    def test_excel_file_is_read(self):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as xl:
            pd.DataFrame({"Part No": ["A", "B"], "Stock On Hand": [10, 20],
                          "Lead Time": [1, 2]}).to_excel(xl, sheet_name="Stock",
                                                         index=False)
        buf.seek(0)
        buf.name = "export.xlsx"
        self.assertEqual(data_io.sheet_names(buf), ["Stock"])
        raw, err = data_io.read_any(buf, "Stock")
        self.assertIsNone(err)
        m = data_io.suggest_mapping("inventory", list(raw.columns))
        self.assertEqual(m["Item"], "Part No")
        clean, _n, problems = data_io.preview_mapping("inventory", raw, m)
        self.assertEqual(problems, [])


# ===========================================================================
class TestDefaults(unittest.TestCase):
    """Only the essential fields are required; the rest are filled in."""

    def test_a_minimal_inventory_file_imports(self):
        raw = pd.read_csv(io.StringIO(
            "Item,OnHand,LeadTime\nA,100,2\nB,50,1"))
        m = data_io.suggest_mapping("inventory", list(raw.columns))
        clean, notes, problems = data_io.preview_mapping("inventory", raw, m)
        self.assertEqual(problems, [])
        self.assertEqual(len(clean), 2)
        self.assertEqual(clean.loc[0, "LotSizeRule"], "LFL")
        self.assertEqual(clean.loc[0, "HoldingCostRate"], 0.22)
        self.assertEqual(clean.loc[0, "Description"], "A")
        self.assertGreater(len(notes), 5, "defaults should be reported back")

    def test_a_minimal_capacity_file_imports(self):
        raw = pd.read_csv(io.StringIO("WorkCentre\nWC-A\nWC-B"))
        clean, _n, problems = data_io.preview_mapping(
            "capacity", raw, data_io.suggest_mapping("capacity", list(raw.columns)))
        self.assertEqual(problems, [])
        self.assertAlmostEqual(clean.loc[0, "AvailableHoursPerPeriod"],
                               1 * 8 * 1 * 5 * 0.85, places=2)

    def test_routing_operation_sequence_is_generated(self):
        raw = pd.read_csv(io.StringIO("Item,WorkCentre\nA,WC-A\nA,WC-B\nB,WC-A"))
        clean, _n, problems = data_io.preview_mapping(
            "routing", raw, data_io.suggest_mapping("routing", list(raw.columns)))
        self.assertEqual(problems, [])
        self.assertEqual(list(clean["OpSeq"]), [10, 20, 30])

    def test_a_percentage_is_converted_to_a_fraction(self):
        """85 instead of 0.85 is the commonest import mistake - fix it, don't refuse."""
        raw = pd.read_csv(io.StringIO(
            "WorkCentre,NumMachines,HoursPerShift,ShiftsPerDay,DaysPerWeek,Efficiency\n"
            "WC-A,2,8,2,5,85"))
        clean, notes, problems = data_io.preview_mapping(
            "capacity", raw, data_io.suggest_mapping("capacity", list(raw.columns)))
        self.assertEqual(problems, [])
        self.assertAlmostEqual(clean.loc[0, "Efficiency"], 0.85, places=4)
        self.assertTrue(any("percentage" in n for n in notes))

    def test_extra_columns_are_ignored(self):
        raw = pd.read_csv(io.StringIO(
            "Item,OnHand,LeadTime,SomeInternalCode,Comment\nA,10,1,XYZ,hello"))
        clean, _n, problems = data_io.preview_mapping(
            "inventory", raw, data_io.suggest_mapping("inventory", list(raw.columns)))
        self.assertEqual(problems, [])
        self.assertNotIn("SomeInternalCode", clean.columns)


# ===========================================================================
class TestRejection(unittest.TestCase):
    """A file missing an ESSENTIAL field is refused, and nothing is damaged."""

    def setUp(self):
        data_io.reset_to_sample()

    @classmethod
    def tearDownClass(cls):
        data_io.reset_to_sample()

    def _reject(self, key, csv):
        ok, msg = upload(key, csv)
        self.assertFalse(ok, "this should have been rejected")
        self.assertFalse(data_io.is_custom(key),
                         "a rejected file must not overwrite the working copy")
        return msg

    def test_missing_essential_field_is_rejected(self):
        msg = self._reject("demand", "Period,Item\n1,A\n2,A")
        self.assertIn("Demand", msg)

    def test_blank_essential_values_are_rejected(self):
        msg = self._reject("demand", "Period,Item,Demand\n1,A,10\n2,A,")
        self.assertIn("Demand", msg)

    def test_non_numeric_essential_value_is_rejected(self):
        msg = self._reject("demand", "Period,Item,Demand\n1,A,10\n2,A,abc")
        self.assertIn("Demand", msg)

    def test_unparseable_file_is_rejected(self):
        ok, _msg = upload("demand", "\x00\x01 not a csv at all \x02")
        self.assertFalse(ok)

    def test_empty_file_is_rejected(self):
        ok, msg = upload("demand", "Period,Item,Demand\n")
        self.assertFalse(ok)
        self.assertIn("no rows", msg.lower())

    def test_bad_source_type_is_rejected(self):
        msg = self._reject(
            "inventory", "Item,OnHand,LeadTime,SourceType\nA,10,1,Z")
        self.assertIn("SourceType", msg)

    def test_duplicate_item_codes_are_rejected(self):
        msg = self._reject("inventory", "Item,OnHand,LeadTime\nA,10,1\nA,20,2")
        self.assertIn("duplicate", msg.lower())

    def test_self_referencing_bom_is_rejected(self):
        msg = self._reject("bom", "ParentItem,ComponentItem,QtyPer\nA,A,1")
        self.assertIn("own component", msg)

    def test_a_run_of_rejections_leaves_the_dataset_usable(self):
        for key, csv in [("demand", "Period,Item\n1,A"),
                         ("bom", "ParentItem,ComponentItem,QtyPer\nA,A,1"),
                         ("inventory", "Item,OnHand,LeadTime\nA,10,1\nA,20,2")]:
            upload(key, csv)
        data = data_io.load_plan_data()
        self.assertEqual(data_io.cross_check(data), [])
        self.assertGreater(run_pipeline(data=data)["health_score"], 0)


# ===========================================================================
class TestUploadRoundTrip(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        data_io.reset_to_sample()
        cls.baseline = run_pipeline(data=data_io.load_plan_data())

    def setUp(self):
        data_io.reset_to_sample()

    @classmethod
    def tearDownClass(cls):
        data_io.reset_to_sample()

    def test_a_valid_upload_is_accepted(self):
        d = data_io.read_active("demand")
        ok, msg = upload("demand", d.to_csv(index=False))
        self.assertTrue(ok, msg)
        self.assertIn("108", msg)

    def test_a_valid_upload_changes_the_plan(self):
        d = data_io.read_active("demand").copy()
        mask = d["Item"] == "BIKE-MTB"
        d.loc[mask, "Demand"] = (d.loc[mask, "Demand"] * 1.5).round().astype(int)
        self.assertTrue(upload("demand", d.to_csv(index=False))[0])
        self.assertTrue(data_io.is_custom("demand"))

        new = run_pipeline(data=data_io.load_plan_data())
        self.assertNotEqual(new["kpis"]["mps_total_units"],
                            self.baseline["kpis"]["mps_total_units"])

    def test_reset_restores_the_baseline_exactly(self):
        d = data_io.read_active("demand").copy()
        d["Demand"] = d["Demand"] * 2
        upload("demand", d.to_csv(index=False))
        data_io.reset_to_sample()

        back = run_pipeline(data=data_io.load_plan_data())
        self.assertEqual(back["kpis"]["mps_total_units"],
                         self.baseline["kpis"]["mps_total_units"])
        self.assertEqual(back["health_score"], self.baseline["health_score"])

    def test_only_the_uploaded_file_is_replaced(self):
        d = data_io.read_active("demand").copy()
        d.loc[0, "Demand"] = int(d.loc[0, "Demand"]) + 7
        upload("demand", d.to_csv(index=False))
        self.assertTrue(data_io.is_custom("demand"))
        for other in ("bom", "inventory", "routing", "capacity"):
            self.assertFalse(data_io.is_custom(other),
                             f"{other} changed when only demand was uploaded")


# ===========================================================================
class TestDerivation(unittest.TestCase):
    """Fields that can be worked out from the other files are worked out."""

    def test_source_type_is_derived_from_the_bom(self):
        data = {
            "inventory": pd.DataFrame([
                {"Item": "TOP", "OnHand": 1, "LeadTime": 1, "SourceType": "auto",
                 "BOMLevel": 0, "UnitCost": 1.0},
                {"Item": "PART", "OnHand": 1, "LeadTime": 1, "SourceType": "auto",
                 "BOMLevel": 0, "UnitCost": 1.0}]),
            "bom": pd.DataFrame([{"ParentItem": "TOP", "ComponentItem": "PART",
                                  "QtyPer": 2, "ScrapPct": 0}]),
        }
        out = data_io.finalise(data)["inventory"].set_index("Item")
        self.assertEqual(out.loc["TOP", "SourceType"], "M", "a parent must be 'make'")
        self.assertEqual(out.loc["PART", "SourceType"], "P", "a leaf must be 'buy'")

    def test_bom_level_is_derived_from_the_structure(self):
        data = {
            "inventory": pd.DataFrame([
                {"Item": i, "OnHand": 1, "LeadTime": 1, "SourceType": "auto",
                 "BOMLevel": 0, "UnitCost": 1.0} for i in ("TOP", "MID", "LEAF")]),
            "bom": pd.DataFrame([
                {"ParentItem": "TOP", "ComponentItem": "MID", "QtyPer": 1, "ScrapPct": 0},
                {"ParentItem": "MID", "ComponentItem": "LEAF", "QtyPer": 1, "ScrapPct": 0}]),
        }
        out = data_io.finalise(data)["inventory"].set_index("Item")
        self.assertEqual(int(out.loc["TOP", "BOMLevel"]), 0)
        self.assertEqual(int(out.loc["MID", "BOMLevel"]), 1)
        self.assertEqual(int(out.loc["LEAF", "BOMLevel"]), 2)


# ===========================================================================
class TestGenericFactory(unittest.TestCase):
    """The whole point: a factory that is nothing like the sample must work.

    A furniture plant - different products, different parts, different work
    centres, 3 BOM levels instead of 4, 24 periods instead of 36.
    """

    @classmethod
    def setUpClass(cls):
        cls.data = cls._furniture()
        cls.R = run_pipeline(data=cls.data)

    @staticmethod
    def _furniture():
        inv = pd.DataFrame([
            ("CHAIR", "Office chair", "M", 900, 260, 1, "LFL", 0, 88.0, "-"),
            ("TABLE", "Desk 1400mm", "M", 620, 180, 1, "LFL", 0, 165.0, "-"),
            ("SEAT-PANEL", "Upholstered seat panel", "M", 950, 280, 1, "LFL", 0, 22.0, "-"),
            ("CHAIR-FRAME", "Chair frame welded", "M", 950, 280, 1, "LFL", 0, 31.0, "-"),
            ("TABLE-TOP", "Laminated table top", "M", 640, 190, 1, "LFL", 0, 54.0, "-"),
            ("LEG-SET", "Leg set of 4", "M", 640, 190, 1, "LFL", 0, 26.0, "-"),
            ("SCREW-KIT", "Fixings kit", "P", 3200, 900, 2, "FOQ", 2000, 3.2, "SUP-A"),
            ("PLYWOOD", "Plywood sheet", "P", 2600, 800, 2, "FOQ", 2000, 14.0, "SUP-B"),
            ("FOAM", "Seat foam block", "P", 1900, 600, 2, "FOQ", 1500, 6.5, "SUP-C"),
            ("STEEL-TUBE", "Steel tube metre", "P", 9500, 3000, 2, "FOQ", 6000, 2.1, "SUP-D"),
            ("LAMINATE", "Laminate sheet", "P", 1400, 400, 2, "FOQ", 1200, 9.0, "SUP-B"),
        ], columns=["Item", "Description", "SourceType", "OnHand", "SafetyStock",
                    "LeadTime", "LotSizeRule", "LotSizeValue", "UnitCost", "Supplier"])
        inv["BOMLevel"] = 0
        inv["UOM"] = "EA"
        inv["OrderingCost"] = 200.0
        inv["HoldingCostRate"] = 0.22
        inv["ScrapPct"] = 0.01

        bom = pd.DataFrame([
            ("CHAIR", "SEAT-PANEL", 1, 0.01), ("CHAIR", "CHAIR-FRAME", 1, 0.01),
            ("CHAIR", "SCREW-KIT", 1, 0.01),
            ("TABLE", "TABLE-TOP", 1, 0.01), ("TABLE", "LEG-SET", 1, 0.01),
            ("TABLE", "SCREW-KIT", 1, 0.01),
            ("SEAT-PANEL", "PLYWOOD", 0.5, 0.03), ("SEAT-PANEL", "FOAM", 1, 0.03),
            ("CHAIR-FRAME", "STEEL-TUBE", 2.5, 0.04),
            ("TABLE-TOP", "PLYWOOD", 1.2, 0.03), ("TABLE-TOP", "LAMINATE", 1, 0.03),
            ("LEG-SET", "STEEL-TUBE", 3.2, 0.04),
        ], columns=["ParentItem", "ComponentItem", "QtyPer", "ScrapPct"])

        rng = np.random.default_rng(3)
        rows = []
        for item, base, amp in [("CHAIR", 240, 0), ("TABLE", 120, 25)]:
            for p in range(1, 25):
                v = base + amp * np.sin(2 * np.pi * (p - 1) / 12) + rng.normal(0, 8)
                rows.append({"Period": p, "Week": f"W{p:02d}", "Item": item,
                             "Demand": int(max(0, round(v)))})
        demand = pd.DataFrame(rows)

        orders = pd.DataFrame([
            {"OrderID": f"SO-{i:04d}", "Item": it, "Period": p,
             "Quantity": int(b * max(0.1, 0.85 - 0.07 * (p - 1))),
             "Customer": "Contract Interiors", "Priority": "Normal"}
            for i, (it, b, p) in enumerate(
                [(it, b, p) for p in range(1, 13) for it, b in
                 [("CHAIR", 245), ("TABLE", 125)]], start=1)])

        supplier = pd.DataFrame([
            {"Supplier": s, "SupplierName": n, "Item": it,
             "LeadTime": int(inv.set_index("Item").loc[it, "LeadTime"]),
             "Reliability": r, "MinOrderQty": 100,
             "UnitPrice": float(inv.set_index("Item").loc[it, "UnitCost"])}
            for it, s, n, r in [("SCREW-KIT", "SUP-A", "FixFast Ltd", 0.94),
                                ("PLYWOOD", "SUP-B", "PanelCo", 0.88),
                                ("LAMINATE", "SUP-B", "PanelCo", 0.88),
                                ("FOAM", "SUP-C", "FoamWorks", 0.91),
                                ("STEEL-TUBE", "SUP-D", "TubeMetals", 0.79)]])

        routing = pd.DataFrame([
            ("SEAT-PANEL", 10, "Cut & Upholster", "WC-A", 0.4, 0.05),
            ("CHAIR-FRAME", 10, "Weld Frame", "WC-A", 0.6, 0.06),
            ("TABLE-TOP", 10, "Cut Panel", "WC-A", 0.5, 0.05),
            ("TABLE-TOP", 20, "Laminate & Edge", "WC-C", 0.7, 0.07),
            ("LEG-SET", 10, "Cut & Weld Legs", "WC-A", 0.4, 0.04),
            ("CHAIR", 10, "Final Assembly", "WC-B", 0.5, 0.08),
            ("TABLE", 10, "Final Assembly", "WC-B", 0.6, 0.10),
        ], columns=["Item", "OpSeq", "Operation", "WorkCentre",
                    "SetupTimeHrs", "RunTimeHrsPerUnit"])

        cap = pd.DataFrame([("WC-A", "Cutting & Welding", 3, 8, 2, 5, 0.88),
                            ("WC-B", "Assembly", 2, 8, 2, 5, 0.86),
                            ("WC-C", "Finishing", 1, 8, 2, 5, 0.90)],
                           columns=["WorkCentre", "Description", "NumMachines",
                                    "HoursPerShift", "ShiftsPerDay", "DaysPerWeek",
                                    "Efficiency"])
        cap["AvailableHoursPerPeriod"] = (
            cap.NumMachines * cap.HoursPerShift * cap.ShiftsPerDay
            * cap.DaysPerWeek * cap.Efficiency).round(2)

        return {
            "demand": demand, "orders": orders, "inventory": inv, "bom": bom,
            "supplier": supplier, "routing": routing, "capacity": cap,
            "mps": pd.DataFrame([{"Item": i, "Period": p, "MPSQty": 0}
                                 for i in ("CHAIR", "TABLE") for p in range(1, 13)]),
            "prodorders": pd.DataFrame(),
        }

    def test_the_dataset_is_internally_consistent(self):
        self.assertEqual(data_io.cross_check(self.data), [])

    def test_every_block_produces_output(self):
        for key in ("forecast", "inventory_params", "mps", "capacity_plan",
                    "gross_requirements", "mrp", "releases", "production_orders",
                    "rule_comparison", "exceptions", "kpis"):
            self.assertIn(key, self.R)
            val = self.R[key]
            if isinstance(val, pd.DataFrame):
                self.assertFalse(val.empty, f"{key} is empty for a non-sample factory")

    def test_forecasting_picked_a_model_for_each_product(self):
        sel = self.R["forecast_selection"]
        self.assertEqual(set(sel["Item"]), {"CHAIR", "TABLE"})
        self.assertTrue((sel["MAPE"] >= 0).all())

    def test_bom_explosion_found_the_right_depth_and_sharing(self):
        tree = self.R["bom_tree"]
        self.assertEqual(tree.max_level(), 2, "furniture BOM is 3 levels (0-2)")
        self.assertEqual(tree.low_level_code["STEEL-TUBE"], 2)
        self.assertEqual(len(tree.parents["STEEL-TUBE"]), 2,
                         "steel tube feeds both the chair frame and the leg set")
        self.assertEqual(len(tree.parents["SCREW-KIT"]), 2)

    def test_mrp_netting_identity_holds_on_this_factory_too(self):
        inv = self.data["inventory"].set_index("Item")
        for item, g in self.R["mrp"].groupby("Item"):
            prev = float(inv.loc[item, "OnHand"])
            for _, r in g.sort_values("Period").iterrows():
                expected = (prev + r["ScheduledReceipt"] + r["PlannedOrderReceipt"]
                            - r["GrossRequirement"])
                self.assertAlmostEqual(r["ProjectedAvailable"], expected, delta=0.02,
                                       msg=f"{item} period {int(r['Period'])}")
                prev = r["ProjectedAvailable"]

    def test_scheduling_ran_and_found_a_bottleneck(self):
        self.assertFalse(self.R["gantt"].empty, "no jobs were scheduled")
        wc = self.R["work_centre_results"]
        self.assertEqual(int(wc["Bottleneck"].sum()), 1)
        self.assertIn(wc[wc["Bottleneck"]].iloc[0]["WorkCentre"],
                      {"WC-A", "WC-B", "WC-C"})

    def test_scenarios_are_built_from_this_factory_not_the_sample(self):
        lib = build_scenarios(self.data, self.R)
        names = " ".join(lib)
        self.assertNotIn("BIKE", names)
        self.assertNotIn("WC03", names)
        self.assertTrue(any("CHAIR" in n or "TABLE" in n for n in lib))
        for spec in lib.values():
            blob = str(spec["overrides"])
            self.assertNotIn("BIKE", blob)

    def test_every_generated_scenario_runs(self):
        for name, spec in build_scenarios(self.data, self.R).items():
            with self.subTest(scenario=name):
                res = run_pipeline(data=self.data, overrides=spec["overrides"])
                self.assertIn("kpis", res)

    def test_the_agent_learns_this_factory_vocabulary(self):
        from agents.scenario_agent import build_vocabulary, interpret
        vocab = build_vocabulary(self.data)
        items, centres, sups = vocab
        self.assertIn("CHAIR", items)
        self.assertIn("finishing", centres["WC-C"])

        im = self.data["inventory"].set_index("Item").to_dict("index")
        p = interpret("what if CHAIR demand rises 40% in weeks 3 to 6", im, vocab)
        self.assertTrue(p["confident"])
        self.assertIn("CHAIR", p["overrides"]["demand_shock"])

        p = interpret("what if the finishing line runs at 50% in week 4", im, vocab)
        self.assertTrue(p["confident"])
        self.assertIn("WC-C", p["overrides"]["capacity"])

    def test_agents_run_and_report_on_this_factory(self):
        from agents import Coordinator
        c = Coordinator(self.R)
        c.run()
        self.assertGreater(len(c.bb.findings), 0)
        brief = c.briefing()
        self.assertNotIn("BIKE", brief)
        self.assertNotIn("SPOKE", brief)

    def test_exports_work_for_this_factory(self):
        blob = data_io.to_excel(self.R)
        self.assertGreater(len(blob), 10_000)
        self.assertEqual(len(data_io.output_tables(self.R)), 20)


# ===========================================================================
class TestCrossCheck(unittest.TestCase):

    def setUp(self):
        data_io.reset_to_sample()
        self.data = data_io.load_plan_data()

    @classmethod
    def tearDownClass(cls):
        data_io.reset_to_sample()

    def test_sample_data_passes(self):
        self.assertEqual(data_io.cross_check(self.data), [])

    def test_unknown_item_in_bom_is_caught(self):
        self.data["bom"] = pd.concat([
            self.data["bom"],
            pd.DataFrame([{"ParentItem": "BIKE-MTB", "ComponentItem": "GHOST-PART",
                           "QtyPer": 1, "ScrapPct": 0}])], ignore_index=True)
        self.assertTrue(any("GHOST-PART" in p for p in data_io.cross_check(self.data)))

    def test_routing_pointing_at_a_missing_work_centre_is_caught(self):
        self.data["routing"].loc[0, "WorkCentre"] = "WC99"
        self.assertTrue(any("WC99" in p for p in data_io.cross_check(self.data)))

    def test_too_little_demand_history_is_caught(self):
        self.data["demand"] = self.data["demand"][self.data["demand"]["Period"] <= 5]
        self.assertTrue(any("history" in p for p in data_io.cross_check(self.data)))


# ===========================================================================
class TestExport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        data_io.reset_to_sample()
        cls.R = run_pipeline(data=data_io.load_plan_data())

    def test_every_output_table_is_exported(self):
        tables = data_io.output_tables(cls_R := self.R)
        self.assertEqual(len(tables), 20)
        for name, df in tables.items():
            self.assertIsInstance(df, pd.DataFrame, f"{name} is not a table")

    def test_excel_workbook_opens_and_holds_inputs_and_outputs(self):
        blob = data_io.to_excel(self.R)
        book = pd.ExcelFile(io.BytesIO(blob))
        self.assertTrue(any(s.startswith("IN ") for s in book.sheet_names))
        self.assertTrue(any(s.startswith("OUT ") for s in book.sheet_names))
        self.assertTrue(all(len(s) <= 31 for s in book.sheet_names))

    def test_zip_contains_inputs_and_outputs(self):
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data_io.to_zip(self.R)))
        names = z.namelist()
        self.assertTrue(any(n.startswith("inputs/") for n in names))
        self.assertTrue(any(n.startswith("outputs/") for n in names))
        self.assertIsNone(z.testzip())

    def test_exported_inputs_re_import_cleanly(self):
        """A downloaded file must be re-uploadable without complaint."""
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data_io.inputs_zip()))
        for f in data_io.FILES:
            if not f["essential"] or f["file"] not in z.namelist():
                continue
            with self.subTest(file=f["file"]):
                raw = pd.read_csv(io.BytesIO(z.read(f["file"])))
                mapping = data_io.suggest_mapping(f["key"], list(raw.columns))
                _clean, _notes, problems = data_io.preview_mapping(
                    f["key"], raw, mapping)
                self.assertEqual(problems, [],
                                 f"exported {f['file']} fails its own import")


if __name__ == "__main__":
    unittest.main(verbosity=2)

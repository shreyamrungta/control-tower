"""
BLOCK 7 - MATERIAL REQUIREMENTS PLANNING (MRP)
==============================================================================
7.1 Inputs to MRP        gross requirements, inventory, scheduled receipts,
                         lead times, lot sizing policy
7.2 Run MRP logic        net requirement, planned order receipts,
                         planned order releases, projected available
7.3 Apply lot sizing     LFL / FOQ / EOQ
7.4 Generate MRP output  MRP_Output.csv  (Item, Week, GR, SR, PAB, NR,
                                          PORcpt, PORel)
7.5 Identify exceptions  shortages, late releases, critical components

Standard MRP record, computed period by period
----------------------------------------------
    available   = PA(t-1) + SR(t) - GR(t)
    NR(t)       = max(0, SafetyStock - available)
    PORcpt(t)   = lot_size(NR(t))
    PA(t)       = available + PORcpt(t)
    PORel(t-LT) = PORcpt(t)                  <- lead time offset

Items are processed in ascending LOW-LEVEL-CODE order so that every parent that
consumes an item has already been planned before that item is netted. This is
what makes shared components (SPOKE is used by all three wheels) come out right.
"""

from collections import defaultdict
import pandas as pd

from .config import HORIZON_PERIODS
from .lotsizing import apply_lot_size, describe


def run_mrp(data, bom_tree, mps_df, params_df=None,
            scheduled_receipts=None, horizon=HORIZON_PERIODS):
    """Full Block 7.

    Returns dict with:
      mrp        : the MRP grid, one row per item per period (7.4)
      releases   : planned order releases, ready for Block 8
      exceptions : shortage / late-release / critical-component list (7.5)
      pegging    : which parent drove each requirement (traceability)
    """
    item_master = data["inventory"].set_index("Item").to_dict("index")
    eoq_map = params_df.set_index("Item")["EOQ"].to_dict() if params_df is not None else {}
    sr = scheduled_receipts or {}          # {(item, period): qty}

    # ---- 7.1  independent demand: the MPS drives the finished goods -------
    gross = defaultdict(float)             # (item, period) -> qty
    pegging = defaultdict(list)            # (item, period) -> [(parent, qty)]

    for _, r in mps_df.iterrows():
        if r["MPSQty"] > 0:
            gross[(r["Item"], int(r["Period"]))] += float(r["MPSQty"])
            pegging[(r["Item"], int(r["Period"]))].append(("MPS", float(r["MPSQty"])))

    mrp_rows, release_rows, exception_rows = [], [], []

    # ---- 7.2  process items in low-level-code order -----------------------
    for item in bom_tree.items_by_level():
        if item not in item_master:
            continue
        m = item_master[item]
        lt = int(m["LeadTime"])
        ss = float(m["SafetyStock"])
        rule = m["LotSizeRule"]
        lot_value = float(m["LotSizeValue"])
        eoq = float(eoq_map.get(item, 0))
        pa = float(m["OnHand"])

        releases_this_item = {}

        for p in range(1, horizon + 1):
            gr = gross.get((item, p), 0.0)
            scheduled = float(sr.get((item, p), 0.0))

            available = pa + scheduled - gr
            nr = max(0.0, ss - available)
            porcpt = apply_lot_size(nr, rule, lot_value, eoq)
            pa_new = available + porcpt

            # ---- lead time offset -> planned order release ---------------
            rel_period = p - lt
            late = rel_period < 1
            if porcpt > 0:
                clamped = max(1, rel_period)
                releases_this_item[clamped] = releases_this_item.get(clamped, 0.0) + porcpt
                release_rows.append({
                    "Item": item,
                    "LowLevelCode": bom_tree.low_level_code.get(item, 0),
                    "SourceType": m["SourceType"],
                    "ReleasePeriod": clamped,
                    "DuePeriod": p,
                    "Quantity": round(porcpt, 3),
                    "LeadTime": lt,
                    "LotSizeRule": describe(rule, lot_value, eoq),
                    "LateRelease": late,
                    "Supplier": m["Supplier"],
                })
                if late:
                    exception_rows.append({
                        "Module": "MRP", "Item": item,
                        "ExceptionType": "Past-Due Release",
                        "Severity": "Critical", "Period": p,
                        "Message": (f"Order of {porcpt:,.0f} units is needed in period {p} "
                                    f"but the {lt}-period lead time means it should already "
                                    f"have been released in period {rel_period}"),
                        "RecommendedAction": ("Expedite with supplier, split the order, or "
                                              "pull the requirement later in the MPS"),
                    })

            mrp_rows.append({
                "Item": item,
                "LowLevelCode": bom_tree.low_level_code.get(item, 0),
                "SourceType": m["SourceType"],
                "Period": p,
                "GrossRequirement": round(gr, 3),
                "ScheduledReceipt": round(scheduled, 3),
                "ProjectedAvailable": round(pa_new, 3),
                "NetRequirement": round(nr, 3),
                "PlannedOrderReceipt": round(porcpt, 3),
                "SafetyStock": ss,
                "LeadTime": lt,
                "BelowSafety": pa_new < ss - 0.001,
                "Negative": pa_new < -0.001,
            })
            pa = pa_new

        # ---- write the release row back into the grid ---------------------
        for row in mrp_rows:
            if row["Item"] == item:
                row["PlannedOrderRelease"] = round(
                    releases_this_item.get(row["Period"], 0.0), 3)

        # ---- 7.2 explode this item's releases into its components ---------
        for rel_period, qty in releases_this_item.items():
            for child, required in bom_tree.explode(item, qty):
                gross[(child, rel_period)] += required
                pegging[(child, rel_period)].append((item, round(required, 1)))

    mrp_df = pd.DataFrame(mrp_rows)
    releases_df = pd.DataFrame(release_rows)

    # ---- 7.5 remaining exceptions -----------------------------------------
    exception_rows += _material_exceptions(mrp_df, item_master, bom_tree)

    pegging_df = pd.DataFrame([
        {"Item": i, "Period": p, "DrivenBy": par, "Quantity": q}
        for (i, p), lst in pegging.items() for par, q in lst
    ]).sort_values(["Item", "Period"]) if pegging else pd.DataFrame()

    return {
        "mrp": mrp_df,
        "releases": releases_df.sort_values(["ReleasePeriod", "LowLevelCode", "Item"])
                    if not releases_df.empty else releases_df,
        "exceptions": pd.DataFrame(exception_rows),
        "pegging": pegging_df,
    }


def _material_exceptions(mrp_df, item_master, bom_tree):
    """7.5 - shortages, safety-stock breaches and critical components."""
    rows = []
    if mrp_df.empty:
        return rows

    for item, g in mrp_df.groupby("Item"):
        g = g.sort_values("Period")
        m = item_master[item]

        neg = g[g["Negative"]]
        if not neg.empty:
            first = neg.iloc[0]
            rows.append({
                "Module": "MRP", "Item": item, "ExceptionType": "Material Shortage",
                "Severity": "Critical", "Period": int(first["Period"]),
                "Message": (f"Projected available goes negative "
                            f"({first['ProjectedAvailable']:,.0f}) in period "
                            f"{int(first['Period'])}; {len(neg)} period(s) affected"),
                "RecommendedAction": "Expedite supply or re-plan the parent order",
            })

        below = g[g["BelowSafety"] & ~g["Negative"]]
        if not below.empty:
            first = below.iloc[0]
            rows.append({
                "Module": "MRP", "Item": item, "ExceptionType": "Safety Stock Breach",
                "Severity": "Medium", "Period": int(first["Period"]),
                "Message": (f"Projected available {first['ProjectedAvailable']:,.0f} is "
                            f"below safety stock {first['SafetyStock']:,.0f} from period "
                            f"{int(first['Period'])}"),
                "RecommendedAction": "Increase lot size or review the safety stock level",
            })

        # a purchased item with a long lead time used by many parents is
        # structurally critical - worth flagging before it bites
        n_parents = len(bom_tree.parents.get(item, []))
        if m["SourceType"] == "P" and int(m["LeadTime"]) >= 4 and n_parents >= 2:
            rows.append({
                "Module": "MRP", "Item": item, "ExceptionType": "Critical Component",
                "Severity": "Low", "Period": 1,
                "Message": (f"{item} has a {int(m['LeadTime'])}-period lead time and feeds "
                            f"{n_parents} parent items - a supply failure propagates widely"),
                "RecommendedAction": "Dual-source or hold additional buffer stock",
            })
    return rows


def mrp_output_table(mrp_df):
    """7.4 - the classic MRP report layout, in flow-chart column order."""
    cols = ["Item", "LowLevelCode", "SourceType", "Period", "GrossRequirement",
            "ScheduledReceipt", "ProjectedAvailable", "NetRequirement",
            "PlannedOrderReceipt", "PlannedOrderRelease"]
    out = mrp_df[cols].copy()
    return out.rename(columns={
        "GrossRequirement": "GR", "ScheduledReceipt": "SR",
        "ProjectedAvailable": "PAB", "NetRequirement": "NR",
        "PlannedOrderReceipt": "PORcpt", "PlannedOrderRelease": "PORel",
        "Period": "Week",
    })


def item_grid(mrp_df, item):
    """Transpose one item's MRP record into the familiar horizontal grid."""
    g = mrp_df[mrp_df["Item"] == item].sort_values("Period")
    if g.empty:
        return pd.DataFrame()
    rows = {
        "Gross Requirements":     g["GrossRequirement"].tolist(),
        "Scheduled Receipts":     g["ScheduledReceipt"].tolist(),
        "Projected Available":    g["ProjectedAvailable"].tolist(),
        "Net Requirements":       g["NetRequirement"].tolist(),
        "Planned Order Receipts": g["PlannedOrderReceipt"].tolist(),
        "Planned Order Releases": g["PlannedOrderRelease"].tolist(),
    }
    return pd.DataFrame(rows, index=[f"P{p}" for p in g["Period"]]).T

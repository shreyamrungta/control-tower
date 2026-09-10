"""
BLOCK 8 - PRODUCTION ORDER RELEASE
==============================================================================
8.1 Identify candidate production orders   (from MRP planned releases)
8.2 Check material availability            (are all components available?)
8.3 Decision: materials available?
8.4 No  -> classify the constraint         material / capacity / supplier
8.5 Yes -> release to shop floor           status = Released, join the queue

Only MAKE items become production orders. Planned releases for PURCHASED items
become purchase requisitions and are reported separately.
"""

import pandas as pd

from .config import HORIZON_PERIODS


def run_order_release(data, bom_tree, mrp_result, horizon=HORIZON_PERIODS):
    """Full Block 8. Returns (production_orders, purchase_reqs, summary)."""
    item_master = data["inventory"].set_index("Item").to_dict("index")
    releases = mrp_result["releases"]
    mrp = mrp_result["mrp"]

    if releases.empty:
        return pd.DataFrame(), pd.DataFrame(), {}

    # projected availability lookup for the material check
    pa = {(r["Item"], int(r["Period"])): float(r["ProjectedAvailable"])
          for _, r in mrp.iterrows()}
    late_items = set(releases[releases["LateRelease"]]["Item"])

    prod_rows, purch_rows = [], []
    pid = pur = 1

    for _, r in releases.sort_values(["ReleasePeriod", "LowLevelCode", "Item"]).iterrows():
        item = r["Item"]
        m = item_master[item]

        # ---- purchased items -> purchase requisition ----------------------
        if m["SourceType"] == "P":
            purch_rows.append({
                "ReqID": f"PR-{pur:04d}",
                "Item": item,
                "Quantity": r["Quantity"],
                "Supplier": m["Supplier"],
                "ReleasePeriod": int(r["ReleasePeriod"]),
                "DuePeriod": int(r["DuePeriod"]),
                "LeadTime": int(r["LeadTime"]),
                "Status": "Past Due - Expedite" if r["LateRelease"] else "Planned",
                "Value": round(r["Quantity"] * float(m["UnitCost"]), 2),
            })
            pur += 1
            continue

        # ---- 8.2 material availability check ------------------------------
        shortages, waiting = [], []
        for comp, required in bom_tree.explode(item, r["Quantity"]):
            cm = item_master.get(comp)
            if cm is None:
                continue
            avail = pa.get((comp, int(r["ReleasePeriod"])), 0.0)
            if avail < 0:
                shortages.append(f"{comp} (short {abs(avail):,.0f})")
            elif comp in late_items:
                if cm["SourceType"] == "P":
                    waiting.append(f"{comp} (supplier {cm['Supplier']})")
                else:
                    shortages.append(f"{comp} (late order)")

        # ---- 8.3 / 8.4 / 8.5 ----------------------------------------------
        if shortages:
            status, constraint = "On Hold", "Material Constrained"
            note = "Missing: " + ", ".join(shortages[:3])
        elif waiting:
            status, constraint = "On Hold", "Waiting for Supplier"
            note = "Awaiting: " + ", ".join(waiting[:3])
        elif r["LateRelease"]:
            status, constraint = "On Hold", "Capacity Constrained"
            note = "Release date is already in the past - insufficient lead time"
        else:
            status, constraint = "Released", "None"
            note = "All components available"

        prod_rows.append({
            "OrderID": f"PO-{pid:04d}",
            "Item": item,
            "Description": m["Description"],
            "Quantity": r["Quantity"],
            "ReleasePeriod": int(r["ReleasePeriod"]),
            "DuePeriod": int(r["DuePeriod"]),
            "LowLevelCode": int(r["LowLevelCode"]),
            "Status": status,
            "ConstraintType": constraint,
            "Notes": note,
        })
        pid += 1

    prod_df = pd.DataFrame(prod_rows)
    purch_df = pd.DataFrame(purch_rows)

    summary = {
        "total_candidates": len(prod_df),
        "released": int((prod_df["Status"] == "Released").sum()) if not prod_df.empty else 0,
        "on_hold": int((prod_df["Status"] == "On Hold").sum()) if not prod_df.empty else 0,
        "purchase_reqs": len(purch_df),
        "purchase_value": round(purch_df["Value"].sum(), 2) if not purch_df.empty else 0.0,
        "expedite_count": int((purch_df["Status"].str.startswith("Past Due")).sum()) if not purch_df.empty else 0,
        "by_constraint": (prod_df["ConstraintType"].value_counts().to_dict()
                          if not prod_df.empty else {}),
    }
    return prod_df, purch_df, summary


# ===========================================================================
# BLOCK 9 - ROUTING & CAPACITY PREPARATION
# ===========================================================================
def build_job_list(production_orders, routing_df, capacity_df,
                   period_window=(1, 4), include_on_hold=False):
    """9.1 / 9.2 / 9.3 - turn released orders into an executable job list.

    Each production order becomes a JOB; each routing step becomes an
    OPERATION on that job, to be run in sequence at its work centre.

    period_window keeps the shop-floor simulation to a realistic execution
    window - a planner schedules the next few weeks, not the whole horizon.
    """
    if production_orders.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = production_orders.copy()
    if not include_on_hold:
        df = df[df["Status"] == "Released"]
    lo, hi = period_window
    df = df[(df["ReleasePeriod"] >= lo) & (df["ReleasePeriod"] <= hi)]

    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    ops = []
    for _, o in df.iterrows():
        r = routing_df[routing_df["Item"] == o["Item"]].sort_values("OpSeq")
        for _, step in r.iterrows():
            proc = float(step["SetupTimeHrs"]) + o["Quantity"] * float(step["RunTimeHrsPerUnit"])
            ops.append({
                "JobID": o["OrderID"],
                "Item": o["Item"],
                "Quantity": o["Quantity"],
                "OpSeq": int(step["OpSeq"]),
                "Operation": step["Operation"],
                "WorkCentre": step["WorkCentre"],
                "SetupHrs": float(step["SetupTimeHrs"]),
                "RunHrs": round(o["Quantity"] * float(step["RunTimeHrsPerUnit"]), 3),
                "ProcessHrs": round(proc, 3),
                "ReleasePeriod": int(o["ReleasePeriod"]),
                "DuePeriod": int(o["DuePeriod"]),
            })

    ops_df = pd.DataFrame(ops).sort_values(["JobID", "OpSeq"]).reset_index(drop=True)

    jobs_df = (ops_df.groupby(["JobID", "Item", "Quantity", "ReleasePeriod", "DuePeriod"])
               .agg(Operations=("OpSeq", "count"),
                    TotalProcessHrs=("ProcessHrs", "sum"))
               .reset_index()
               .sort_values("ReleasePeriod"))

    return jobs_df, ops_df

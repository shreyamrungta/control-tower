"""
BLOCK 4 - INVENTORY PLANNING
==============================================================================
4.1 Import inventory master data
4.2 Calculate inventory parameters (available inventory, reorder point,
    safety stock, EOQ, projected availability)
4.3 Project inventory over the planning horizon
4.4 Identify inventory exceptions
4.5 Output inventory report

Formulae used (all reproducible by hand)
----------------------------------------
    Holding cost      H    = HoldingCostRate x UnitCost        (per unit-year)
    EOQ                    = sqrt( 2 x D x S / H )
    Avg period demand d    = D / periods_per_year
    Statistical SS         = z x sigma_period x sqrt(LeadTime)
    Reorder point     ROP  = d x LeadTime + SafetyStock
    Periods of supply      = Available / d
"""

import numpy as np
import pandas as pd

from .config import (SERVICE_LEVEL_Z, PERIODS_PER_YEAR, HORIZON_PERIODS,
                     EXCESS_INVENTORY_RATIO)


# ===========================================================================
# 4.2  INVENTORY PARAMETERS
# ===========================================================================
def calculate_parameters(inventory_df, annual_demand, demand_sigma=None):
    """Compute EOQ, ROP, statistical safety stock and coverage for every item.

    annual_demand : {item: annual usage}   (from BOM roll-up of the forecast)
    demand_sigma  : {item: std dev of demand per period}  (optional; for
                    finished goods this is the forecast RMSE, for components
                    it is estimated from the parent's coefficient of variation)
    """
    demand_sigma = demand_sigma or {}
    rows = []

    for _, r in inventory_df.iterrows():
        item = r["Item"]
        D = float(annual_demand.get(item, 0.0))
        d = D / PERIODS_PER_YEAR
        H = float(r["HoldingCostRate"]) * float(r["UnitCost"])
        S = float(r["OrderingCost"])
        LT = float(r["LeadTime"])

        eoq = np.sqrt(2 * D * S / H) if D > 0 and H > 0 else 0.0

        # if no explicit sigma is supplied, assume a 25% coefficient of
        # variation - conservative and clearly documented
        sigma = float(demand_sigma.get(item, 0.25 * d))
        ss_stat = SERVICE_LEVEL_Z * sigma * np.sqrt(LT) if LT > 0 else SERVICE_LEVEL_Z * sigma
        ss_master = float(r["SafetyStock"])

        rop = d * LT + ss_master
        available = float(r["OnHand"])
        pos = available / d if d > 0 else np.inf

        rows.append({
            "Item": item,
            "Description": r["Description"],
            "BOMLevel": int(r["BOMLevel"]),
            "SourceType": r["SourceType"],
            "OnHand": available,
            "AnnualDemand": round(D, 1),
            "AvgPeriodDemand": round(d, 2),
            "LeadTime": int(LT),
            "UnitCost": float(r["UnitCost"]),
            "HoldingCostPerUnitYr": round(H, 3),
            "OrderingCost": S,
            "EOQ": int(round(eoq)),
            "SafetyStock_Master": ss_master,
            "SafetyStock_Statistical": int(round(ss_stat)),
            "ReorderPoint": int(round(rop)),
            "LotSizeRule": r["LotSizeRule"],
            "LotSizeValue": float(r["LotSizeValue"]),
            "PeriodsOfSupply": round(pos, 1) if np.isfinite(pos) else None,
            "InventoryValue": round(available * float(r["UnitCost"]), 2),
            "Supplier": r["Supplier"],
        })

    return pd.DataFrame(rows)


# ===========================================================================
# 4.3  PROJECT INVENTORY OVER THE PLANNING HORIZON
# ===========================================================================
def project_inventory(params_df, demand_by_period, horizon=HORIZON_PERIODS):
    """Reorder-point simulation of on-hand stock across the horizon.

    demand_by_period : {(item, period): qty}

    Logic per period:
        opening   = closing of previous period
        receipts  = orders placed LT periods ago that land now
        closing   = opening + receipts - demand
        if projected position (closing + on order) < ROP  -> place an order
    """
    rows = []
    for _, r in params_df.iterrows():
        item = r["Item"]
        on_hand = float(r["OnHand"])
        lt = int(r["LeadTime"])
        rop = float(r["ReorderPoint"])
        ss = float(r["SafetyStock_Master"])
        order_qty = _lot_size(r)

        pipeline = {}          # period -> qty arriving
        on_order = 0.0

        for p in range(1, horizon + 1):
            opening = on_hand
            receipts = pipeline.pop(p, 0.0)
            on_order -= receipts
            demand = float(demand_by_period.get((item, p), 0.0))
            closing = opening + receipts - demand

            position = closing + on_order
            placed = 0.0
            if position < rop:
                # order enough to cover back up to ROP, respecting the lot rule
                need = rop - position
                placed = max(order_qty, np.ceil(need / order_qty) * order_qty) if order_qty > 0 else need
                arrive = p + lt
                pipeline[arrive] = pipeline.get(arrive, 0.0) + placed
                on_order += placed

            rows.append({
                "Item": item, "Period": p,
                "Opening": round(opening, 3),
                "Receipts": round(receipts, 3),
                "Demand": round(demand, 3),
                "Closing": round(closing, 3),
                "OnOrder": round(on_order, 3),
                "SafetyStock": ss,
                "ReorderPoint": rop,
                "OrderPlaced": round(placed, 3),
                "BelowSafety": closing < ss,
                "Stockout": closing < 0,
            })
            on_hand = closing

    return pd.DataFrame(rows)


def _lot_size(r):
    rule = r["LotSizeRule"]
    if rule == "FOQ" and r["LotSizeValue"] > 0:
        return float(r["LotSizeValue"])
    if rule == "EOQ" and r["EOQ"] > 0:
        return float(r["EOQ"])
    return 0.0          # lot-for-lot


# ===========================================================================
# 4.4  INVENTORY EXCEPTIONS
# ===========================================================================
def identify_exceptions(params_df, projection_df,
                        excess_ratio=EXCESS_INVENTORY_RATIO):
    """Below safety stock, projected stockouts and excess inventory."""
    rows = []

    # --- current position exceptions ---------------------------------------
    for _, r in params_df.iterrows():
        if r["SafetyStock_Master"] > 0 and r["OnHand"] < r["SafetyStock_Master"]:
            rows.append(_ex(r["Item"], "Below Safety Stock", "High", 0,
                            f"On-hand {r['OnHand']:.0f} is below safety stock "
                            f"{r['SafetyStock_Master']:.0f}",
                            "Expedite replenishment order"))
        if r["SafetyStock_Master"] > 0 and r["OnHand"] > excess_ratio * r["SafetyStock_Master"]:
            rows.append(_ex(r["Item"], "Excess Inventory", "Low", 0,
                            f"On-hand {r['OnHand']:.0f} is {r['OnHand']/r['SafetyStock_Master']:.1f}x "
                            f"safety stock (value {r['InventoryValue']:,.0f})",
                            "Reduce order quantity / defer next replenishment"))
        if r["SafetyStock_Statistical"] > r["SafetyStock_Master"] * 1.5 and r["AvgPeriodDemand"] > 0:
            rows.append(_ex(r["Item"], "Safety Stock Too Low", "Medium", 0,
                            f"Statistical SS {r['SafetyStock_Statistical']:.0f} far exceeds "
                            f"master SS {r['SafetyStock_Master']:.0f} for a 95% service level",
                            "Raise safety stock in the item master"))

    # --- projected exceptions ----------------------------------------------
    # Consolidated the way a real MRP exception report is: ONE line per item
    # per exception type, showing the first period affected and the depth of
    # the problem - not one line per period, which drowns the planner.
    for item, g in projection_df.groupby("Item"):
        stockouts = g[g["Stockout"]]
        if not stockouts.empty:
            first = stockouts.iloc[0]
            worst = stockouts["Closing"].min()
            rows.append(_ex(item, "Projected Stockout", "Critical", first["Period"],
                            f"First stockout in period {int(first['Period'])}; "
                            f"{len(stockouts)} period(s) affected, worst shortfall "
                            f"{abs(worst):.0f} units",
                            "Expedite inbound order or re-plan MPS"))

        below = g[g["BelowSafety"] & ~g["Stockout"]]
        if not below.empty:
            first = below.iloc[0]
            worst = below["Closing"].min()
            ss = float(first["SafetyStock"])
            gap_pct = (1 - worst / ss) * 100 if ss > 0 else 0
            severity = "High" if gap_pct > 25 else "Medium"
            rows.append(_ex(item, "Projected Below Safety", severity, first["Period"],
                            f"Dips below safety stock from period {int(first['Period'])}; "
                            f"{len(below)} period(s) affected, lowest balance {worst:.0f} "
                            f"vs safety stock {ss:.0f} ({gap_pct:.0f}% below)",
                            "Bring forward the planned replenishment"))

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    df["_o"] = df["Severity"].map(order)
    return df.sort_values(["_o", "Item", "Period"]).drop(columns="_o").reset_index(drop=True)


def _ex(item, etype, severity, period, message, action):
    return {"Module": "Inventory", "Item": item, "ExceptionType": etype,
            "Severity": severity, "Period": int(period), "Message": message,
            "RecommendedAction": action}


# ===========================================================================
# 4.5  MAIN ENTRY POINT
# ===========================================================================
def run_inventory_planning(data, bom_tree, forecast_df, forecast_accuracy=None,
                           horizon=HORIZON_PERIODS):
    """Full Block 4. Returns (params_df, projection_df, exceptions_df)."""
    annual = bom_tree.annual_usage(forecast_df, PERIODS_PER_YEAR, horizon)

    # forecast RMSE is the best available estimate of demand variability for
    # finished goods; components inherit a scaled version of it
    sigma = {}
    if forecast_accuracy is not None and not forecast_accuracy.empty:
        for _, r in forecast_accuracy.iterrows():
            sigma[r["Item"]] = float(r["RMSE"])

    params = calculate_parameters(data["inventory"], annual, sigma)

    # independent demand for the projection = max(forecast, booked orders)
    demand_by_period = _independent_demand(forecast_df, data["orders"], horizon)
    # dependent demand for components = untimed BOM explosion of that demand
    sched = pd.DataFrame([{"Item": i, "Period": p, "Qty": q}
                          for (i, p), q in demand_by_period.items()])
    if not sched.empty:
        expl = bom_tree.explode_gross_requirements(sched)
        for _, r in expl.iterrows():
            key = (r["Item"], int(r["Period"]))
            if key not in demand_by_period:
                demand_by_period[key] = float(r["GrossRequirement"])

    projection = project_inventory(params, demand_by_period, horizon)
    exceptions = identify_exceptions(params, projection)
    return params, projection, exceptions


def _independent_demand(forecast_df, orders_df, horizon):
    """max(forecast, confirmed customer orders) per finished item per period."""
    fc = {(r["Item"], int(r["Period"])): float(r["Forecast"])
          for _, r in forecast_df.iterrows()}
    co = orders_df.groupby(["Item", "Period"])["Quantity"].sum().to_dict()
    out = {}
    for key in set(fc) | set(co):
        if key[1] <= horizon:
            out[key] = max(fc.get(key, 0.0), float(co.get(key, 0.0)))
    return out

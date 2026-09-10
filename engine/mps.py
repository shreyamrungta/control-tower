"""
BLOCK 5 - MASTER PRODUCTION SCHEDULING (MPS)
==============================================================================
5.1 Inputs to MPS            forecast, customer orders, inventory, capacity
5.2 Create / revise MPS      MPS quantity, lot size, maintain PAB
5.3 Calculate PAB            PAB(t) = PAB(t-1) + MPS(t) - max(Forecast, Orders)
5.4 Check MPS feasibility    negative PAB? safety stock violation? overload?
5.5 MPS OK?   No -> loop back to 5.2 and revise    Yes -> Block 6

The 5.4 -> 5.5 -> 5.2 feedback loop of the flow chart is implemented literally:
`run_mps` generates a trial schedule, rough-cut capacity planning tests it, and
if a work centre is overloaded the schedule is revised (production pulled
forward into periods with spare capacity) and re-tested, up to `max_iterations`.
Every iteration is recorded so the dashboard can show the convergence history.
"""

import numpy as np
import pandas as pd

from .config import HORIZON_PERIODS
from .lotsizing import apply_lot_size


# ===========================================================================
# 5.1  DEMAND INPUT
# ===========================================================================
def build_demand_input(forecast_df, orders_df, horizon=HORIZON_PERIODS):
    """Forecast vs booked orders per finished item per period.

    Gross demand uses max(forecast, orders): booked orders that exceed the
    forecast must be produced, and unconsumed forecast beyond the order book
    still has to be planned for.
    """
    fc = forecast_df.set_index(["Item", "Period"])["Forecast"].to_dict()
    co = orders_df.groupby(["Item", "Period"])["Quantity"].sum().to_dict()

    rows = []
    for item in sorted(forecast_df["Item"].unique()):
        for p in range(1, horizon + 1):
            f = float(fc.get((item, p), 0.0))
            o = float(co.get((item, p), 0.0))
            rows.append({
                "Item": item, "Period": p,
                "Forecast": f, "CustomerOrders": o,
                "GrossDemand": max(f, o),
            })
    return pd.DataFrame(rows)


# ===========================================================================
# 5.2 / 5.3  BUILD THE SCHEDULE AND ITS PROJECTED AVAILABLE BALANCE
# ===========================================================================
def build_mps(demand_df, item_master, params_df=None, horizon=HORIZON_PERIODS,
              forced=None):
    """Generate the MPS and its PAB row for every finished item.

    forced : optional {(item, period): qty} of MPS quantities fixed by the
             revision loop (5.2) or by a planner override on the dashboard.
    """
    forced = forced or {}
    eoq_map = {}
    if params_df is not None:
        eoq_map = params_df.set_index("Item")["EOQ"].to_dict()

    rows = []
    for item, g in demand_df.groupby("Item"):
        g = g.sort_values("Period")
        m = item_master[item]
        on_hand = float(m["OnHand"])
        ss = float(m["SafetyStock"])
        rule = m["LotSizeRule"]
        lot_value = float(m["LotSizeValue"])
        eoq = float(eoq_map.get(item, 0))

        pab_prev = on_hand
        cum_orders = 0.0

        for _, r in g.iterrows():
            p = int(r["Period"])
            demand = float(r["GrossDemand"])

            # ---- 5.2 determine the MPS quantity ---------------------------
            if (item, p) in forced:
                mps_qty = float(forced[(item, p)])
            else:
                projected = pab_prev - demand
                if projected < ss:
                    net_need = ss - projected          # bring back up to SS
                    mps_qty = apply_lot_size(net_need, rule, lot_value, eoq)
                else:
                    mps_qty = 0.0

            # ---- 5.3 projected available balance --------------------------
            pab = pab_prev + mps_qty - demand
            cum_orders += float(r["CustomerOrders"])

            rows.append({
                "Item": item, "Period": p,
                "Forecast": round(float(r["Forecast"]), 3),
                "CustomerOrders": round(float(r["CustomerOrders"]), 3),
                "GrossDemand": round(demand, 3),
                "OpeningPAB": round(pab_prev, 3),
                "MPSQty": round(mps_qty, 3),
                "PAB": round(pab, 3),
                "SafetyStock": ss,
                "BelowSafety": pab < ss,
                "NegativePAB": pab < 0,
            })
            pab_prev = pab

    mps_df = pd.DataFrame(rows)
    return _add_atp(mps_df)


def _add_atp(mps_df):
    """Available-to-Promise: the uncommitted quantity a salesperson may sell.

    Standard discrete ATP with look-back correction:

      * Period 1        ATP = OnHand + MPS(1) - orders up to the next MPS receipt
      * Later MPS period t   ATP = MPS(t) - orders from t up to the next receipt
      * Non-MPS periods ATP = 0 (nothing new becomes available)

    A negative ATP means that period is over-committed. The textbook correction
    is to carry the shortfall BACK and consume the nearest earlier positive ATP,
    because stock already on hand can be used to cover a later order. Without
    this look-back step ATP shows spurious negatives.
    """
    out = []
    for item, g in mps_df.groupby("Item"):
        g = g.sort_values("Period").reset_index(drop=True)
        n = len(g)
        receipt_idx = g.index[g["MPSQty"] > 0].tolist()

        # every schedule reports ATP in period 1, receipt or not
        checkpoints = sorted(set([0] + receipt_idx))
        atp = [0.0] * n

        for pos, i in enumerate(checkpoints):
            nxt = checkpoints[pos + 1] if pos + 1 < len(checkpoints) else n
            supply = float(g.loc[i, "MPSQty"])
            if i == 0:
                supply += float(g.loc[0, "OpeningPAB"])     # opening stock
            committed = float(g.loc[i:nxt - 1, "CustomerOrders"].sum())
            atp[i] = supply - committed

        # ---- look-back correction ------------------------------------------
        for i in range(n - 1, -1, -1):
            if atp[i] < 0:
                shortfall = -atp[i]
                atp[i] = 0.0
                for j in range(i - 1, -1, -1):
                    if atp[j] <= 0:
                        continue
                    take = min(atp[j], shortfall)
                    atp[j] -= take
                    shortfall -= take
                    if shortfall <= 0:
                        break
                if shortfall > 0:
                    # genuinely over-committed: no earlier stock can cover it
                    atp[i] = -shortfall

        g["ATP"] = [round(v, 3) for v in atp]
        out.append(g)
    return pd.concat(out, ignore_index=True)


# ===========================================================================
# 5.4  ROUGH-CUT CAPACITY PLANNING  (the feasibility test)
# ===========================================================================
def rough_cut_capacity(mps_df, bom_tree, routing_df, capacity_df,
                       item_master, horizon=HORIZON_PERIODS,
                       capacity_periods=None):
    """Convert the MPS into work-centre hours and compare with availability.

    The MPS is exploded through the whole BOM (untimed) so that sub-assembly
    and component operations are loaded too - this is a full rough-cut plan,
    not just final assembly.

    Setup time is charged once per item per period (one batch per period).
    """
    sched = mps_df[mps_df["MPSQty"] > 0][["Item", "Period", "MPSQty"]].rename(
        columns={"MPSQty": "Qty"})
    if sched.empty:
        return pd.DataFrame(), pd.DataFrame()

    exploded = bom_tree.explode_gross_requirements(sched)

    make_items = {i for i, m in item_master.items() if m["SourceType"] == "M"}
    rt = routing_df.set_index("Item")

    load = {}      # (wc, period) -> hours
    detail = []
    for _, r in exploded.iterrows():
        item, p, qty = r["Item"], int(r["Period"]), float(r["GrossRequirement"])
        if item not in make_items or qty <= 0 or p > horizon:
            continue
        ops = routing_df[routing_df["Item"] == item]
        for _, op in ops.iterrows():
            hrs = float(op["SetupTimeHrs"]) + qty * float(op["RunTimeHrsPerUnit"])
            key = (op["WorkCentre"], p)
            load[key] = load.get(key, 0.0) + hrs
            detail.append({"Item": item, "Period": p, "Quantity": round(qty, 1),
                           "WorkCentre": op["WorkCentre"], "Operation": op["Operation"],
                           "SetupHrs": float(op["SetupTimeHrs"]),
                           "RunHrs": round(qty * float(op["RunTimeHrsPerUnit"]), 2),
                           "TotalHrs": round(hrs, 2)})

    avail = capacity_df.set_index("WorkCentre")["AvailableHoursPerPeriod"].to_dict()
    rows = []
    for wc in capacity_df["WorkCentre"]:
        for p in range(1, horizon + 1):
            required = load.get((wc, p), 0.0)
            # a timed machine breakdown overrides the standing capacity
            available = float((capacity_periods or {}).get((wc, p), avail[wc]))
            rows.append({
                "WorkCentre": wc, "Period": p,
                "RequiredHours": round(required, 2),
                "AvailableHours": round(available, 2),
                "Utilisation": round(required / available, 4) if available else 0,
                "Overloaded": required > available,
                "OverloadHours": round(max(0.0, required - available), 2),
            })
    return pd.DataFrame(rows), pd.DataFrame(detail)


def check_feasibility(mps_df, capacity_plan):
    """5.4 - collect every reason the MPS would fail."""
    issues = []

    neg = mps_df[mps_df["NegativePAB"]]
    for _, r in neg.iterrows():
        issues.append({"Type": "Negative PAB", "Severity": "Critical",
                       "Item": r["Item"], "Period": int(r["Period"]),
                       "Detail": f"PAB {r['PAB']:.0f} is negative - demand cannot be met"})

    low = mps_df[mps_df["BelowSafety"] & ~mps_df["NegativePAB"]]
    for _, r in low.iterrows():
        issues.append({"Type": "Safety Stock Violation", "Severity": "High",
                       "Item": r["Item"], "Period": int(r["Period"]),
                       "Detail": f"PAB {r['PAB']:.0f} below safety stock {r['SafetyStock']:.0f}"})

    if not capacity_plan.empty:
        over = capacity_plan[capacity_plan["Overloaded"]]
        for _, r in over.iterrows():
            issues.append({"Type": "Capacity Overload", "Severity": "Critical",
                           "Item": r["WorkCentre"], "Period": int(r["Period"]),
                           "Detail": f"{r['WorkCentre']} needs {r['RequiredHours']:.0f}h "
                                     f"but only {r['AvailableHours']:.0f}h available "
                                     f"({r['Utilisation']*100:.0f}% utilisation)"})
    return pd.DataFrame(issues)


# ===========================================================================
# 5.5  THE REVISION LOOP
# ===========================================================================
def run_mps(data, bom_tree, forecast_df, params_df=None,
            horizon=HORIZON_PERIODS, max_iterations=6,
            capacity_periods=None, orders_df=None):
    """Full Block 5 including the 5.5 -> 5.2 feasibility loop.

    Revision strategy when a work centre is overloaded: pull MPS quantity
    BACKWARD into an earlier period that still has spare capacity. This trades
    a little extra inventory for a feasible plan - the classic level-loading
    move a master scheduler makes by hand.
    """
    item_master = data["inventory"].set_index("Item").to_dict("index")
    demand_df = build_demand_input(
        forecast_df, data["orders"] if orders_df is None else orders_df, horizon)

    forced = {}
    history = []
    mps_df = capacity_plan = detail = issues = None

    for iteration in range(1, max_iterations + 1):
        mps_df = build_mps(demand_df, item_master, params_df, horizon, forced)
        capacity_plan, detail = rough_cut_capacity(
            mps_df, bom_tree, data["routing"], data["capacity"], item_master,
            horizon, capacity_periods)
        issues = check_feasibility(mps_df, capacity_plan)

        n_cap = int((issues["Type"] == "Capacity Overload").sum()) if not issues.empty else 0
        n_neg = int((issues["Type"] == "Negative PAB").sum()) if not issues.empty else 0
        history.append({
            "Iteration": iteration,
            "CapacityOverloads": n_cap,
            "NegativePAB": n_neg,
            "SafetyViolations": int((issues["Type"] == "Safety Stock Violation").sum()) if not issues.empty else 0,
            "PeakUtilisation": round(capacity_plan["Utilisation"].max(), 3) if not capacity_plan.empty else 0,
            "TotalMPSUnits": int(mps_df["MPSQty"].sum()),
            "Feasible": n_cap == 0 and n_neg == 0,
        })

        # ---- 5.5 MPS OK? --------------------------------------------------
        if n_cap == 0 and n_neg == 0:
            break
        if iteration == max_iterations:
            break

        # ---- No -> 5.2 revise ---------------------------------------------
        new_forced = _level_load(mps_df, capacity_plan, detail, forced)
        if new_forced == forced:
            break                      # no further improvement possible
        forced = new_forced

    return {
        "demand_input": demand_df,
        "mps": mps_df,
        "capacity_plan": capacity_plan,
        "capacity_detail": detail,
        "issues": issues,
        "history": pd.DataFrame(history),
        "feasible": bool(history[-1]["Feasible"]),
        "iterations": len(history),
    }


def _level_load(mps_df, capacity_plan, detail, forced):
    """Pull MPS quantity backward out of overloaded periods into slack ones."""
    forced = dict(forced)
    if capacity_plan.empty or detail.empty:
        return forced

    over = capacity_plan[capacity_plan["Overloaded"]].sort_values(
        "OverloadHours", ascending=False)
    if over.empty:
        return forced

    # slack hours per (wc, period) after the current plan
    slack = {(r["WorkCentre"], int(r["Period"])):
             max(0.0, r["AvailableHours"] - r["RequiredHours"])
             for _, r in capacity_plan.iterrows()}

    for _, ov in over.iterrows():
        wc, period, overload = ov["WorkCentre"], int(ov["Period"]), ov["OverloadHours"]
        if period <= 1:
            continue     # nothing earlier to pull into

        # which finished items drive load on this work centre in this period?
        contributors = detail[(detail["WorkCentre"] == wc) &
                              (detail["Period"] == period)]
        if contributors.empty:
            continue

        for _, c in contributors.sort_values("TotalHrs", ascending=False).iterrows():
            if overload <= 0:
                break
            # find the finished item whose MPS drives this contributor
            candidates = mps_df[(mps_df["Period"] == period) & (mps_df["MPSQty"] > 0)]
            for _, m in candidates.iterrows():
                item, p = m["Item"], int(m["Period"])
                cur = float(forced.get((item, p), m["MPSQty"]))
                if cur <= 0:
                    continue
                # how much can the previous period absorb?
                room = slack.get((wc, p - 1), 0.0)
                if room <= 0.5:
                    continue
                hrs_per_unit = max(c["RunHrs"] / max(c["Quantity"], 1), 1e-6)
                movable_units = min(cur * 0.5, room / hrs_per_unit)
                movable_units = float(int(movable_units))
                if movable_units < 1:
                    continue

                forced[(item, p)] = cur - movable_units
                prev = float(forced.get((item, p - 1),
                                        mps_df[(mps_df["Item"] == item) &
                                               (mps_df["Period"] == p - 1)]["MPSQty"].sum()))
                forced[(item, p - 1)] = prev + movable_units

                used = movable_units * hrs_per_unit
                slack[(wc, p - 1)] = room - used
                overload -= used
                if overload <= 0:
                    break
    return forced

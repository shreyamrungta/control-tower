"""
BLOCK 12 - EXCEPTION MANAGEMENT & MANAGERIAL DECISION
==============================================================================
12.1 Detect exceptions automatically   forecast / inventory / supplier / MRP /
                                       capacity / scheduling
12.2 Show recommended actions          expedite, reschedule, alternate supplier,
                                       change priority, run what-if
12.3 Manager decision                  accept, modify, reject, add notes
12.4 Implement decision in the system   update data, re-run required modules,
                                        re-simulate the schedule

Every recommended action is expressed as a concrete OVERRIDE that the pipeline
can actually apply (see scenarios.apply_overrides), so approving a
recommendation genuinely re-plans the factory rather than just ticking a box.
"""

import json
import os
from datetime import datetime

import pandas as pd

from .config import OUTPUT_DIR, MAPE_ALERT_THRESHOLD, TRACKING_SIGNAL_LIMIT

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


# ===========================================================================
# 12.1  DETECT EXCEPTIONS ACROSS EVERY MODULE
# ===========================================================================
def detect_forecast_exceptions(selection_df):
    rows = []
    if selection_df is None or selection_df.empty:
        return rows
    for _, r in selection_df.iterrows():
        if r["MAPE"] > MAPE_ALERT_THRESHOLD:
            rows.append({
                "Module": "Forecast", "Item": r["Item"],
                "ExceptionType": "Forecast Accuracy",
                "Severity": "High", "Period": 0,
                "Message": (f"Best model ({r['SelectedMethod']}) still has MAPE "
                            f"{r['MAPE']}%, above the {MAPE_ALERT_THRESHOLD}% threshold"),
                "RecommendedAction": "Review demand pattern; consider a causal model or manual override",
            })
        if abs(r["TrackingSignal"]) > TRACKING_SIGNAL_LIMIT:
            direction = "under-forecasting" if r["TrackingSignal"] > 0 else "over-forecasting"
            rows.append({
                "Module": "Forecast", "Item": r["Item"],
                "ExceptionType": "Forecast Bias",
                "Severity": "Medium", "Period": 0,
                "Message": (f"Tracking signal {r['TrackingSignal']} (limit "
                            f"+/-{TRACKING_SIGNAL_LIMIT}) - the model is consistently "
                            f"{direction}"),
                "RecommendedAction": "Switch to a trend-capable model or add a bias correction",
            })
    return rows


def detect_supplier_exceptions(supplier_df, releases_df):
    """Supplier risk: low reliability combined with an actual order to place."""
    rows = []
    if supplier_df is None or supplier_df.empty:
        return rows
    ordered = set(releases_df["Item"]) if releases_df is not None and not releases_df.empty else set()
    for _, r in supplier_df.iterrows():
        if r["Item"] not in ordered:
            continue
        if r["Reliability"] < 0.85:
            rows.append({
                "Module": "Supplier", "Item": r["Item"],
                "ExceptionType": "Supplier Risk",
                "Severity": "High" if r["Reliability"] < 0.82 else "Medium",
                "Period": 0,
                "Message": (f"{r['SupplierName']} ({r['Supplier']}) has "
                            f"{r['Reliability']*100:.0f}% on-time reliability on a "
                            f"{r['LeadTime']}-period lead time"),
                "RecommendedAction": "Qualify an alternate supplier or raise the buffer stock",
            })
    return rows


def detect_capacity_exceptions(capacity_plan):
    rows = []
    if capacity_plan is None or capacity_plan.empty:
        return rows
    over = capacity_plan[capacity_plan["Overloaded"]]
    for wc, g in over.groupby("WorkCentre"):
        worst = g.loc[g["OverloadHours"].idxmax()]
        rows.append({
            "Module": "Capacity", "Item": wc,
            "ExceptionType": "Capacity Overload",
            "Severity": "Critical", "Period": int(worst["Period"]),
            "Message": (f"{wc} overloaded in {len(g)} period(s); worst is period "
                        f"{int(worst['Period'])} needing {worst['OverloadHours']:.0f}h "
                        f"more than available"),
            "RecommendedAction": "Add a shift, subcontract, or level-load the MPS",
        })
    return rows


def consolidate(*exception_frames):
    """Merge every module's exceptions into one prioritised register."""
    frames = []
    for f in exception_frames:
        if f is None:
            continue
        if isinstance(f, list):
            f = pd.DataFrame(f)
        if isinstance(f, pd.DataFrame) and not f.empty:
            frames.append(f)

    if not frames:
        return pd.DataFrame(columns=["ExceptionID", "Module", "Item", "ExceptionType",
                                     "Severity", "Period", "Message", "RecommendedAction"])

    df = pd.concat(frames, ignore_index=True)
    df["_o"] = df["Severity"].map(SEVERITY_ORDER).fillna(9)
    df = (df.sort_values(["_o", "Module", "Item", "Period"])
            .drop(columns="_o").reset_index(drop=True))
    df.insert(0, "ExceptionID", [f"EX-{i:03d}" for i in range(1, len(df) + 1)])
    return df


# ===========================================================================
# 12.2  RECOMMENDED ACTIONS - each one is an executable override
# ===========================================================================
def recommended_actions(exception, item_master=None, supplier_df=None):
    """Return concrete, executable options for one exception.

    Each option is {label, description, overrides, rerun_from} where
    `overrides` is the payload scenarios.apply_overrides understands and
    `rerun_from` names the earliest pipeline block that must be recomputed.
    """
    item = exception["Item"]
    etype = exception["ExceptionType"]
    opts = []
    m = (item_master or {}).get(item, {})

    if etype in ("Past-Due Release", "Material Shortage", "Projected Stockout",
                 "Waiting for Supplier"):
        lt = int(m.get("LeadTime", 2))
        if lt > 1:
            opts.append({
                "label": "Expedite - halve the lead time",
                "description": f"Pay for expedited freight to cut {item} lead time "
                               f"from {lt} to {max(1, lt // 2)} periods",
                "overrides": {"lead_time": {item: max(1, lt // 2)}},
                "rerun_from": "mrp",
            })
        alt = _alternate_supplier(item, supplier_df)
        if alt:
            opts.append({
                "label": f"Switch to alternate supplier {alt['Supplier']}",
                "description": f"Move {item} to {alt['SupplierName']} "
                               f"({alt['LeadTime']}-period lead time, "
                               f"{alt['Reliability']*100:.0f}% reliability)",
                "overrides": {"supplier": {item: alt["Supplier"]},
                              "lead_time": {item: int(alt["LeadTime"])}},
                "rerun_from": "mrp",
            })
        oh = float(m.get("OnHand", 0))
        opts.append({
            "label": "Emergency buy-in",
            "description": f"Raise {item} on-hand by 30% via a spot purchase",
            "overrides": {"on_hand": {item: round(oh * 1.3)}},
            "rerun_from": "inventory",
        })

    if etype in ("Below Safety Stock", "Safety Stock Breach", "Projected Below Safety",
                 "Safety Stock Too Low", "Supplier Risk"):
        ss = float(m.get("SafetyStock", 0))
        opts.append({
            "label": "Raise safety stock by 25%",
            "description": f"Increase {item} safety stock from {ss:,.0f} to "
                           f"{ss*1.25:,.0f} units",
            "overrides": {"safety_stock": {item: round(ss * 1.25)}},
            "rerun_from": "inventory",
        })

    if etype == "Capacity Overload" or etype == "Capacity Bottleneck":
        opts.append({
            "label": "Add a shift",
            "description": f"Run {item} on an additional shift per day",
            "overrides": {"capacity": {item: {"ShiftsPerDay": "+1"}}},
            "rerun_from": "mps",
        })
        opts.append({
            "label": "Add a machine",
            "description": f"Bring one more machine online at {item}",
            "overrides": {"capacity": {item: {"NumMachines": "+1"}}},
            "rerun_from": "mps",
        })

    if etype in ("Late Jobs",):
        opts.append({
            "label": "Switch dispatching rule to EDD",
            "description": "Sequence by earliest due date to cut tardiness",
            "overrides": {"dispatch_rule": "EDD"},
            "rerun_from": "scheduling",
        })

    if etype in ("Forecast Accuracy", "Forecast Bias"):
        opts.append({
            "label": "Force a trend-capable model",
            "description": f"Use Holt's method for {item} instead of the "
                           f"lowest-MAPE model",
            "overrides": {"forecast_method": {item: "Holt"}},
            "rerun_from": "forecast",
        })

    if etype == "Excess Inventory":
        opts.append({
            "label": "Cut safety stock by 20%",
            "description": f"Release working capital tied up in {item}",
            "overrides": {"safety_stock": {item: round(float(m.get('SafetyStock', 0)) * 0.8)}},
            "rerun_from": "inventory",
        })

    opts.append({
        "label": "Run a what-if instead",
        "description": "Model the impact before committing to any change",
        "overrides": {},
        "rerun_from": None,
    })
    return opts


def _alternate_supplier(item, supplier_df):
    """Find the most reliable supplier that is not the incumbent."""
    if supplier_df is None or supplier_df.empty:
        return None
    cur = supplier_df[supplier_df["Item"] == item]
    if cur.empty:
        return None
    incumbent = cur.iloc[0]["Supplier"]
    others = supplier_df[supplier_df["Supplier"] != incumbent]
    if others.empty:
        return None
    best = others.sort_values(["Reliability", "LeadTime"],
                              ascending=[False, True]).iloc[0]
    return {"Supplier": best["Supplier"], "SupplierName": best["SupplierName"],
            "LeadTime": int(best["LeadTime"]), "Reliability": float(best["Reliability"])}


# ===========================================================================
# 12.3 / 12.4  MANAGER DECISION LOG
# ===========================================================================
class DecisionLog:
    """Records manager decisions and turns approved ones into live overrides.

    Persisted to JSON so decisions survive a dashboard restart - this is the
    'Implement Decision in System' step of the flow chart, not a mock-up.
    """

    def __init__(self, path=None):
        self.path = path or os.path.join(OUTPUT_DIR, "decision_log.json")
        self.entries = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.entries, f, indent=2)

    def record(self, exception_id, item, exception_type, decision, action_label,
               overrides, rerun_from, notes="", decided_by="Planner"):
        """12.3 - accept / modify / reject, with free-text notes."""
        self.entries.append({
            "Timestamp": datetime.now().isoformat(timespec="seconds"),
            "ExceptionID": exception_id,
            "Item": item,
            "ExceptionType": exception_type,
            "Decision": decision,                # Approved / Rejected / Modified
            "Action": action_label,
            "Overrides": overrides,
            "RerunFrom": rerun_from,
            "Notes": notes,
            "DecidedBy": decided_by,
        })
        self.save()
        return self.entries[-1]

    def active_overrides(self):
        """12.4 - merge every approved decision into one override payload."""
        merged = {}
        for e in self.entries:
            if e["Decision"] not in ("Approved", "Modified"):
                continue
            for key, value in (e["Overrides"] or {}).items():
                if isinstance(value, dict):
                    merged.setdefault(key, {}).update(value)
                else:
                    merged[key] = value
        return merged

    def earliest_rerun(self):
        """Which pipeline block must be recomputed for the approved decisions."""
        order = ["forecast", "inventory", "mps", "bom", "mrp", "release", "scheduling"]
        stages = [e["RerunFrom"] for e in self.entries
                  if e["Decision"] in ("Approved", "Modified") and e["RerunFrom"]]
        if not stages:
            return None
        return min(stages, key=lambda s: order.index(s) if s in order else 99)

    def to_frame(self):
        if not self.entries:
            return pd.DataFrame(columns=["Timestamp", "ExceptionID", "Item",
                                         "Decision", "Action", "Notes", "DecidedBy"])
        df = pd.DataFrame(self.entries)
        return df[["Timestamp", "ExceptionID", "Item", "ExceptionType",
                   "Decision", "Action", "Notes", "DecidedBy"]]

    def clear(self):
        self.entries = []
        self.save()

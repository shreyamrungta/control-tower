"""
generate_data.py  --  BLOCK 2 of the flow chart: PREPARE INPUT DATASET FILES
==============================================================================
Generates the nine input CSV files for the Integrated Manufacturing Operations
Control Tower, using a 4-level bicycle manufacturing case.

Case:   3 finished bicycles, 32 items, 4 BOM levels (0-3), 6 work centres,
        36 periods (weeks) of demand history, 12 period planning horizon.

Demand patterns are deliberately different per product so that different
forecasting models win for different products (Block 3.4):
    BIKE-MTB   -> level + positive trend        -> Holt's method should win
    BIKE-ROAD  -> level + trend + seasonality   -> Holt-Winters should win
    BIKE-KIDS  -> stationary + noise + spikes   -> MA / SES should win

Run:  python generate_data.py
"""

import os
import numpy as np
import pandas as pd

SEED = 42
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

HISTORY_PERIODS = 36      # 2.1 requirement: 24-36 periods
HORIZON_PERIODS = 12      # forward planning horizon
SEASON_LENGTH = 12        # 3 full seasonal cycles inside 36 periods

FINISHED = ["BIKE-MTB", "BIKE-ROAD", "BIKE-KIDS"]


# ---------------------------------------------------------------------------
# ITEM MASTER  (drives Inventory_Master.csv)
# ---------------------------------------------------------------------------
# Stock levels are expressed in WEEKS OF SUPPLY and converted into units at the
# bottom of this file by rolling average finished-goods demand down the BOM.
# That keeps the dataset internally consistent: a spoke, of which 66 are used
# per mountain bike, automatically gets a stock level 66x larger than the bike.
#
# (item, description, bom_level, source M=make P=purchase, onhand_weeks,
#  safety_weeks, leadtime, lot rule, FOQ in weeks, unit cost, supplier, scrap%)
#
# Three items are deliberately under-stocked to create realistic, traceable
# exceptions for Blocks 4.4 / 7.5 / 12.1 rather than a wall of red:
#     DERAILLEUR  - long lead time (5) from the least reliable supplier
#     TYRE-700C   - long lead time (4), thin cover
#     FRAME-ROAD  - thin cover on a made item, drives a capacity exception
ITEMS = [
    # ---- Level 0 : finished goods -----------------------------------------
    ("BIKE-MTB",  "Mountain Bike 26 inch, 21 speed", 0, "M", 3.0, 1.2, 1, "LFL", 0,   210.00, "-",     0.01),
    ("BIKE-ROAD", "Road Bike 700C, 14 speed",        0, "M", 3.2, 1.3, 1, "LFL", 0,   265.00, "-",     0.01),
    ("BIKE-KIDS", "Kids Bike 20 inch, single speed", 0, "M", 3.5, 1.4, 1, "FOQ", 2.0,  95.00, "-",     0.01),

    # ---- Level 1 : major sub-assemblies -----------------------------------
    ("FRAME-MTB",   "Frame assembly, MTB, painted",  1, "M", 3.4, 1.5, 2, "LFL", 0,    62.00, "-",     0.02),
    ("FRAME-ROAD",  "Frame assembly, Road, painted", 1, "M", 1.5, 1.4, 2, "LFL", 0,    78.00, "-",     0.02),   # short
    ("FRAME-KIDS",  "Frame assembly, Kids, painted", 1, "M", 3.8, 1.6, 2, "FOQ", 2.0,  28.00, "-",     0.02),
    ("WHEEL-26",    "Wheel assembly 26 inch",        1, "M", 3.2, 1.3, 1, "LFL", 0,    24.00, "-",     0.015),
    ("WHEEL-700C",  "Wheel assembly 700C",           1, "M", 3.0, 1.3, 1, "LFL", 0,    29.00, "-",     0.015),
    ("WHEEL-20",    "Wheel assembly 20 inch",        1, "M", 3.6, 1.5, 1, "FOQ", 2.0,  14.00, "-",     0.015),
    ("DRIVE-21S",   "Drivetrain 21 speed",           1, "M", 3.1, 1.3, 1, "LFL", 0,    41.00, "-",     0.01),
    ("DRIVE-14S",   "Drivetrain 14 speed",           1, "M", 3.3, 1.4, 1, "LFL", 0,    47.00, "-",     0.01),
    ("DRIVE-SS",    "Drivetrain single speed",       1, "M", 3.7, 1.5, 1, "EOQ", 0,    16.00, "-",     0.01),
    ("BRAKE-DISC",  "Disc brake set (F+R)",          1, "P", 4.2, 2.0, 3, "FOQ", 2.5,  22.00, "SUP-01", 0.0),
    ("BRAKE-RIM",   "Rim brake set (F+R)",           1, "P", 3.9, 1.8, 2, "FOQ", 2.5,   9.50, "SUP-01", 0.0),
    ("SADDLE-ASSY", "Saddle with seatpost",          1, "P", 4.0, 1.8, 2, "EOQ", 0,     7.80, "SUP-02", 0.0),

    # ---- Level 2 : components ---------------------------------------------
    ("TUBESET-MTB",  "Cut & mitered tube set, MTB",  2, "M", 3.0, 1.2, 1, "LFL", 0,    23.00, "-",     0.03),
    ("TUBESET-ROAD", "Cut & mitered tube set, Road", 2, "M", 2.9, 1.2, 1, "LFL", 0,    31.00, "-",     0.03),
    ("TUBESET-KIDS", "Cut & mitered tube set, Kids", 2, "M", 3.4, 1.4, 1, "FOQ", 2.0,   9.00, "-",     0.03),
    ("RIM-26",       "Alloy rim 26 inch",            2, "P", 4.4, 2.2, 3, "FOQ", 2.5,   6.20, "SUP-03", 0.0),
    ("RIM-700C",     "Alloy rim 700C",               2, "P", 4.1, 2.1, 3, "FOQ", 2.5,   7.40, "SUP-03", 0.0),
    ("RIM-20",       "Steel rim 20 inch",            2, "P", 4.0, 1.9, 2, "FOQ", 2.5,   3.10, "SUP-03", 0.0),
    ("HUB-STD",      "Standard hub (shared)",        2, "P", 5.0, 2.6, 4, "EOQ", 0,     4.50, "SUP-04", 0.0),
    ("SPOKE",        "Stainless spoke (shared)",     2, "P", 4.5, 2.0, 2, "FOQ", 3.0,   0.09, "SUP-04", 0.0),
    ("TYRE-26",      "Tyre 26x2.10",                 2, "P", 5.2, 2.6, 4, "FOQ", 2.5,   5.60, "SUP-05", 0.0),
    ("TYRE-700C",    "Tyre 700x25C",                 2, "P", 1.8, 2.4, 4, "FOQ", 2.5,   6.90, "SUP-05", 0.0),   # short
    ("TYRE-20",      "Tyre 20x1.75",                 2, "P", 4.3, 2.0, 3, "FOQ", 2.5,   3.40, "SUP-05", 0.0),
    ("CHAIN",        "Chain (shared)",               2, "P", 4.0, 1.8, 2, "EOQ", 0,     4.10, "SUP-02", 0.0),
    ("CRANKSET",     "Crankset (shared)",            2, "P", 4.4, 2.1, 3, "FOQ", 2.5,   9.80, "SUP-02", 0.0),
    ("DERAILLEUR",   "Rear derailleur (shared)",     2, "P", 2.0, 2.8, 5, "FOQ", 3.0,  12.50, "SUP-06", 0.0),   # short

    # ---- Level 3 : raw material -------------------------------------------
    ("ALU-TUBE-STK", "Aluminium tube stock (metre)", 3, "P", 5.0, 2.6, 4, "FOQ", 3.0,   1.80, "SUP-07", 0.0),
    ("STL-TUBE-STK", "Steel tube stock (metre)",     3, "P", 4.6, 2.2, 3, "FOQ", 3.0,   0.95, "SUP-07", 0.0),
    ("PAINT-PWD",    "Powder coat paint (kg)",       3, "P", 4.2, 2.0, 2, "EOQ", 0,     6.40, "SUP-08", 0.0),
]

# ---------------------------------------------------------------------------
# BILL OF MATERIALS  (parent, component, qty per, scrap %)
# 4 levels deep: BIKE -> FRAME -> TUBESET -> ALU/STL stock
# ---------------------------------------------------------------------------
BOM = [
    # ---- Level 0 -> 1 ------------------------------------------------------
    ("BIKE-MTB",  "FRAME-MTB",   1, 0.01),
    ("BIKE-MTB",  "WHEEL-26",    2, 0.01),
    ("BIKE-MTB",  "DRIVE-21S",   1, 0.01),
    ("BIKE-MTB",  "BRAKE-DISC",  1, 0.01),
    ("BIKE-MTB",  "SADDLE-ASSY", 1, 0.01),

    ("BIKE-ROAD", "FRAME-ROAD",  1, 0.01),
    ("BIKE-ROAD", "WHEEL-700C",  2, 0.01),
    ("BIKE-ROAD", "DRIVE-14S",   1, 0.01),
    ("BIKE-ROAD", "BRAKE-RIM",   1, 0.01),
    ("BIKE-ROAD", "SADDLE-ASSY", 1, 0.01),

    ("BIKE-KIDS", "FRAME-KIDS",  1, 0.01),
    ("BIKE-KIDS", "WHEEL-20",    2, 0.01),
    ("BIKE-KIDS", "DRIVE-SS",    1, 0.01),
    ("BIKE-KIDS", "BRAKE-RIM",   1, 0.01),
    ("BIKE-KIDS", "SADDLE-ASSY", 1, 0.01),

    # ---- Level 1 -> 2 ------------------------------------------------------
    ("FRAME-MTB",  "TUBESET-MTB",  1,    0.02),
    ("FRAME-MTB",  "PAINT-PWD",    0.35, 0.05),
    ("FRAME-ROAD", "TUBESET-ROAD", 1,    0.02),
    ("FRAME-ROAD", "PAINT-PWD",    0.30, 0.05),
    ("FRAME-KIDS", "TUBESET-KIDS", 1,    0.02),
    ("FRAME-KIDS", "PAINT-PWD",    0.25, 0.05),

    ("WHEEL-26",   "RIM-26",   1,  0.01),
    ("WHEEL-26",   "HUB-STD",  1,  0.01),
    ("WHEEL-26",   "SPOKE",    32, 0.03),
    ("WHEEL-26",   "TYRE-26",  1,  0.01),
    ("WHEEL-700C", "RIM-700C", 1,  0.01),
    ("WHEEL-700C", "HUB-STD",  1,  0.01),
    ("WHEEL-700C", "SPOKE",    32, 0.03),
    ("WHEEL-700C", "TYRE-700C",1,  0.01),
    ("WHEEL-20",   "RIM-20",   1,  0.01),
    ("WHEEL-20",   "HUB-STD",  1,  0.01),
    ("WHEEL-20",   "SPOKE",    28, 0.03),
    ("WHEEL-20",   "TYRE-20",  1,  0.01),

    ("DRIVE-21S",  "CHAIN",      1, 0.01),
    ("DRIVE-21S",  "CRANKSET",   1, 0.01),
    ("DRIVE-21S",  "DERAILLEUR", 1, 0.01),
    ("DRIVE-14S",  "CHAIN",      1, 0.01),
    ("DRIVE-14S",  "CRANKSET",   1, 0.01),
    ("DRIVE-14S",  "DERAILLEUR", 1, 0.01),
    ("DRIVE-SS",   "CHAIN",      1, 0.01),
    ("DRIVE-SS",   "CRANKSET",   1, 0.01),

    # ---- Level 2 -> 3 ------------------------------------------------------
    ("TUBESET-MTB",  "ALU-TUBE-STK", 4.2, 0.06),
    ("TUBESET-ROAD", "ALU-TUBE-STK", 4.6, 0.06),
    ("TUBESET-KIDS", "STL-TUBE-STK", 3.4, 0.06),
]

# ---------------------------------------------------------------------------
# SUPPLIERS
# ---------------------------------------------------------------------------
SUPPLIERS = {
    "SUP-01": ("BrakeTech Industries",   0.95),
    "SUP-02": ("VeloParts Global",       0.92),
    "SUP-03": ("RimWorks Alloys",        0.88),
    "SUP-04": ("HubSpoke Precision",     0.97),
    "SUP-05": ("RoadGrip Rubber",        0.85),
    "SUP-06": ("ShiftPro Components",    0.80),   # least reliable - scenario hook
    "SUP-07": ("MetalStock Supply Co",   0.93),
    "SUP-08": ("ColourCoat Chemicals",   0.96),
}

# ---------------------------------------------------------------------------
# WORK CENTRES  (Machine_Capacity.csv)
# ---------------------------------------------------------------------------
# Capacity is deliberately tight at WC03, WC04 and WC06 so that the MPS
# feasibility check (5.4) actually fails on the first pass and the "No" branch
# of decision 5.5 has to level-load the schedule. A plant where nothing is ever
# a bottleneck makes for a dull control tower.
WORK_CENTRES = [
    # code, description, machines, hrs/shift, shifts/day, days/week, efficiency
    ("WC01", "Tube Cutting & Mitering", 2,  8, 2, 5, 0.90),   # 144.0 h - ample
    ("WC02", "Frame Welding",           2,  8, 2, 5, 0.85),   # 136.0 h - tight
    ("WC03", "Painting & Curing",       1, 10, 1, 5, 0.90),   #  45.0 h - BOTTLENECK
    ("WC04", "Wheel Building",          2,  8, 2, 5, 0.88),   # 140.8 h - tight
    ("WC05", "Sub-Assembly",            2,  8, 2, 5, 0.90),   # 144.0 h - ample
    ("WC06", "Final Assembly & QC",     2,  8, 2, 5, 0.85),   # 136.0 h - tight
]

# ---------------------------------------------------------------------------
# ROUTINGS  (item, op seq, operation, work centre, setup hrs, run hrs/unit)
# ---------------------------------------------------------------------------
ROUTINGS = [
    ("TUBESET-MTB",  10, "Cut & Miter Tubes",  "WC01", 0.50, 0.100),
    ("TUBESET-ROAD", 10, "Cut & Miter Tubes",  "WC01", 0.60, 0.110),
    ("TUBESET-KIDS", 10, "Cut & Miter Tubes",  "WC01", 0.40, 0.070),

    ("FRAME-MTB",  10, "Weld Frame",   "WC02", 1.00, 0.200),
    ("FRAME-MTB",  20, "Powder Coat",  "WC03", 0.75, 0.080),
    ("FRAME-ROAD", 10, "Weld Frame",   "WC02", 1.20, 0.220),
    ("FRAME-ROAD", 20, "Powder Coat",  "WC03", 0.75, 0.085),
    ("FRAME-KIDS", 10, "Weld Frame",   "WC02", 0.80, 0.140),
    ("FRAME-KIDS", 20, "Powder Coat",  "WC03", 0.60, 0.060),

    ("WHEEL-26",   10, "Lace & True Wheel", "WC04", 0.40, 0.120),
    ("WHEEL-700C", 10, "Lace & True Wheel", "WC04", 0.45, 0.125),
    ("WHEEL-20",   10, "Lace & True Wheel", "WC04", 0.30, 0.090),

    ("DRIVE-21S",  10, "Assemble Drivetrain", "WC05", 0.50, 0.100),
    ("DRIVE-14S",  10, "Assemble Drivetrain", "WC05", 0.50, 0.105),
    ("DRIVE-SS",   10, "Assemble Drivetrain", "WC05", 0.35, 0.070),

    ("BIKE-MTB",   10, "Final Assembly & QC", "WC06", 0.60, 0.250),
    ("BIKE-ROAD",  10, "Final Assembly & QC", "WC06", 0.65, 0.265),
    ("BIKE-KIDS",  10, "Final Assembly & QC", "WC06", 0.45, 0.180),
]

CUSTOMERS = ["CyclePoint Retail", "Metro Sports Chain", "GreenWheel Distributors",
             "Campus Cycles", "ProRide Outlets"]


# ===========================================================================
# DEMAND GENERATION
# ===========================================================================
def generate_demand_history(rng):
    """2.1 Demand_History.csv - 3 products, 3 distinct demand patterns."""
    rows = []
    t = np.arange(1, HISTORY_PERIODS + 1)

    # --- BIKE-MTB : level + upward trend + noise (no seasonality) -----------
    mtb = 120 + 1.8 * t + rng.normal(0, 11, HISTORY_PERIODS)

    # --- BIKE-ROAD : level + mild trend + strong seasonality ----------------
    seasonal = 34 * np.sin(2 * np.pi * (t - 2) / SEASON_LENGTH)
    road = 95 + 0.9 * t + seasonal + rng.normal(0, 7, HISTORY_PERIODS)

    # --- BIKE-KIDS : stationary + noise + two promotional spikes ------------
    kids = 155 + rng.normal(0, 17, HISTORY_PERIODS)
    kids[11] += 95      # period 12 promotion
    kids[27] += 80      # period 28 promotion

    for item, series in [("BIKE-MTB", mtb), ("BIKE-ROAD", road), ("BIKE-KIDS", kids)]:
        for p, v in zip(t, series):
            rows.append({
                "Period": int(p),
                "Week": f"W{int(p):02d}",
                "Item": item,
                "Demand": int(max(0, round(v))),
            })
    return pd.DataFrame(rows)


def generate_customer_orders(rng):
    """2.2 Customer_Orders.csv - confirmed (booked) orders in the future horizon.

    Booked orders taper off further into the horizon, which is realistic and
    makes the MPS max(forecast, orders) logic meaningful.
    """
    rows = []
    oid = 1
    base = {"BIKE-MTB": 175, "BIKE-ROAD": 130, "BIKE-KIDS": 160}
    for p in range(1, HORIZON_PERIODS + 1):
        # order book coverage decays with distance into the horizon
        coverage = max(0.10, 0.95 - 0.075 * (p - 1))
        for item in FINISHED:
            total = base[item] * coverage
            n_orders = rng.integers(1, 4)
            if total < 5:
                continue
            splits = rng.dirichlet(np.ones(n_orders)) * total
            for q in splits:
                q = int(round(q))
                if q <= 0:
                    continue
                rows.append({
                    "OrderID": f"SO-{oid:04d}",
                    "Item": item,
                    "Period": p,
                    "Quantity": q,
                    "Customer": CUSTOMERS[rng.integers(0, len(CUSTOMERS))],
                    "Priority": ["High", "Normal", "Normal", "Low"][rng.integers(0, 4)],
                })
                oid += 1
    return pd.DataFrame(rows)


def compute_weekly_usage(demand_df):
    """Roll average finished-goods demand down the BOM to per-item weekly usage.

    Uses the mean of the last 12 periods of history (more representative of the
    current run rate than the full 36 periods when a trend is present).
    Scrap is applied the same way the MRP engine applies it:
        issued = required / (1 - scrap)
    """
    recent = demand_df[demand_df.Period > HISTORY_PERIODS - 12]
    usage = recent.groupby("Item")["Demand"].mean().to_dict()

    level_of = {i[0]: i[2] for i in ITEMS}
    children = {}
    for parent, comp, qty, scrap in BOM:
        children.setdefault(parent, []).append((comp, qty, scrap))

    # process parents in ascending BOM level so every parent's usage is final
    # before it is exploded into its children
    for lvl in sorted(set(level_of.values())):
        for parent in [i for i, l in level_of.items() if l == lvl]:
            pu = usage.get(parent, 0.0)
            for comp, qty, scrap in children.get(parent, []):
                usage[comp] = usage.get(comp, 0.0) + pu * qty / (1.0 - scrap)
    return usage


def _nice(x):
    """Round to a readable number - real item masters do not hold 4173.62 units."""
    if x <= 0:
        return 0
    if x < 100:
        return int(round(x / 5.0) * 5)
    if x < 1000:
        return int(round(x / 10.0) * 10)
    if x < 10000:
        return int(round(x / 50.0) * 50)
    return int(round(x / 500.0) * 500)


def generate_inventory_master(usage):
    """2.3 Inventory_Master.csv - stock levels derived from rolled-up usage."""
    rows = []
    for (item, desc, lvl, src, oh_w, ss_w, lt, rule, foq_w, cost, sup, scrap) in ITEMS:
        u = usage.get(item, 0.0)
        rows.append({
            "Item": item,
            "Description": desc,
            "BOMLevel": lvl,
            "SourceType": src,               # M = make, P = purchase
            "UOM": "M" if item.endswith("STK") or item == "PAINT-PWD" else "EA",
            "WeeklyUsage": round(u, 1),
            "OnHand": _nice(oh_w * u),
            "SafetyStock": _nice(ss_w * u),
            "LeadTime": lt,                  # in periods (weeks)
            "LotSizeRule": rule,             # LFL / FOQ / EOQ
            "LotSizeValue": _nice(foq_w * u) if rule == "FOQ" else 0,
            "UnitCost": cost,
            "OrderingCost": 250 if src == "P" else 180,
            "HoldingCostRate": 0.22,         # annual, fraction of unit cost
            "Supplier": sup,
            "ScrapPct": scrap,
        })
    return pd.DataFrame(rows)


def generate_bom():
    """2.4 BOM.csv - multi level, minimum 3 levels (this one is 4)."""
    return pd.DataFrame(
        [{"ParentItem": p, "ComponentItem": c, "QtyPer": q, "ScrapPct": s}
         for (p, c, q, s) in BOM]
    )


def generate_supplier_leadtime(inv):
    """2.5 Supplier_LeadTime.csv"""
    rows = []
    for _, r in inv[inv.SourceType == "P"].iterrows():
        sup = r["Supplier"]
        name, rel = SUPPLIERS[sup]
        moq = int(r["LotSizeValue"]) if r["LotSizeValue"] > 0 else int(max(50, r["SafetyStock"] * 0.4))
        rows.append({
            "Supplier": sup,
            "SupplierName": name,
            "Item": r["Item"],
            "LeadTime": int(r["LeadTime"]),
            "Reliability": rel,
            "MinOrderQty": moq,
            "UnitPrice": r["UnitCost"],
        })
    return pd.DataFrame(rows)


def generate_mps_template():
    """2.6 MPS.csv - initial / blank template, filled in by Block 5."""
    rows = []
    for item in FINISHED:
        for p in range(1, HORIZON_PERIODS + 1):
            rows.append({"Item": item, "Period": p, "MPSQty": 0})
    return pd.DataFrame(rows)


def generate_routing():
    """2.7 Routing_WorkCentre.csv"""
    return pd.DataFrame(
        [{"Item": i, "OpSeq": s, "Operation": o, "WorkCentre": w,
          "SetupTimeHrs": st, "RunTimeHrsPerUnit": rt}
         for (i, s, o, w, st, rt) in ROUTINGS]
    )


def generate_machine_capacity():
    """2.8 Machine_Capacity.csv"""
    rows = []
    for (wc, desc, m, hps, spd, dpw, eff) in WORK_CENTRES:
        rows.append({
            "WorkCentre": wc,
            "Description": desc,
            "NumMachines": m,
            "HoursPerShift": hps,
            "ShiftsPerDay": spd,
            "DaysPerWeek": dpw,
            "Efficiency": eff,
            "AvailableHoursPerPeriod": round(m * hps * spd * dpw * eff, 2),
        })
    return pd.DataFrame(rows)


def generate_production_orders_template():
    """2.9 Production_Orders.csv - template, generated by the system in Block 8."""
    return pd.DataFrame(columns=[
        "OrderID", "Item", "Quantity", "ReleasePeriod", "DueperiodPeriod",
        "Status", "ConstraintType", "Notes"
    ])


# ===========================================================================
def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    rng = np.random.default_rng(SEED)

    demand    = generate_demand_history(rng)
    orders    = generate_customer_orders(rng)
    usage     = compute_weekly_usage(demand)
    inventory = generate_inventory_master(usage)
    bom       = generate_bom()
    supplier  = generate_supplier_leadtime(inventory)
    mps       = generate_mps_template()
    routing   = generate_routing()
    capacity  = generate_machine_capacity()
    prodord   = generate_production_orders_template()

    files = {
        "Demand_History.csv":     demand,
        "Customer_Orders.csv":    orders,
        "Inventory_Master.csv":   inventory,
        "BOM.csv":                bom,
        "Supplier_LeadTime.csv":  supplier,
        "MPS.csv":                mps,
        "Routing_WorkCentre.csv": routing,
        "Machine_Capacity.csv":   capacity,
        "Production_Orders.csv":  prodord,
    }

    print("=" * 74)
    print("BLOCK 2 - PREPARE INPUT DATASET FILES")
    print("=" * 74)
    for name, df in files.items():
        path = os.path.join(DATA_DIR, name)
        df.to_csv(path, index=False)
        print(f"  {name:<26} {len(df):>6} rows  x {len(df.columns)} cols")

    # ---- validation (the 'All files validated' node in Block 2) -----------
    print("-" * 74)
    errors = validate(demand, orders, inventory, bom, routing, capacity)
    if errors:
        for e in errors:
            print(f"  [FAIL] {e}")
        raise SystemExit(1)
    print("  [OK] All files validated and ready for import into the system.")
    print(f"  Items: {len(inventory)} | BOM links: {len(bom)} | "
          f"Max BOM level: {inventory.BOMLevel.max()} | Work centres: {len(capacity)}")
    print("=" * 74)


def validate(demand, orders, inventory, bom, routing, capacity):
    """Referential-integrity checks across the nine files."""
    errs = []
    items = set(inventory.Item)

    for col, df, name in [("Item", demand, "Demand_History"),
                          ("Item", orders, "Customer_Orders"),
                          ("Item", routing, "Routing_WorkCentre")]:
        unknown = set(df[col]) - items
        if unknown:
            errs.append(f"{name}: unknown items {sorted(unknown)}")

    unknown = (set(bom.ParentItem) | set(bom.ComponentItem)) - items
    if unknown:
        errs.append(f"BOM: unknown items {sorted(unknown)}")

    unknown_wc = set(routing.WorkCentre) - set(capacity.WorkCentre)
    if unknown_wc:
        errs.append(f"Routing: unknown work centres {sorted(unknown_wc)}")

    # every 'make' item must have a routing
    make_items = set(inventory[inventory.SourceType == "M"].Item)
    missing_rt = make_items - set(routing.Item)
    if missing_rt:
        errs.append(f"Make items without routing: {sorted(missing_rt)}")

    # every 'make' item except level 3 should have a BOM
    make_no_bom = make_items - set(bom.ParentItem)
    if make_no_bom:
        errs.append(f"Make items without BOM: {sorted(make_no_bom)}")

    # demand history length
    for item, g in demand.groupby("Item"):
        if len(g) < 24:
            errs.append(f"{item}: only {len(g)} periods of history (need >= 24)")

    # BOM must be acyclic
    parents = {}
    for _, r in bom.iterrows():
        parents.setdefault(r.ParentItem, []).append(r.ComponentItem)

    def has_cycle(node, stack):
        if node in stack:
            return True
        for c in parents.get(node, []):
            if has_cycle(c, stack | {node}):
                return True
        return False

    for p in parents:
        if has_cycle(p, set()):
            errs.append(f"BOM contains a cycle involving {p}")
            break

    return errs


if __name__ == "__main__":
    main()

"""
DATA IMPORT / EXPORT
==============================================================================
Lets the control tower run on YOUR data, whatever your columns are called.

Import is a three-step flow, so a file never has to be reshaped by hand:

    1. Read      CSV or Excel, any column names, any sheet
    2. Map       your columns are matched to the fields the planner needs.
                 The match is guessed for you ("Part No" -> Item, "Qty" ->
                 Quantity) and you can correct any of it.
    3. Fill      anything you do not have is filled with a documented default,
                 or derived from the other files. Only a handful of fields are
                 genuinely essential.

`data/`         the built-in sample dataset, never modified
`data_active/`  the live working copy the planner actually reads

Uploading replaces just that one file in the working copy, so you can bring
your own bill of materials while keeping the sample demand history. "Reset to
sample data" restores the originals.
"""

import difflib
import io
import os
import re
import shutil
import zipfile

import numpy as np
import pandas as pd

from engine.config import BASE_DIR, DATA_DIR

ACTIVE_DIR = os.path.join(BASE_DIR, "data_active")

# ---------------------------------------------------------------------------
# The nine input files, in the order the flow chart lists them (Block 2).
#
#   essential : without this the file is meaningless
#   optional  : {field: (default, why)} - filled in when you do not supply it
#   aliases   : common real-world spellings, used to guess the mapping
# ---------------------------------------------------------------------------
FILES = [
    {
        "step": "2.1", "key": "demand", "file": "Demand_History.csv",
        "label": "Demand History",
        "help": "Past sales, one row per product per period. At least 12 periods, "
                "ideally 24 or more.",
        "essential": ["Item", "Period", "Demand"],
        "optional": {"Week": ("P{period}", "a readable period label")},
        "aliases": {
            "Item": ["item", "sku", "product", "partno", "partnumber", "material",
                     "itemcode", "productcode", "code", "part"],
            "Period": ["period", "week", "month", "date", "t", "timebucket", "bucket",
                       "wk", "weekending", "periodend", "yearweek", "fiscalweek",
                       "salesdate", "postingdate"],
            "Demand": ["demand", "qty", "quantity", "sales", "units", "volume",
                       "actual", "actualdemand", "soldqty"],
        },
    },
    {
        "step": "2.2", "key": "orders", "file": "Customer_Orders.csv",
        "label": "Customer Orders",
        "help": "Orders already confirmed, by product and period.",
        "essential": ["Item", "Period", "Quantity"],
        "optional": {"OrderID": ("SO-{n}", "generated if absent"),
                     "Customer": ("Customer", "unknown customer"),
                     "Priority": ("Normal", "all orders treated equally")},
        "aliases": {
            "Item": ["item", "sku", "product", "partno", "material", "code"],
            "Period": ["period", "week", "duedate", "dueperiod", "month", "date",
                       "wk", "requireddate", "deliverydate", "shipdate", "orderdate"],
            "Quantity": ["quantity", "qty", "orderqty", "units", "amount", "volume"],
            "OrderID": ["orderid", "order", "orderno", "ordernumber", "so", "reference"],
            "Customer": ["customer", "client", "account", "customername"],
            "Priority": ["priority", "urgency", "class"],
        },
    },
    {
        "step": "2.3", "key": "inventory", "file": "Inventory_Master.csv",
        "label": "Inventory Master",
        "help": "One row per part. Only the item code, stock on hand and lead time "
                "are essential — everything else has a sensible default.",
        "essential": ["Item", "OnHand", "LeadTime"],
        "optional": {
            "Description":     ("{item}", "the item code is used as its own description"),
            "BOMLevel":        (0, "derived from the bill of materials"),
            "SourceType":      ("auto", "M if the item has components, otherwise P"),
            "UOM":             ("EA", "each"),
            "SafetyStock":     (0, "no buffer stock"),
            "LotSizeRule":     ("LFL", "lot-for-lot: order exactly what is needed"),
            "LotSizeValue":    (0, "not used by lot-for-lot"),
            "UnitCost":        (1.0, "so inventory value and EOQ still compute"),
            "OrderingCost":    (200.0, "typical cost of raising one order"),
            "HoldingCostRate": (0.22, "22% of unit cost per year"),
            "Supplier":        ("-", "no supplier recorded"),
            "ScrapPct":        (0.0, "no scrap allowance"),
        },
        "aliases": {
            "Item": ["item", "sku", "partno", "partnumber", "material", "code", "product"],
            "Description": ["description", "name", "itemname", "desc", "materialdesc"],
            "OnHand": ["onhand", "stock", "qtyonhand", "inventory", "currentstock",
                       "availableqty", "soh", "stockonhand", "balance"],
            "SafetyStock": ["safetystock", "ss", "buffer", "minstock", "minimum",
                            "reorderlevel", "safety"],
            "LeadTime": ["leadtime", "lt", "leadtimeweeks", "leadtimedays",
                         "replenishmentleadtime", "delivery"],
            "LotSizeRule": ["lotsizerule", "lotrule", "orderpolicy", "policy", "sizingrule"],
            "LotSizeValue": ["lotsizevalue", "lotsize", "orderqty", "fixedqty",
                             "batchsize", "moq"],
            "UnitCost": ["unitcost", "cost", "price", "standardcost", "unitprice", "value"],
            "OrderingCost": ["orderingcost", "setupcost", "ordercost"],
            "HoldingCostRate": ["holdingcostrate", "carryingcost", "holdingrate"],
            "Supplier": ["supplier", "vendor", "supplierid", "vendorcode", "source"],
            "ScrapPct": ["scrappct", "scrap", "yieldloss", "shrinkage", "wastage"],
            "BOMLevel": ["bomlevel", "level", "lowlevelcode"],
            "SourceType": ["sourcetype", "makebuy", "procurementtype", "type", "source"],
        },
    },
    {
        "step": "2.4", "key": "bom", "file": "BOM.csv",
        "label": "Bill of Materials",
        "help": "The recipe: which component goes into which parent, and how many.",
        "essential": ["ParentItem", "ComponentItem", "QtyPer"],
        "optional": {"ScrapPct": (0.0, "no scrap allowance on this link")},
        "aliases": {
            "ParentItem": ["parentitem", "parent", "assembly", "parentsku", "topitem",
                           "parentpart", "father", "parentcode"],
            "ComponentItem": ["componentitem", "component", "child", "childitem",
                              "material", "componentsku", "childpart", "componentcode"],
            "QtyPer": ["qtyper", "quantity", "qty", "usage", "qtyperparent", "perunit",
                       "componentqty", "usagequantity"],
            "ScrapPct": ["scrappct", "scrap", "yieldloss", "shrinkage", "wastage"],
        },
    },
    {
        "step": "2.5", "key": "supplier", "file": "Supplier_LeadTime.csv",
        "label": "Supplier Lead Times",
        "help": "Which supplier provides which part. Lead time falls back to the "
                "inventory master if you do not give one here.",
        "essential": ["Supplier", "Item"],
        "optional": {"SupplierName": ("{supplier}", "the code is used as the name"),
                     "LeadTime": (0, "taken from the inventory master"),
                     "Reliability": (0.9, "90% on-time assumed"),
                     "MinOrderQty": (0, "no minimum"),
                     "UnitPrice": (0.0, "taken from the inventory master")},
        "aliases": {
            "Supplier": ["supplier", "vendor", "supplierid", "vendorcode", "supplierc0de"],
            "SupplierName": ["suppliername", "vendorname", "name"],
            "Item": ["item", "sku", "partno", "material", "code", "product"],
            "LeadTime": ["leadtime", "lt", "deliverytime", "leadtimeweeks"],
            "Reliability": ["reliability", "otd", "ontime", "servicelevel", "performance"],
            "MinOrderQty": ["minorderqty", "moq", "minimumorder", "minqty"],
            "UnitPrice": ["unitprice", "price", "cost", "unitcost"],
        },
    },
    {
        "step": "2.6", "key": "mps", "file": "MPS.csv",
        "label": "MPS Template",
        "help": "Starting master schedule. Blank is fine — Block 5 fills it in.",
        "essential": ["Item", "Period"],
        "optional": {"MPSQty": (0, "Block 5 calculates the quantity")},
        "aliases": {
            "Item": ["item", "sku", "product", "partno", "code"],
            "Period": ["period", "week", "month", "bucket", "date", "wk"],
            "MPSQty": ["mpsqty", "qty", "quantity", "plannedqty", "mps", "buildqty"],
        },
    },
    {
        "step": "2.7", "key": "routing", "file": "Routing_WorkCentre.csv",
        "label": "Routing / Work Centre",
        "help": "Which work centre performs which operation for each made item, "
                "and how long it takes.",
        "essential": ["Item", "WorkCentre"],
        "optional": {"OpSeq": ("auto", "numbered 10, 20, 30 in file order"),
                     "Operation": ("{workcentre}", "the work centre name is used"),
                     "SetupTimeHrs": (0.0, "no setup time"),
                     "RunTimeHrsPerUnit": (0.1, "6 minutes per unit")},
        "aliases": {
            "Item": ["item", "sku", "product", "partno", "material", "code"],
            "WorkCentre": ["workcentre", "workcenter", "wc", "machine", "resource",
                           "workstation", "cell", "line"],
            "OpSeq": ["opseq", "sequence", "operationno", "step", "opno", "seq", "order"],
            "Operation": ["operation", "operationname", "task", "activity", "process",
                          "description"],
            "SetupTimeHrs": ["setuptimehrs", "setuptime", "setup", "setuphours", "changeover"],
            "RunTimeHrsPerUnit": ["runtimehrsperunit", "runtime", "cycletime", "timeperunit",
                                  "processtime", "unittime", "runhours"],
        },
    },
    {
        "step": "2.8", "key": "capacity", "file": "Machine_Capacity.csv",
        "label": "Machine Capacity",
        "help": "How much time each work centre offers. Only the work centre code is "
                "essential — a single machine on one 8-hour shift is assumed.",
        "essential": ["WorkCentre"],
        "optional": {"Description": ("{workcentre}", "the code is used as the name"),
                     "NumMachines": (1, "one machine"),
                     "HoursPerShift": (8, "8-hour shift"),
                     "ShiftsPerDay": (1, "one shift per day"),
                     "DaysPerWeek": (5, "five working days"),
                     "Efficiency": (0.85, "85% effective")},
        "aliases": {
            "WorkCentre": ["workcentre", "workcenter", "wc", "machine", "resource",
                           "workstation", "cell", "line"],
            "Description": ["description", "name", "workcentrename", "desc"],
            "NumMachines": ["nummachines", "machines", "machinecount", "capacityunits",
                            "resources", "noofmachines", "qty"],
            "HoursPerShift": ["hourspershift", "shifthours", "hours"],
            "ShiftsPerDay": ["shiftsperday", "shifts", "noofshifts"],
            "DaysPerWeek": ["daysperweek", "workingdays", "days"],
            "Efficiency": ["efficiency", "utilisation", "utilization", "oee",
                           "effectiveness", "performance"],
        },
    },
    {
        "step": "2.9", "key": "prodorders", "file": "Production_Orders.csv",
        "label": "Production Orders",
        "help": "Template only — the system generates this in Block 8.",
        "essential": [], "optional": {}, "aliases": {},
    },
]

BY_KEY = {f["key"]: f for f in FILES}

# every column the engine ultimately expects, in order
SCHEMA = {f["key"]: f["essential"] + list(f["optional"]) for f in FILES}

NUMERIC = {
    "demand":    ["Period", "Demand"],
    "orders":    ["Period", "Quantity"],
    "inventory": ["BOMLevel", "OnHand", "SafetyStock", "LeadTime", "LotSizeValue",
                  "UnitCost", "OrderingCost", "HoldingCostRate", "ScrapPct"],
    "bom":       ["QtyPer", "ScrapPct"],
    "supplier":  ["LeadTime", "Reliability", "MinOrderQty", "UnitPrice"],
    "mps":       ["Period", "MPSQty"],
    "routing":   ["OpSeq", "SetupTimeHrs", "RunTimeHrsPerUnit"],
    "capacity":  ["NumMachines", "HoursPerShift", "ShiftsPerDay", "DaysPerWeek",
                  "Efficiency"],
}



# ---------------------------------------------------------------------------
# Turning real-world spreadsheet values into numbers
# ---------------------------------------------------------------------------
def to_number(series):
    """Coerce a column exported from a real system into numbers.

    Handles the things spreadsheets and ERP exports actually contain:
    thousands separators ("1,200"), currency symbols ("$12.50", "GBP 4"),
    percentages ("85%"), trailing units ("3 wks"), parenthesised negatives
    ("(50)") and stray whitespace.
    """
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    t = series.astype(str).str.strip()
    neg = t.str.match(r"^\(.*\)$")                      # (50) means -50
    t = t.str.replace(r"^\((.*)\)$", r"\1", regex=True)
    t = (t.str.replace(r"[^\d.\-eE]", "", regex=True)   # drop £ $ , % letters
          .str.replace(r"(?<=.)-", "", regex=True))       # keep only a leading -
    out = pd.to_numeric(t.replace({"": None, "-": None, ".": None}), errors="coerce")
    return out.where(~neg, -out)


def to_period(series):
    """Turn whatever identifies a time bucket into 1, 2, 3 ...

    Accepts plain numbers, labels like "Week 1" / "W03" / "P7", and real dates.
    Dates are ranked in order, so twelve weekly dates become periods 1 to 12 -
    which is what the planner needs, and what a date column always means.
    """
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    t = series.astype(str).str.strip()

    digits = pd.to_numeric(t.str.extract(r"(\d+)", expand=False), errors="coerce")
    looks_like_date = t.str.contains(r"[/\-]\s*\d|\d\s*[/\-]", regex=True, na=False)
    if digits.notna().all() and not looks_like_date.any():
        return digits

    parsed = pd.to_datetime(t, errors="coerce", format="mixed", dayfirst=False)
    if parsed.notna().mean() > 0.8:
        return parsed.rank(method="dense").astype("Int64")

    return digits


# ===========================================================================
# Reading a file of any shape
# ===========================================================================
def sheet_names(uploaded_file):
    """Sheet list for an Excel upload; empty for CSV."""
    name = getattr(uploaded_file, "name", "") or ""
    if not name.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return []
    try:
        uploaded_file.seek(0)
        return pd.ExcelFile(uploaded_file).sheet_names
    except Exception:                                        # noqa: BLE001
        return []


def read_any(uploaded_file, sheet=None):
    """Read a CSV or Excel upload into a DataFrame. Returns (df, error)."""
    name = (getattr(uploaded_file, "name", "") or "").lower()
    try:
        uploaded_file.seek(0)
    except Exception:                                        # noqa: BLE001
        pass

    try:
        if name.endswith((".xlsx", ".xlsm", ".xls")):
            df = pd.read_excel(uploaded_file, sheet_name=sheet or 0)
        elif name.endswith((".tsv", ".txt")):
            df = pd.read_csv(uploaded_file, sep=None, engine="python")
        else:
            df = pd.read_csv(uploaded_file)
    except Exception as e:                                   # noqa: BLE001
        return None, f"Could not read that file — {e}"

    if df is None or df.empty:
        return None, "The file has no rows."

    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    if df.empty:
        return None, "The file has no usable rows."
    return df, None


# ===========================================================================
# Guessing the column mapping
# ===========================================================================
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def suggest_mapping(key, columns):
    """Guess which of the user's columns feeds each field the planner needs.

    Exact match on the normalised name first, then the alias list, then a fuzzy
    match. Returns {field: user_column or None}.
    """
    spec = BY_KEY[key]
    fields = spec["essential"] + list(spec["optional"])
    norm_cols = {_norm(c): c for c in columns}
    used, mapping = set(), {}

    for field in fields:
        found = None
        candidates = [_norm(field)] + [_norm(a) for a in spec["aliases"].get(field, [])]

        for cand in candidates:                              # exact, then contains
            if cand in norm_cols and norm_cols[cand] not in used:
                found = norm_cols[cand]
                break
        if not found:
            for cand in candidates:
                for nc, orig in norm_cols.items():
                    if orig in used:
                        continue
                    if nc == cand or (len(cand) > 3 and cand in nc):
                        found = orig
                        break
                if found:
                    break
        if not found:                                        # fuzzy last resort
            pool = [nc for nc, o in norm_cols.items() if o not in used]
            close = difflib.get_close_matches(_norm(field), pool, n=1, cutoff=0.82)
            if close:
                found = norm_cols[close[0]]

        mapping[field] = found
        if found:
            used.add(found)
    return mapping


# ===========================================================================
# Applying the mapping and filling the gaps
# ===========================================================================
def apply_mapping(key, df, mapping):
    """Rename the user's columns and fill every field they did not supply.

    Returns (clean_df, notes) where notes explains each default that was used,
    so nothing is silently invented.
    """
    spec = BY_KEY[key]
    out = pd.DataFrame(index=range(len(df)))
    notes = []

    for field in spec["essential"]:
        src = mapping.get(field)
        out[field] = df[src].values if src and src in df.columns else np.nan

    for field, (default, why) in spec["optional"].items():
        src = mapping.get(field)
        if src and src in df.columns:
            out[field] = df[src].values
            continue

        if default == "auto" and field == "OpSeq":
            out[field] = [(i + 1) * 10 for i in range(len(df))]
        elif default == "auto" and field == "SourceType":
            out[field] = "P"                                 # corrected in finalise()
        elif isinstance(default, str) and default.startswith("{"):
            token = default.strip("{}")
            source = {"item": "Item", "supplier": "Supplier",
                      "workcentre": "WorkCentre", "period": "Period"}.get(token)
            if source and source in out.columns:
                out[field] = out[source].astype(str)
                if token == "period":
                    out[field] = "P" + out[source].astype(str)
            else:
                out[field] = ""
        elif default == "SO-{n}":
            out[field] = [f"SO-{i + 1:04d}" for i in range(len(df))]
        else:
            out[field] = default
        notes.append(f"**{field}** — {why}")

    # numeric coercion, tolerant of real-world formatting
    if "Period" in out.columns:
        before = out["Period"].copy()
        out["Period"] = to_period(out["Period"])
        if (not pd.api.types.is_numeric_dtype(before)) and out["Period"].notna().any():
            notes.append("**Period** — converted your labels/dates into "
                         "numbered periods 1, 2, 3 …")
    for col in NUMERIC.get(key, []):
        if col in out.columns and col != "Period":
            raw_col = out[col]
            out[col] = to_number(raw_col)
            if (not pd.api.types.is_numeric_dtype(raw_col)) and out[col].notna().any():
                notes.append(f"**{col}** — stripped symbols and separators to read "
                             f"the numbers")

    # a percentage given as 85 rather than 0.85 is the commonest import mistake
    for col in ("Efficiency", "Reliability", "HoldingCostRate", "ScrapPct"):
        if col in out.columns and pd.api.types.is_numeric_dtype(out[col]):
            if out[col].max(skipna=True) is not np.nan and out[col].max(skipna=True) > 1.5:
                out[col] = out[col] / 100.0
                notes.append(f"**{col}** — looked like a percentage, divided by 100")

    # a label templated on the period can only be built once Period is numeric
    for col in out.columns:
        if out[col].astype(str).eq("P{period}").all() and "Period" in out.columns:
            out[col] = "P" + out["Period"].astype("Int64").astype(str)

    if key == "capacity":
        out["AvailableHoursPerPeriod"] = (
            out["NumMachines"] * out["HoursPerShift"] * out["ShiftsPerDay"]
            * out["DaysPerWeek"] * out["Efficiency"]).round(2)

    return out, notes


def validate(key, df):
    """Check a mapped table. Returns a list of readable problems."""
    spec = BY_KEY[key]
    problems = []
    if not spec["essential"]:
        return problems

    for field in spec["essential"]:
        if field not in df.columns:
            problems.append(f"No column mapped to **{field}**.")
        elif df[field].isna().all():
            problems.append(f"**{field}** is empty in every row.")
        elif df[field].isna().any():
            n = int(df[field].isna().sum())
            problems.append(f"**{field}** is blank in {n} row(s).")
    if problems:
        return problems

    for col in NUMERIC.get(key, []):
        if col in df.columns and df[col].isna().any():
            n = int(df[col].isna().sum())
            problems.append(f"**{col}** has {n} value(s) that are not numbers.")

    if key == "inventory":
        bad = set(df["SourceType"].dropna().astype(str).str.upper()) - {"M", "P", "AUTO"}
        if bad:
            problems.append(f"SourceType must be M (make) or P (purchase). Found: {sorted(bad)}.")
        bad = set(df["LotSizeRule"].dropna().astype(str).str.upper()) - {"LFL", "FOQ", "EOQ", "POQ"}
        if bad:
            problems.append(f"LotSizeRule must be LFL, FOQ or EOQ. Found: {sorted(bad)}.")
        dup = df["Item"].duplicated()
        if dup.any():
            problems.append(f"{int(dup.sum())} duplicate item code(s), e.g. "
                            f"{df.loc[dup, 'Item'].iloc[0]}.")
    if key == "bom":
        self_ref = df[df["ParentItem"].astype(str) == df["ComponentItem"].astype(str)]
        if not self_ref.empty:
            problems.append(f"{len(self_ref)} row(s) list an item as its own component "
                            f"(e.g. {self_ref.iloc[0]['ParentItem']}).")
    if key == "capacity":
        dup = df["WorkCentre"].duplicated()
        if dup.any():
            problems.append(f"{int(dup.sum())} duplicate work centre code(s).")
    return problems


def preview_mapping(key, df, mapping):
    """The mapped table plus its notes and problems, without saving anything."""
    clean, notes = apply_mapping(key, df, mapping)
    return clean, notes, validate(key, clean)


def commit(key, clean_df):
    """Store an already-mapped, already-validated table."""
    ensure_active()
    clean_df.to_csv(os.path.join(ACTIVE_DIR, BY_KEY[key]["file"]), index=False)
    return True, (f"{BY_KEY[key]['label']} replaced — {len(clean_df):,} rows, "
                  f"{len(clean_df.columns)} columns.")


def accept_upload(key, uploaded_file, mapping=None, sheet=None):
    """One-shot import: read, auto-map, validate, store. Returns (ok, message)."""
    df, err = read_any(uploaded_file, sheet)
    if err:
        return False, err
    mapping = mapping or suggest_mapping(key, list(df.columns))
    clean, _notes, problems = preview_mapping(key, df, mapping)
    if problems:
        return False, " ".join(problems)
    return commit(key, clean)


# ===========================================================================
# Deriving what can be derived from the other files
# ===========================================================================
def finalise(data):
    """Fill fields that can only be worked out once every file is present.

    `BOMLevel` and `SourceType` are derived from the bill of materials, so a
    user importing an item list never has to classify make-vs-buy by hand.
    """
    inv, bom = data["inventory"], data["bom"]

    parents = set(bom["ParentItem"].astype(str))
    if "SourceType" in inv.columns:
        auto = inv["SourceType"].astype(str).str.upper().isin(["AUTO", "NAN", ""])
        inv.loc[auto, "SourceType"] = np.where(
            inv.loc[auto, "Item"].astype(str).isin(parents), "M", "P")

    if "BOMLevel" in inv.columns and (inv["BOMLevel"].fillna(0) == 0).all() and not bom.empty:
        children = {}
        for _, r in bom.iterrows():
            children.setdefault(str(r["ParentItem"]), []).append(str(r["ComponentItem"]))
        all_items = parents | set(bom["ComponentItem"].astype(str))
        roots = [i for i in all_items
                 if i not in set(bom["ComponentItem"].astype(str))]
        level = {}

        def walk(node, depth, seen):
            if node in seen or depth > 25:
                return
            level[node] = max(level.get(node, 0), depth)
            for c in children.get(node, []):
                walk(c, depth + 1, seen | {node})

        for r in roots:
            walk(r, 0, set())
        inv["BOMLevel"] = inv["Item"].astype(str).map(level).fillna(0).astype(int)

    if "supplier" in data and not data["supplier"].empty:
        s = data["supplier"]
        lt = inv.set_index("Item")["LeadTime"].to_dict()
        cost = inv.set_index("Item")["UnitCost"].to_dict()
        if "LeadTime" in s.columns:
            zero = pd.to_numeric(s["LeadTime"], errors="coerce").fillna(0) <= 0
            s.loc[zero, "LeadTime"] = s.loc[zero, "Item"].map(lt).fillna(1)
        if "UnitPrice" in s.columns:
            zero = pd.to_numeric(s["UnitPrice"], errors="coerce").fillna(0) <= 0
            s.loc[zero, "UnitPrice"] = s.loc[zero, "Item"].map(cost).fillna(0)

    data["inventory"], data["bom"] = inv, bom
    return data


def load_plan_data():
    """Load the working copy and derive everything derivable."""
    from engine import io_utils
    ensure_active()
    return finalise(io_utils.load_all(ACTIVE_DIR))


# ===========================================================================
# The working copy
# ===========================================================================
def ensure_active():
    os.makedirs(ACTIVE_DIR, exist_ok=True)
    missing = [f for f in FILES
               if not os.path.exists(os.path.join(ACTIVE_DIR, f["file"]))]
    if not missing:
        return
    if not os.path.exists(os.path.join(DATA_DIR, FILES[0]["file"])):
        import generate_data
        generate_data.main()
    for f in missing:
        src = os.path.join(DATA_DIR, f["file"])
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(ACTIVE_DIR, f["file"]))


def reset_to_sample():
    if os.path.exists(ACTIVE_DIR):
        shutil.rmtree(ACTIVE_DIR)
    ensure_active()


def fingerprint():
    parts = []
    for f in FILES:
        path = os.path.join(ACTIVE_DIR, f["file"])
        if os.path.exists(path):
            st = os.stat(path)
            parts.append(f"{f['key']}:{st.st_size}:{int(st.st_mtime)}")
    return "|".join(parts)


def is_custom(key):
    a = os.path.join(ACTIVE_DIR, BY_KEY[key]["file"])
    b = os.path.join(DATA_DIR, BY_KEY[key]["file"])
    if not (os.path.exists(a) and os.path.exists(b)):
        return False
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() != fb.read()
    except OSError:
        return False


def read_active(key):
    path = os.path.join(ACTIVE_DIR, BY_KEY[key]["file"])
    return pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()


def cross_check(data):
    """Whole-dataset checks that only make sense once all files are loaded."""
    problems = []
    items = set(data["inventory"]["Item"].astype(str))

    for key, col, name in [("demand", "Item", "Demand History"),
                           ("orders", "Item", "Customer Orders"),
                           ("routing", "Item", "Routing")]:
        if data[key].empty or col not in data[key].columns:
            continue
        unknown = set(data[key][col].astype(str)) - items
        if unknown:
            problems.append(f"{name} refers to {len(unknown)} item(s) not in the "
                            f"Inventory Master: {sorted(unknown)[:5]}")

    if not data["bom"].empty:
        unknown = (set(data["bom"]["ParentItem"].astype(str))
                   | set(data["bom"]["ComponentItem"].astype(str))) - items
        if unknown:
            problems.append(f"The bill of materials refers to {len(unknown)} unknown "
                            f"item(s): {sorted(unknown)[:5]}")

    if not data["routing"].empty:
        unknown_wc = set(data["routing"]["WorkCentre"].astype(str)) - \
            set(data["capacity"]["WorkCentre"].astype(str))
        if unknown_wc:
            problems.append(f"Routing uses work centre(s) with no capacity defined: "
                            f"{sorted(unknown_wc)}")

    made = set(data["inventory"][data["inventory"]["SourceType"] == "M"]["Item"].astype(str))
    no_routing = made - set(data["routing"]["Item"].astype(str))
    if no_routing:
        problems.append(f"{len(no_routing)} made item(s) have no routing, so they "
                        f"cannot be scheduled: {sorted(no_routing)[:5]}")

    if not data["demand"].empty:
        for item, g in data["demand"].groupby("Item"):
            if len(g) < 12:
                problems.append(f"{item} has only {len(g)} periods of history — "
                                f"forecasting needs at least 12.")
    return problems


# ===========================================================================
# Export
# ===========================================================================
def output_tables(result):
    return {
        "Forecast":             result["forecast"],
        "Forecast Accuracy":    result["forecast_accuracy"],
        "Forecast Selection":   result["forecast_selection"],
        "Inventory Params":     result["inventory_params"],
        "Inventory Projection": result["inventory_projection"],
        "MPS":                  result["mps"],
        "Capacity Plan":        result["capacity_plan"],
        "Gross Requirements":   result["gross_requirements"],
        "MRP Output":           result["mrp_output"],
        "Planned Releases":     result["releases"],
        "Production Orders":    result["production_orders"],
        "Purchase Reqs":        result["purchase_reqs"],
        "Job List":             result["jobs"],
        "Schedule Gantt":       result["gantt"],
        "Job Results":          result["job_results"],
        "Work Centre Results":  result["work_centre_results"],
        "Rule Comparison":      (result["rule_comparison"].reset_index()
                                 if not result["rule_comparison"].empty else pd.DataFrame()),
        "Best Rule":            result["best_by_criterion"],
        "Exceptions":           result["exceptions"],
        "KPI Summary":          pd.DataFrame([result["kpis"]]),
    }


def to_excel(result):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xl:
        head = xl.book.add_format({"bold": True, "bg_color": "#24463E",
                                   "font_color": "#FFFFFF", "border": 0})

        def sheet(name, df):
            if df is None or df.empty:
                return
            safe = name[:31].replace("/", "-").replace("*", "")
            df.to_excel(xl, sheet_name=safe, index=False)
            ws = xl.sheets[safe]
            for i, col in enumerate(df.columns):
                width = max(len(str(col)) + 2,
                            min(38, int(df[col].astype(str).str.len().max() or 8) + 2))
                ws.set_column(i, i, width)
                ws.write(0, i, col, head)
            ws.freeze_panes(1, 0)

        for f in FILES:
            df = read_active(f["key"])
            if not df.empty:
                sheet(f"IN {f['step']} {f['label']}", df)
        for name, df in output_tables(result).items():
            sheet(f"OUT {name}", df)
    buf.seek(0)
    return buf.getvalue()


def to_zip(result, include_inputs=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if include_inputs:
            for f in FILES:
                df = read_active(f["key"])
                if not df.empty:
                    z.writestr(f"inputs/{f['file']}", df.to_csv(index=False))
        for name, df in output_tables(result).items():
            if df is not None and not df.empty:
                z.writestr(f"outputs/{name.replace(' ', '_')}.csv", df.to_csv(index=False))
    buf.seek(0)
    return buf.getvalue()


def inputs_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            df = read_active(f["key"])
            if not df.empty:
                z.writestr(f["file"], df.to_csv(index=False))
    buf.seek(0)
    return buf.getvalue()


def blank_template(key):
    """An empty CSV with exactly the columns this file accepts."""
    return pd.DataFrame(columns=SCHEMA[key])


def csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")

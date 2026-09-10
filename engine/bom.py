"""
BLOCK 6 - BOM EXPLOSION
==============================================================================
6.1 Import BOM structure   (multi level, quantity per parent, scrap %)
6.2 Explode MPS through the BOM
6.3 Calculate gross requirements by item and period
6.4 Output gross requirements report

Also provides the low-level coding used by the MRP engine in Block 7, and the
where-used / indented-BOM views used by the dashboard.

Scrap convention
----------------
To build 1 parent you must ISSUE  qty_per / (1 - scrap_pct)  components,
because a fraction of what is issued is scrapped.  This is the standard
"shrinkage-adjusted" requirement.
"""

from collections import defaultdict
import pandas as pd


class BOMTree:
    """Indexed BOM with low-level codes and explosion helpers."""

    def __init__(self, bom_df, inventory_df=None):
        self.bom = bom_df.copy()
        self.inventory = inventory_df

        # parent -> [(component, qty_per, scrap_pct), ...]
        self.children = defaultdict(list)
        # component -> [(parent, qty_per), ...]
        self.parents = defaultdict(list)

        for _, r in self.bom.iterrows():
            self.children[r.ParentItem].append(
                (r.ComponentItem, float(r.QtyPer), float(r.ScrapPct))
            )
            self.parents[r.ComponentItem].append((r.ParentItem, float(r.QtyPer)))

        self.all_items = set(self.children) | set(self.parents)
        self.low_level_code = self._compute_low_level_codes()

    # -- 6.1 ---------------------------------------------------------------
    def _compute_low_level_codes(self):
        """Low-level code = the DEEPEST level at which an item appears.

        MRP must process items in ascending low-level-code order so that an
        item's requirements from every parent are collected before it is
        planned.  SPOKE, for example, is used by three different wheels.
        """
        code = {item: 0 for item in self.all_items}
        roots = [i for i in self.all_items if i not in self.parents]

        def walk(item, level, path):
            if item in path:
                raise ValueError(f"Cyclic BOM detected at {item}")
            code[item] = max(code.get(item, 0), level)
            for child, _, _ in self.children.get(item, []):
                walk(child, level + 1, path | {item})

        for r in roots:
            walk(r, 0, set())
        return code

    def items_by_level(self):
        """All items sorted by low-level code (MRP processing order)."""
        return sorted(self.all_items, key=lambda i: (self.low_level_code[i], i))

    def max_level(self):
        return max(self.low_level_code.values()) if self.low_level_code else 0

    # -- 6.2 / 6.3 ---------------------------------------------------------
    def explode(self, parent, qty):
        """Single-level explosion: components needed to make `qty` of `parent`."""
        out = []
        for child, qty_per, scrap in self.children.get(parent, []):
            required = qty * qty_per / (1.0 - scrap) if scrap < 1 else qty * qty_per
            out.append((child, required))
        return out

    def explode_gross_requirements(self, master_schedule):
        """6.2 / 6.3 - explode a schedule through ALL BOM levels.

        `master_schedule` : DataFrame with Item, Period, Qty
        Returns gross requirements per item per period, level by level.

        Note this is the *untimed* explosion of Block 6 - every requirement
        lands in the same period as its parent.  Block 7 (MRP) redoes the
        explosion with lead-time offsetting, which is what actually drives
        purchasing and production.
        """
        gross = defaultdict(float)          # (item, period) -> qty
        source = defaultdict(list)          # (item, period) -> [(parent, qty)]

        for _, r in master_schedule.iterrows():
            if r["Qty"] > 0:
                gross[(r["Item"], int(r["Period"]))] += float(r["Qty"])

        # walk levels in order so each level is fully accumulated first
        for item in self.items_by_level():
            periods = [p for (i, p) in list(gross) if i == item]
            for p in sorted(set(periods)):
                qty = gross[(item, p)]
                if qty <= 0:
                    continue
                for child, required in self.explode(item, qty):
                    gross[(child, p)] += required
                    source[(child, p)].append((item, round(required, 2)))

        rows = []
        for (item, p), qty in sorted(gross.items(), key=lambda kv: (self.low_level_code.get(kv[0][0], 0), kv[0][0], kv[0][1])):
            rows.append({
                "Item": item,
                "LowLevelCode": self.low_level_code.get(item, 0),
                "Period": p,
                "GrossRequirement": round(qty, 2),
                "DrivenBy": "; ".join(f"{par}({q})" for par, q in source.get((item, p), [])) or "MPS",
            })
        return pd.DataFrame(rows)

    # -- views used by the dashboard ---------------------------------------
    def indented_bom(self, root, qty=1.0):
        """Classic indented multi-level BOM listing."""
        rows = []

        def walk(item, q, level):
            rows.append({
                "Level": level,
                "Indent": ("    " * level) + ("- " if level else "") + item,
                "Item": item,
                "QtyPer": round(q, 4),
                "LowLevelCode": self.low_level_code.get(item, 0),
            })
            for child, qty_per, scrap in self.children.get(item, []):
                walk(child, q * qty_per / (1 - scrap), level + 1)

        walk(root, qty, 0)
        return pd.DataFrame(rows)

    def where_used(self, component):
        """Which parents consume this component, and at what quantity."""
        return pd.DataFrame(
            [{"Parent": p, "QtyPer": q} for p, q in self.parents.get(component, [])]
        )

    def annual_usage(self, forecast_df, periods_per_year=52, horizon=12):
        """Roll finished-goods forecast down the BOM to annualised item usage.

        Used by Block 4 to compute EOQ for dependent-demand components.
        """
        avg = forecast_df.groupby("Item")["Forecast"].mean().to_dict()
        sched = pd.DataFrame(
            [{"Item": i, "Period": 1, "Qty": v * periods_per_year}
             for i, v in avg.items()]
        )
        expl = self.explode_gross_requirements(sched)
        return expl.groupby("Item")["GrossRequirement"].sum().to_dict()


def build_bom_tree(data):
    return BOMTree(data["bom"], data.get("inventory"))

"""
MULTI-AGENT LAYER - the scenario agent  (Block 13)
==============================================================================
Turns a plain-English what-if into an executable set of engine overrides, runs
the full pipeline with them, and reports the difference against the baseline.

    "what if <product> demand jumps 40% in weeks 3 to 6"
        -> {"demand_shock": {"<PRODUCT>": {"periods": [3,4,5,6], "multiplier": 1.4}}}

The vocabulary is built from the loaded dataset, so the product and work centre
names it understands are always the ones actually in the plan.

The parsing is deliberately rule-based rather than model-based: it is
inspectable, it is reproducible in a demonstration, and it needs no API key or
network access. `interpret` returns the overrides it derived AND the phrases it
matched, so the user can always see why the agent read the request the way it
did - and correct it if the agent guessed wrong.
"""

import difflib
import re

from .base import Agent
from engine.pipeline import run_pipeline
from engine.scenarios import build_scenarios, compare_to_baseline, summarise_impact


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
# The lists below are the SAMPLE factory's vocabulary. For any other dataset
# `build_vocabulary` derives the same structure from the item codes, item
# descriptions, work centre names and supplier names actually loaded - so the
# agent speaks the language of whatever factory is in front of it.

_STOP = {"the", "a", "an", "of", "and", "with", "for", "set", "assembly",
         "assy", "kit", "std", "standard", "inch", "mm", "type"}


def _phrases(code, description=""):
    """Everyday ways a person might refer to this item."""
    out = set()
    c = str(code).strip()
    out.add(c.lower())
    spaced = re.sub(r"[-_/]+", " ", c).lower().strip()
    out.add(spaced)
    out.add(spaced.replace(" ", ""))

    d = str(description or "").strip().lower()
    if d and d not in ("nan", "-"):
        d = re.sub(r"[(),]", " ", d)
        d = re.sub(r"\s+", " ", d).strip()
        out.add(d)
        words = [w for w in d.split() if w not in _STOP and len(w) > 2]
        if len(words) >= 2:
            out.add(" ".join(words[-2:]))
        for w in words:
            out.add(w)
            # light stemming, so a description of "Painting & Curing" is also
            # found by "paint", and "Welding" by "weld". Generic English
            # morphology, not a curated per-product synonym list.
            for suffix in ("ing", "ed", "es", "s"):
                if len(w) > len(suffix) + 3 and w.endswith(suffix):
                    stem = w[: -len(suffix)]
                    if len(stem) > 3:
                        out.add(stem)
                    break
    return {q for q in out if len(q) > 2}


def build_vocabulary(data):
    """Derive item / work centre / supplier aliases from the loaded dataset."""
    items, centres, suppliers = {}, {}, {}

    inv = data.get("inventory")
    if inv is not None and not inv.empty:
        desc_col = "Description" if "Description" in inv.columns else None
        for _, r in inv.iterrows():
            code = str(r["Item"])
            items[code] = sorted(_phrases(code, r[desc_col] if desc_col else ""))

    cap = data.get("capacity")
    if cap is not None and not cap.empty:
        desc_col = "Description" if "Description" in cap.columns else None
        for _, r in cap.iterrows():
            code = str(r["WorkCentre"])
            centres[code] = sorted(_phrases(code, r[desc_col] if desc_col else ""))

    sup = data.get("supplier")
    if sup is not None and not sup.empty:
        name_col = "SupplierName" if "SupplierName" in sup.columns else None
        for _, r in sup.drop_duplicates("Supplier").iterrows():
            code = str(r["Supplier"])
            suppliers[code] = sorted(_phrases(code, r[name_col] if name_col else ""))

    return items, centres, suppliers


# There is deliberately NO hard-coded alias table. Every vocabulary is derived
# from the loaded dataset by `build_vocabulary`, so the agent can never suggest
# or match a term belonging to a factory that is not the one in front of it.


INCREASE_WORDS = ("increase", "rise", "rises", "jump", "jumps", "surge", "up",
                  "grow", "grows", "spike", "double", "doubles", "higher", "more")
DECREASE_WORDS = ("decrease", "fall", "falls", "drop", "drops", "collapse", "down",
                  "decline", "declines", "halve", "halves", "lower", "less", "reduce")


# ===========================================================================
def _find_entity(text, aliases):
    """Match the longest alias present in the text; fall back to fuzzy match."""
    text = text.lower()
    best, best_len = None, 0
    for code, names in aliases.items():
        if code.lower() in text and len(code) > best_len:
            best, best_len = code, len(code)
        for n in names:
            if n in text and len(n) > best_len:
                best, best_len = code, len(n)
    if best:
        return best

    words = re.findall(r"[a-z0-9\-]+", text)
    flat = {n: c for c, names in aliases.items() for n in names}
    for w in words:
        m = difflib.get_close_matches(w, flat.keys(), n=1, cutoff=0.85)
        if m:
            return flat[m[0]]
    return None


def _find_periods(text):
    """Pull a period range or list out of the request."""
    t = text.lower()
    m = re.search(r"(?:period|week)s?\s*(\d+)\s*(?:to|-|through|until|and)\s*(\d+)", t)
    if m:
        return list(range(int(m.group(1)), int(m.group(2)) + 1))
    m = re.search(r"(?:in|from|during)\s+(?:period|week)\s*(\d+)", t)
    if m:
        return [int(m.group(1))]
    m = re.search(r"(?:period|week)\s*(\d+)", t)
    if m:
        return [int(m.group(1))]
    return None


def _find_percentage(text):
    """Return a multiplier, e.g. '40% higher' -> 1.40, 'halve' -> 0.5."""
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)", t)
    if m:
        pct = float(m.group(1)) / 100.0
        if any(w in t for w in DECREASE_WORDS):
            return max(0.0, 1.0 - pct)
        return 1.0 + pct
    if "double" in t or "twice" in t:
        return 2.0
    if "triple" in t:
        return 3.0
    if "halve" in t or "half" in t:
        return 0.5
    m = re.search(r"(?:by|of)\s+(\d+(?:\.\d+)?)\s*x", t)
    if m:
        return float(m.group(1))
    return None


def _find_absolute_level(text):
    """Distinguish 'runs AT 50%' (a level) from 'rises BY 50%' (a change).

    "the paint line runs at 50%" means half of normal capacity, not one and a
    half times it - so an absolute level has to be parsed separately from a
    percentage change or the scenario comes out backwards.
    """
    t = text.lower()
    m = re.search(r"(?:at|to|down to|reduced to|operating at|running at)\s+"
                  r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)", t)
    if m:
        return float(m.group(1)) / 100.0
    if re.search(r"\bhalf (?:capacity|speed|rate)\b", t):
        return 0.5
    return None


def _find_integer(text, keywords):
    t = text.lower()
    for kw in keywords:
        m = re.search(rf"(\d+)\s*(?:period|week)s?\s*{kw}", t)
        if m:
            return int(m.group(1))
        m = re.search(rf"{kw}\s*(?:by|of)?\s*(\d+)", t)
        if m:
            return int(m.group(1))
    return None


# ===========================================================================
def interpret(text, item_master=None, vocab=None):
    """Parse a plain-English what-if into engine overrides.

    vocab : optional (item_aliases, wc_aliases, supplier_aliases) from
            build_vocabulary(data). Without it the sample factory's vocabulary
            is used, so a non-bicycle dataset should always pass one.

    Returns {overrides, explanation, matched, confident}
    """
    t = text.lower().strip()
    overrides = {}
    matched = []

    if vocab is None:
        # fall back to a vocabulary built from the item master alone
        vocab = ({str(k): sorted(_phrases(k, v.get("Description", "")))
                  for k, v in (item_master or {}).items()}, {}, {})
    item_vocab, wc_vocab, sup_vocab = vocab
    item = _find_entity(t, item_vocab)
    wc = _find_entity(t, wc_vocab)
    supplier = _find_entity(t, sup_vocab)
    periods = _find_periods(t)
    mult = _find_percentage(t)
    level = _find_absolute_level(t)

    is_demand = any(w in t for w in ("demand", "order", "sales", "forecast", "sell"))
    is_leadtime = "lead time" in t or "leadtime" in t or "lead-time" in t
    is_delay = any(w in t for w in ("delay", "delayed", "late", "slip", "slips", "slipping"))
    is_breakdown = any(w in t for w in ("breakdown", "break down", "breaks down",
                                        "down", "fails", "failure", "outage", "offline"))
    is_capacity_add = any(w in t for w in ("add", "extra", "another", "second",
                                           "invest", "new machine", "more capacity"))
    is_rush = "rush" in t or "urgent order" in t or "fleet" in t

    # ---- rush order --------------------------------------------------------
    if is_rush and item:
        qty = None
        m = re.search(r"(\d[\d,]*)\s*(?:unit|piece|item)?s?", t)
        if m:
            qty = int(m.group(1).replace(",", ""))
        overrides["rush_order"] = [{
            "item": item, "period": (periods or [2])[0],
            "quantity": qty or 200, "customer": "Rush Order",
        }]
        matched.append(f"rush order of {qty or 200} x {item} in period {(periods or [2])[0]}")

    # ---- demand change -----------------------------------------------------
    elif is_demand and item and mult:
        if periods:
            overrides["demand_shock"] = {item: {"periods": periods, "multiplier": mult}}
            matched.append(f"demand for {item} x{mult:.2f} in periods "
                           f"{periods[0]}-{periods[-1]}")
        else:
            overrides["demand_multiplier"] = {item: mult}
            matched.append(f"demand for {item} x{mult:.2f} across the horizon")

    # ---- supplier delay ----------------------------------------------------
    elif (is_delay or is_leadtime) and (supplier or item):
        extra = _find_integer(t, ["delay", "delayed", "late", "slip", "longer", "by"])
        if supplier:
            overrides["supplier_delay"] = {supplier: extra or 2}
            matched.append(f"{supplier} lead times +{extra or 2} periods")
        else:
            base = int((item_master or {}).get(item, {}).get("LeadTime", 2))
            if mult:
                new_lt = max(1, int(round(base * mult)))
            elif extra:
                new_lt = base + extra if (is_delay and not is_leadtime) else extra
            else:
                new_lt = base + 2
            overrides["lead_time"] = {item: new_lt}
            matched.append(f"{item} lead time {base} -> {new_lt} periods")

    # ---- capacity ----------------------------------------------------------
    # A breakdown can be phrased as a failure ("the paint line goes down") or as
    # a degraded rate ("the paint line runs at 50%"), so both are accepted.
    elif wc and (is_breakdown or level is not None or (mult is not None and mult < 1)):
        pct = level if level is not None else (
            mult if (mult is not None and mult < 1) else 0.5)
        periods = periods or [4, 5]
        overrides["capacity"] = {wc: {"periods": periods, "capacity_pct": pct}}
        matched.append(f"{wc} at {pct*100:.0f}% capacity in periods "
                       f"{periods[0]}-{periods[-1]}")

    elif wc and is_capacity_add:
        field = "ShiftsPerDay" if "shift" in t else "NumMachines"
        n = _find_integer(t, ["add", "extra"]) or 1
        overrides["capacity"] = {wc: {field: f"+{n}"}}
        matched.append(f"{wc} {field} +{n}")

    # ---- safety stock ------------------------------------------------------
    elif item and "safety stock" in t and mult:
        base = float((item_master or {}).get(item, {}).get("SafetyStock", 0))
        overrides["safety_stock"] = {item: round(base * mult)}
        matched.append(f"{item} safety stock {base:.0f} -> {base*mult:.0f}")

    # ---- dispatching rule --------------------------------------------------
    for rule in ("FCFS", "SPT", "EDD", "LPT", "CR"):
        if re.search(rf"\b{rule.lower()}\b", t):
            overrides["dispatch_rule"] = rule
            matched.append(f"dispatching rule -> {rule}")
            break

    confident = bool(overrides)
    if confident:
        explanation = "Interpreted as: " + "; ".join(matched) + "."
    else:
        explanation = (
            "Could not map that request onto a change the engine can make. Try naming "
            "a product, component or work centre together with what changes - for "
            "example 'what if <product> demand rises 30% in weeks 3 to 6', "
            "'what if <supplier> slips by 3 weeks', or "
            "'what if <work centre> runs at 50% in week 4'.")

    return {"overrides": overrides, "explanation": explanation,
            "matched": matched, "confident": confident}


# ===========================================================================
class ScenarioAgent(Agent):
    name = "ScenarioAgent"
    role = "Turns plain-English what-ifs into executed simulations (Block 13)"

    def observe(self):
        self.observations["kpis"] = self.bb.result.get("kpis", {})
        self.observations["item_master"] = (
            self.bb.table("data")["inventory"].set_index("Item").to_dict("index"))
        self.observations["vocab"] = build_vocabulary(self.bb.table("data"))

    def reason(self):
        return []

    def act(self):
        return []

    # -- the agent's real interface ---------------------------------------
    def run_request(self, text, schedule_window=None, base_data=None):
        """Interpret a request, run it, and report the impact."""
        self.observe()
        parsed = interpret(text, self.observations["item_master"],
                           self.observations["vocab"])
        if not parsed["confident"]:
            return {"ok": False, "explanation": parsed["explanation"],
                    "overrides": {}, "comparison": None, "summary": None}

        window = schedule_window or self.bb.result.get("schedule_window", (1, 8))
        result = run_pipeline(data=base_data or self.bb.result.get("base_data"),
                              overrides=parsed["overrides"],
                              schedule_window=window)
        comparison = compare_to_baseline(self.observations["kpis"], result["kpis"], text)
        summary = summarise_impact(comparison, text)

        self.bb.send(self.name, "Coordinator",
                     f"Ran what-if '{text}' -> {parsed['explanation']}")

        return {"ok": True, "explanation": parsed["explanation"],
                "overrides": parsed["overrides"], "result": result,
                "comparison": comparison, "summary": summary,
                "narrative": self.narrate(comparison, result, text)}

    def run_named_scenario(self, name, schedule_window=None, base_data=None,
                           library=None):
        """Run one scenario from the library built for this dataset."""
        self.observe()
        lib = library or build_scenarios(self.bb.table("data"), self.bb.result)
        spec = lib[name]
        window = schedule_window or self.bb.result.get("schedule_window", (1, 8))
        result = run_pipeline(data=base_data or self.bb.result.get("base_data"),
                              overrides=spec["overrides"], schedule_window=window)
        comparison = compare_to_baseline(self.observations["kpis"], result["kpis"], name)
        return {"ok": True, "explanation": spec["description"],
                "overrides": spec["overrides"], "result": result,
                "comparison": comparison,
                "summary": summarise_impact(comparison, name),
                "narrative": self.narrate(comparison, result, name)}

    # -- written interpretation of the numbers -----------------------------
    def narrate(self, comparison, result, label):
        """Explain what the scenario did, in the language a manager would use."""
        if comparison is None or comparison.empty:
            return "No comparable KPIs were produced."

        lines = []
        base_kpis = self.observations["kpis"]
        new_kpis = result["kpis"]

        # headline
        health_before = self.bb.result.get("health_score")
        health_after = result.get("health_score")
        if health_before is not None and health_after is not None:
            delta = health_after - health_before
            verdict = ("no material change" if abs(delta) < 1
                       else ("an improvement" if delta > 0 else "a deterioration"))
            lines.append(
                f"Plan health moves from {health_before} to {health_after} out of 100 - "
                f"{verdict} of {abs(delta):.1f} points.")

        # feasibility is the first thing a planner checks
        if base_kpis.get("mps_feasible") and not new_kpis.get("mps_feasible"):
            lines.append(
                "The master production schedule is no longer feasible: even after "
                f"{new_kpis.get('mps_iterations')} rounds of level-loading, capacity "
                f"peaks at {new_kpis.get('rccp_peak_utilisation')}% of what is available. "
                "This plan cannot be executed as it stands.")
        elif not base_kpis.get("mps_feasible") and new_kpis.get("mps_feasible"):
            lines.append("The master production schedule becomes feasible under this scenario.")

        # material availability
        hold_before = base_kpis.get("orders_on_hold", 0)
        hold_after = new_kpis.get("orders_on_hold", 0)
        if hold_after > hold_before:
            lines.append(
                f"Orders on hold rise from {hold_before} to {hold_after}. "
                f"{hold_after - hold_before} additional production order(s) cannot start "
                "because their components are not there.")

        # the false-comfort trap
        otd_before = base_kpis.get("sched_otd", 0)
        otd_after = new_kpis.get("sched_otd", 0)
        if otd_after > otd_before and hold_after > hold_before * 1.2:
            lines.append(
                f"On-time delivery appears to improve ({otd_before}% to {otd_after}%), but "
                "this is an artefact: fewer orders were released to the shop floor, so the "
                "schedule had less work to be late with. Read the held-order count, not the "
                "OTD figure, as the true measure of this scenario.")
        elif otd_after < otd_before:
            lines.append(
                f"On-time delivery falls from {otd_before}% to {otd_after}%, with "
                f"{new_kpis.get('sched_tardy_jobs')} job(s) finishing late against "
                f"{base_kpis.get('sched_tardy_jobs')} in the baseline.")

        # cost
        pv_before = base_kpis.get("purchase_value", 0)
        pv_after = new_kpis.get("purchase_value", 0)
        if pv_before and abs(pv_after - pv_before) > pv_before * 0.05:
            lines.append(
                f"Purchase commitment moves from {pv_before:,.0f} to {pv_after:,.0f} "
                f"({(pv_after - pv_before) / pv_before * 100:+.0f}%).")

        worse = comparison[comparison["Impact"] == "Worse"]
        better = comparison[comparison["Impact"] == "Improved"]
        lines.append(f"In total {len(worse)} KPI(s) deteriorate and {len(better)} improve.")
        return " ".join(lines)

"""
INTEGRATED MANUFACTURING OPERATIONS CONTROL TOWER
==============================================================================
Interactive dashboard. The page list follows the assignment flow chart exactly,
block 1 through block 13, plus an All Data page that shows every input and
result table in one place.

Run:   streamlit run dashboard/app.py
"""

import json
import os
import sys
import traceback

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import Coordinator                                    # noqa: E402
from engine import io_utils                                       # noqa: E402
from engine.config import DISPATCH_RULES, RULE_DESCRIPTIONS       # noqa: E402
from engine.exceptions import DecisionLog, recommended_actions    # noqa: E402
from engine.kpi import kpi_definitions                            # noqa: E402
from engine.mrp import item_grid                                  # noqa: E402
from engine.pipeline import run_pipeline                          # noqa: E402
from engine.scenarios import (build_scenarios, scenario_catalogue,  # noqa: E402
                              summarise_impact)
from dashboard import data_io                                     # noqa: E402
from dashboard import theme as T                                  # noqa: E402

st.set_page_config(page_title="Manufacturing Operations Control Tower",
                   page_icon="🏭", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(T.CSS, unsafe_allow_html=True)


# ===========================================================================
# PIPELINE  (cached against the data fingerprint, so an upload re-plans)
# ===========================================================================
@st.cache_resource(show_spinner="Re-planning the factory…")
def cached_pipeline(fingerprint, overrides_json, window, rule):
    overrides = json.loads(overrides_json)
    data = data_io.load_plan_data()          # loads + derives BOMLevel/SourceType
    return run_pipeline(data=data, overrides=overrides,
                        schedule_window=tuple(window), selected_rule=rule)


@st.cache_resource(show_spinner=False)
def cached_agents(_result, cache_key):
    c = Coordinator(_result)
    c.run()
    return c


def get_decision_log():
    if "decision_log" not in st.session_state:
        st.session_state.decision_log = DecisionLog()
    return st.session_state.decision_log


# ===========================================================================
# SHARED UI
# ===========================================================================
import re as _re


def header(block, title, subtitle):
    st.markdown(f'<div class="ct-block">{block}</div>', unsafe_allow_html=True)
    st.markdown(f"## {title}")
    st.markdown(f'<div class="ct-sub">{subtitle}</div>', unsafe_allow_html=True)


def steps(items):
    """The sub-steps of this block, worded as on the flow chart."""
    chips = " ".join(f'<span class="ct-step">{s}</span>' for s in items)
    st.markdown(f'<div class="ct-steps">{chips}</div>', unsafe_allow_html=True)


def cards(items, per_row=5):
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row)
        for col, (label, value, sub) in zip(cols, items[i:i + per_row]):
            col.markdown(T.card(label, value, sub), unsafe_allow_html=True)


def note(text, kind="note"):
    html = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(text))
    html = _re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", html)
    html = _re.sub(r"`(.+?)`", r"<code>\1</code>", html)
    st.markdown(f'<div class="ct-{kind}">{html}</div>', unsafe_allow_html=True)


def show(fig):
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def table(df, height=None, **kwargs):
    if height is not None:
        kwargs["height"] = height
    st.dataframe(df, use_container_width=True, hide_index=True, **kwargs)


def download(df, filename, label="Download CSV", key=None):
    """Every table on every page can be taken away as a CSV."""
    st.download_button(label, data=data_io.csv_bytes(df), file_name=filename,
                       mime="text/csv", key=key, use_container_width=False)


def table_with_download(df, filename, height=None, key=None):
    table(df, height=height)
    download(df, filename, f"⬇ Download {filename}", key=key)


# ===========================================================================
# PAGE — OVERVIEW
# ===========================================================================
def page_overview(R, agents):
    k = R["kpis"]
    header("Control Tower", "Operations Overview",
           "One view of the whole plan, from demand forecast through to the shop floor.")

    score = R["health_score"]
    verdict = ("Healthy" if score >= 80 else
               "Workable with intervention" if score >= 60 else
               "At risk" if score >= 40 else "Failing")
    kind = ("good" if score >= 80 else "note" if score >= 60 else
            "warn" if score >= 40 else "crit")

    c1, c2 = st.columns([1, 2.6])
    with c1:
        st.markdown(T.card("Plan health score", f"{score}/100", verdict),
                    unsafe_allow_html=True)
    with c2:
        b = R["health_breakdown"]
        fig = go.Figure(go.Bar(
            x=b["Score"], y=b["Component"], orientation="h",
            marker=dict(color=[T.colour_for(i) for i in range(len(b))], line=dict(width=0)),
            text=[f"{v:.0f}" for v in b["Score"]], textposition="outside",
            textfont=dict(color=T.INK_2, size=11),
            hovertemplate="%{y}<br>Score %{x:.0f}/100<extra></extra>"))
        fig.update_layout(**T.layout("Health score components (0–100 each)",
                                     height=210, showlegend=False))
        fig.update_xaxes(range=[0, 118], showticklabels=False)
        fig.update_yaxes(showgrid=False, autorange="reversed")
        show(fig)

    note(f"Weighted from forecast accuracy, MPS feasibility, material readiness, "
         f"on-time delivery and open exceptions.", kind)

    st.markdown("#### Plan at a glance")
    cards([
        ("Forecast MAPE", f"{k.get('forecast_avg_mape', 0)}%",
         f"{k.get('forecast_alerts', 0)} model alert(s)"),
        ("MPS", "Feasible" if k.get("mps_feasible") else "Infeasible",
         f"{k.get('mps_iterations')} revision iteration(s)"),
        ("Peak capacity", f"{k.get('rccp_peak_utilisation', 0)}%",
         f"bottleneck {k.get('rccp_bottleneck', '-')}"),
        ("Orders released", f"{k.get('orders_released', 0)}",
         f"{k.get('orders_on_hold', 0)} on hold"),
        ("Inventory value", f"{k.get('inventory_value', 0):,.0f}",
         f"{k.get('inventory_items', 0)} items"),
    ])
    st.write("")
    cards([
        ("On-time delivery", f"{k.get('sched_otd', 0)}%",
         f"{k.get('sched_tardy_jobs', 0)} job(s) late"),
        ("Makespan", f"{k.get('sched_makespan', 0):.0f} h",
         f"{k.get('sched_makespan_periods', 0):.1f} periods"),
        ("Avg flow time", f"{k.get('sched_avg_flow', 0):.1f} h",
         f"WIP {k.get('sched_wip', 0):.2f} jobs"),
        ("Purchase value", f"{k.get('purchase_value', 0):,.0f}",
         f"{k.get('purchase_reqs', 0)} requisitions"),
        ("Open exceptions", f"{k.get('exceptions_total', 0)}",
         f"{k.get('exceptions_critical', 0)} critical"),
    ])

    st.markdown("")
    left, right = st.columns([1.45, 1])
    with left:
        st.markdown("#### Where the plan stands, block by block")
        blocks = [
            ("3", "Demand Forecasting", f"MAPE {k.get('forecast_avg_mape')}%",
             "good" if k.get("forecast_avg_mape", 100) < 15 else "warning"),
            ("4", "Inventory Planning",
             f"{k.get('items_below_safety', 0)} item(s) below safety stock",
             "good" if k.get("items_below_safety", 0) == 0 else "warning"),
            ("5", "Master Production Schedule",
             "Feasible" if k.get("mps_feasible") else "Infeasible",
             "good" if k.get("mps_feasible") else "critical"),
            ("6", "BOM Explosion",
             f"{R['bom_tree'].max_level() + 1} levels, "
             f"{len(R['gross_requirements'])} requirement rows", "good"),
            ("7", "Material Requirements Planning",
             f"{k.get('mrp_planned_orders', 0)} planned orders, "
             f"{k.get('mrp_late_releases', 0)} past due",
             "good" if k.get("mrp_late_releases", 0) == 0 else "serious"),
            ("8", "Production Order Release",
             f"{k.get('orders_released', 0)} released / {k.get('orders_on_hold', 0)} held",
             "good" if k.get("orders_on_hold", 0) == 0 else "warning"),
            ("9", "Routing & Capacity Prep",
             f"{len(R['jobs'])} jobs, {len(R['operations'])} operations", "good"),
            ("10", "Shop-Floor Scheduling",
             f"{k.get('sched_rule')}, makespan {k.get('sched_makespan', 0):.0f} h", "good"),
            ("11", "Performance Comparison", f"OTD {k.get('sched_otd')}%",
             "good" if k.get("sched_otd", 0) >= 95 else "warning"),
            ("12", "Exception Management",
             f"{k.get('exceptions_total', 0)} open, {k.get('exceptions_critical', 0)} critical",
             "good" if k.get("exceptions_critical", 0) == 0 else "critical"),
        ]
        rows = []
        for num, name, status, sev in blocks:
            icon = {"good": "🟢", "warning": "🟡", "serious": "🟠", "critical": "🔴"}[sev]
            rows.append({"Block": num, "Stage": name, "": icon, "Status": status})
        table(pd.DataFrame(rows), height=390)
    with right:
        st.markdown("#### Highest-priority exceptions")
        exc = R["exceptions"]
        if exc.empty:
            note("No open exceptions — the plan is clean.", "good")
        else:
            top = exc.head(8)[["Severity", "Item", "ExceptionType", "Period"]].copy()
            top.insert(0, "", top["Severity"].map(T.SEVERITY_ICON))
            table(top, height=390)

    with st.expander("What the agents concluded"):
        st.markdown(agents.briefing())


# ===========================================================================
# BLOCK 1 — UNDERSTAND ASSIGNMENT REQUIREMENTS
# ===========================================================================
def page_block1(R, agents):
    header("Block 1", "Understand Assignment Requirements",
           "Objectives, scope, deliverables and the end-to-end framework this "
           "system implements.")
    steps(["1.1 Read & Analyze Assignment", "1.2 Clarify Doubts",
           "1.3 Plan Project Work", "1.4 Understand Overall Framework"])

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 1.1 Objectives, scope and deliverables")
        table(pd.DataFrame([
            {"Item": "Objective", "Detail": "Build an integrated planning system covering "
             "forecasting through to shop-floor scheduling and exception management"},
            {"Item": "Scope", "Detail": "3+ products, multi-level BOM (≥3 levels), "
             "24–36 periods of history, multiple work centres"},
            {"Item": "Deliverables", "Detail": "Platform, architecture, datasets, "
             "documentation, reports and a live demonstration"},
            {"Item": "Evaluation", "Detail": "Correctness of the planning logic, depth of "
             "integration, quality of the dashboard, and insight in the analysis"},
        ]))

        st.markdown("#### 1.3 How the work was planned")
        table(pd.DataFrame([
            {"Stage": "1. Data design", "Output": "9 validated input files"},
            {"Stage": "2. Engine", "Output": "15 modules, blocks 3–13"},
            {"Stage": "3. Verification", "Output": "64 tests over the planning maths"},
            {"Stage": "4. Dashboard", "Output": "This interface"},
            {"Stage": "5. Agent layer", "Output": "8 agents + coordinator"},
            {"Stage": "6. Documentation", "Output": "README, formulas, viva notes"},
        ]))

    with c2:
        st.markdown("#### 1.4 The end-to-end framework")
        note("**Forecasting → Inventory → MPS → BOM → MRP → Scheduling → Performance.** "
             "Each block consumes the one before it. That is why a forecast error in "
             "Block 3 becomes a material shortage in Block 7 and a late job in Block 10 — "
             "and why the system traces problems back across blocks rather than "
             "reporting them in isolation.")
        table(pd.DataFrame([
            {"Block": "3", "Question it answers": "How many will customers want?"},
            {"Block": "4", "Question it answers": "How much stock should we hold?"},
            {"Block": "5", "Question it answers": "What will we build, week by week?"},
            {"Block": "6", "Question it answers": "What parts does that need?"},
            {"Block": "7", "Question it answers": "What must we order, and when?"},
            {"Block": "8", "Question it answers": "Can we start this job today?"},
            {"Block": "9", "Question it answers": "Which machine does which step?"},
            {"Block": "10", "Question it answers": "In what order do we run the jobs?"},
            {"Block": "11", "Question it answers": "Which sequencing rule is best?"},
            {"Block": "12", "Question it answers": "What is wrong and what do we do?"},
            {"Block": "13", "Question it answers": "What if something breaks?"},
        ]), height=420)

    st.markdown("#### 1.2 Assumptions confirmed")
    table(pd.DataFrame([
        {"Assumption": "Planning periods are weeks", "Consequence": "Lead times, demand and capacity all in weekly buckets"},
        {"Assumption": "Gross demand = max(forecast, booked orders)", "Consequence": "Booked orders above forecast are still produced"},
        {"Assumption": "Scrap is shrinkage", "Consequence": "Issue qty ÷ (1 − scrap); compounds down the BOM"},
        {"Assumption": "An item is due at the START of its due period", "Consequence": "A sub-assembly is on the shelf when the parent starts"},
        {"Assumption": "Setup is charged once per order", "Consequence": "One batch per production order"},
        {"Assumption": "MRP is infinite-capacity", "Consequence": "Capacity is checked before (Block 5) and after (Block 10), not during"},
    ]))


# ===========================================================================
# BLOCK 2 — PREPARE INPUT DATASET FILES  (import / export lives here)
# ===========================================================================
def page_inputs(R, agents):
    header("Block 2", "Prepare Input Dataset Files",
           "The nine input files. Replace any of them with your own CSV, or "
           "download the current set as a template to edit.")
    steps([f"{f['step']} {f['label']}" for f in data_io.FILES])

    data = R["data"]
    custom = [f["key"] for f in data_io.FILES if data_io.is_custom(f["key"])]

    if custom:
        note(f"**Running on your data.** {len(custom)} file(s) replaced: "
             + ", ".join(data_io.BY_KEY[k]["label"] for k in custom)
             + ". Use *Reset to sample data* in the sidebar to go back.", "good")
    else:
        note(f"Running on the **built-in sample dataset** — "
             f"{len(_finished_items(R))} finished products, {len(data['inventory'])} "
             f"parts, {R['bom_tree'].max_level() + 1} BOM levels and "
             f"{len(data['capacity'])} work centres. Upload your own files below to "
             f"plan **any** factory: nothing in the forecasting, MRP, scheduling, "
             f"scenarios or agent vocabulary is tied to a particular product — it is "
             f"all derived from whatever data is loaded.")

    cards([
        ("Items", f"{len(data['inventory'])}",
         f"{data['inventory'].BOMLevel.max() + 1} BOM levels"),
        ("BOM links", f"{len(data['bom'])}", "parent → component"),
        ("Work centres", f"{len(data['capacity'])}", f"{len(data['routing'])} routing steps"),
        ("Demand history", f"{data['demand'].Period.max()}", "periods per product"),
        ("Customer orders", f"{len(data['orders'])}",
         f"{data['orders'].Quantity.sum():,.0f} units booked"),
    ])

    st.markdown("")
    tab_browse, tab_import, tab_export, tab_check = st.tabs(
        ["📊 Browse the files", "📥 Import your data", "📤 Export", "✅ Validation"])

    # ---- browse ----------------------------------------------------------
    with tab_browse:
        st.markdown("#### Demand history — the three patterns")
        d = data["demand"]
        colours = T.series_colours(d["Item"].unique())
        fig = go.Figure()
        for item, g in d.groupby("Item"):
            g = g.sort_values("Period")
            fig.add_trace(go.Scatter(
                x=g["Period"], y=g["Demand"], name=item, mode="lines",
                line=dict(color=colours[item], width=2),
                hovertemplate=f"{item}<br>Period %{{x}}<br>%{{y}} units<extra></extra>"))
        fig.update_layout(**T.layout(None, height=300, hovermode="x unified"))
        fig.update_xaxes(title="Period")
        fig.update_yaxes(title="Units")
        show(fig)

        choice = st.selectbox(
            "File", [f"{f['step']}  {f['label']}  ({f['file']})" for f in data_io.FILES],
            key="browse_file")
        spec = data_io.FILES[[f"{f['step']}  {f['label']}  ({f['file']})"
                              for f in data_io.FILES].index(choice)]
        st.caption(spec["help"])
        df = data_io.read_active(spec["key"])
        if df.empty:
            note("This file is an empty template — the system fills it in.", "note")
        else:
            table(df, height=420)
        download(df, spec["file"], f"⬇ Download {spec['file']}", key=f"dl_{spec['key']}")

    # ---- import ----------------------------------------------------------
    with tab_import:
        note("Upload a **CSV or Excel** file with *any* column names. The system "
             "matches your columns to the fields the planner needs, shows you the "
             "match so you can correct it, and fills anything you do not have with a "
             "documented default. Only a handful of fields are genuinely essential.")

        for f in data_io.FILES:
            if not f["essential"]:
                continue
            is_c = data_io.is_custom(f["key"])
            with st.expander(
                    f"{'🟢' if is_c else '⚪'}  {f['step']}  {f['label']}"
                    + ("  — using your data" if is_c else ""), expanded=False):
                _import_panel(f)

    # ---- export ----------------------------------------------------------
    with tab_export:
        st.markdown("#### Take the data with you")
        c1, c2, c3 = st.columns(3)
        c1.download_button("📗 Everything as Excel", data=data_io.to_excel(R),
                           file_name="Control_Tower_Full.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True, key="xl_b2")
        c2.download_button("🗜 Everything as ZIP of CSVs", data=data_io.to_zip(R),
                           file_name="Control_Tower_All.zip", mime="application/zip",
                           use_container_width=True, key="zip_b2")
        c3.download_button("📁 Just the 9 input files", data=data_io.inputs_zip(),
                           file_name="Control_Tower_Inputs.zip", mime="application/zip",
                           use_container_width=True, key="inzip_b2")
        st.caption("The Excel workbook has one sheet per table — inputs first, then "
                   "every result. It is the easiest thing to hand in or open in Power BI.")

    # ---- validation ------------------------------------------------------
    with tab_check:
        st.markdown("#### Cross-file checks")
        st.caption("These run across the whole dataset, not just one file — the sort of "
                   "error that only shows up once the files are put together.")
        problems = data_io.cross_check(data)
        if not problems:
            note("**All checks passed.** Every item referenced exists, the BOM has no "
                 "loops, every made item has a routing, every routing points at a real "
                 "work centre, and there is enough demand history to forecast from.", "good")
        else:
            for p_ in problems:
                note(f"⚠️ {p_}", "warn")

        table(pd.DataFrame([
            {"Check": "Items referenced exist in the Inventory Master", "Applies to": "Demand, Orders, BOM, Routing"},
            {"Check": "BOM contains no circular references", "Applies to": "BOM"},
            {"Check": "Every made item has a routing", "Applies to": "Inventory + Routing"},
            {"Check": "Every routing step names a real work centre", "Applies to": "Routing + Capacity"},
            {"Check": "At least 12 periods of demand history per product", "Applies to": "Demand History"},
            {"Check": "SourceType is M or P; LotSizeRule is LFL/FOQ/EOQ", "Applies to": "Inventory Master"},
            {"Check": "Efficiency is a fraction, not a percentage", "Applies to": "Machine Capacity"},
        ]))



# ===========================================================================
# The three-step import panel: read -> map -> fill
# ===========================================================================
def _import_panel(spec):
    """Upload, map columns, preview, commit - for one input file."""
    key = spec["key"]
    st.caption(spec["help"])

    ess = ", ".join(f"`{c}`" for c in spec["essential"])
    st.markdown(f"**Essential fields:** {ess}")
    if spec["optional"]:
        with st.expander("Everything else is optional - see the defaults"):
            table(pd.DataFrame([
                {"Field": k, "If you don't supply it": str(v[0]), "Meaning": v[1]}
                for k, v in spec["optional"].items()]))

    up = st.file_uploader("CSV, Excel or tab-separated",
                          type=["csv", "xlsx", "xlsm", "xls", "tsv", "txt"],
                          key=f"up_{key}", label_visibility="collapsed")

    if up is None:
        c1, c2 = st.columns(2)
        c1.download_button("Current file (as a template)",
                           data=data_io.csv_bytes(data_io.read_active(key)),
                           file_name=spec["file"], mime="text/csv", key=f"tpl_{key}",
                           use_container_width=True)
        c2.download_button("Blank template (headers only)",
                           data=data_io.csv_bytes(data_io.blank_template(key)),
                           file_name=f"BLANK_{spec['file']}", mime="text/csv",
                           key=f"blank_{key}", use_container_width=True)
        return

    # ---- step 1: read -----------------------------------------------------
    sheets = data_io.sheet_names(up)
    sheet = None
    if len(sheets) > 1:
        sheet = st.selectbox("Which sheet?", sheets, key=f"sheet_{key}")
    raw, err = data_io.read_any(up, sheet)
    if err:
        st.error(err)
        return

    st.success(f"Read **{len(raw):,} rows** with **{len(raw.columns)} columns**: "
               + ", ".join(f"`{c}`" for c in list(raw.columns)[:10])
               + (" ..." if len(raw.columns) > 10 else ""))

    # ---- step 2: map ------------------------------------------------------
    st.markdown("**Match your columns to the planner's fields**")
    suggested = data_io.suggest_mapping(key, list(raw.columns))
    choices = ["- not in my file -"] + list(raw.columns)
    fields = spec["essential"] + list(spec["optional"])
    mapping = {}

    for row_start in range(0, len(fields), 3):
        cols = st.columns(3)
        for col, field in zip(cols, fields[row_start:row_start + 3]):
            required = field in spec["essential"]
            guess = suggested.get(field)
            idx = choices.index(guess) if guess in choices else 0
            label = field if required else f"{field} (optional)"
            if required and not guess:
                label = "! " + label
            picked = col.selectbox(label, choices, index=idx,
                                   key=f"map_{key}_{field}")
            mapping[field] = None if picked.startswith("- not") else picked

    auto = sum(1 for fld in fields if suggested.get(fld))
    st.caption(f"{auto} of {len(fields)} fields matched automatically. "
               f"Change any that are wrong.")

    # ---- step 3: preview and fill ----------------------------------------
    clean, notes, problems = data_io.preview_mapping(key, raw, mapping)

    if problems:
        for p_ in problems:
            note(f"{p_}", "crit")
    else:
        note(f"**Ready.** {len(clean):,} rows will be imported.", "good")

    st.markdown("**Preview of what will be imported**")
    table(clean.head(8))

    if notes:
        with st.expander(f"{len(notes)} field(s) filled with a default"):
            for n in notes:
                st.markdown(f"- {n}")

    if not problems and st.button("Use this file", key=f"use_{key}", type="primary"):
        ok, msg = data_io.commit(key, clean)
        if ok:
            st.cache_resource.clear()
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)


# ===========================================================================
# BLOCK 3 — DEMAND FORECASTING ENGINE
# ===========================================================================
def page_forecast(R, agents):
    header("Block 3", "Demand Forecasting Engine",
           "Five methods run against every product; the lowest-MAPE model is selected "
           "and extended across the planning horizon.")
    steps(["3.1 Import Historical Demand", "3.2 Run Forecasting Methods",
           "3.3 Evaluate Forecast Accuracy", "3.4 Select Best Model",
           "3.5 Generate Demand Forecast"])

    sel, acc, fc, fit = (R["forecast_selection"], R["forecast_accuracy"],
                         R["forecast"], R["forecast_fit"])

    cards([(r["Item"], r["SelectedMethod"], f"MAPE {r['MAPE']}% · {r['Parameters']}")
           for _, r in sel.iterrows()] +
          [("Average MAPE", f"{sel['MAPE'].mean():.2f}%", "across all products"),
           ("Alerts", f"{int(sel['AlertFlag'].sum())}", "accuracy or bias")], per_row=5)

    st.markdown("")
    item = st.selectbox("Product", sorted(fc["Item"].unique()), key="fc_item")
    row = sel[sel["Item"] == item].iloc[0]

    g = fit[fit["Item"] == item].sort_values("Period")
    f = fc[fc["Item"] == item].sort_values("Period")
    offset = int(g["Period"].max())

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=g["Period"], y=g["Actual"], name="Actual demand",
                             mode="lines", line=dict(color=T.CATEGORICAL[0], width=2),
                             hovertemplate="Period %{x}<br>Actual %{y}<extra></extra>"))
    fig.add_trace(go.Scatter(x=g["Period"], y=g["Fitted"],
                             name=f"Fitted ({row['SelectedMethod']})", mode="lines",
                             line=dict(color=T.CATEGORICAL[1], width=2, dash="dot"),
                             hovertemplate="Period %{x}<br>Fitted %{y}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[offset + p for p in f["Period"]], y=f["Forecast"],
                             name="Forecast", mode="lines+markers",
                             line=dict(color=T.CATEGORICAL[2], width=2),
                             marker=dict(size=8, color=T.CATEGORICAL[2]),
                             hovertemplate="Horizon period %{text}<br>Forecast %{y}<extra></extra>",
                             text=f["Period"]))
    fig.add_vline(x=offset + 0.5, line=dict(color=T.AXIS, width=1, dash="dash"))
    fig.update_layout(**T.layout(f"{item} — history, fit and forward forecast",
                                 height=370, hovermode="x unified"))
    fig.update_xaxes(title="Period")
    fig.update_yaxes(title="Units")
    show(fig)

    if row["AlertFlag"]:
        note(f"**{item}: {row['Alert']}.** A tracking signal beyond ±4 means the model "
             f"is wrong in a consistent direction rather than randomly — the error "
             f"accumulates into the master schedule period after period.", "warn")
    else:
        note(f"**{item}: forecast is within tolerance.** MAPE {row['MAPE']}%, "
             f"tracking signal {row['TrackingSignal']} (limit ±4).", "good")

    c1, c2 = st.columns([1.3, 1])
    with c1:
        st.markdown("#### 3.3 How every method scored")
        a = acc[acc["Item"] == item].sort_values("MAPE")
        fig2 = go.Figure(go.Bar(
            x=a["Method"], y=a["MAPE"],
            marker=dict(color=[T.STATUS["good"] if m == row["SelectedMethod"]
                               else T.CATEGORICAL[0] for m in a["Method"]], line=dict(width=0)),
            text=[f"{v:.1f}%" for v in a["MAPE"]], textposition="outside",
            textfont=dict(size=11, color=T.INK_2),
            hovertemplate="%{x}<br>MAPE %{y:.2f}%<extra></extra>"))
        fig2.update_layout(**T.layout("Lower is better — selected model in green",
                                      height=300, showlegend=False))
        fig2.update_yaxes(title="MAPE %", range=[0, a["MAPE"].max() * 1.22])
        show(fig2)
    with c2:
        st.markdown("#### Error metrics")
        table(acc[acc["Item"] == item][["Method", "MAD", "RMSE", "MAPE", "Bias",
                                        "TrackingSignal"]].sort_values("MAPE"), height=300)

    st.markdown("#### 3.4 Selected model per product")
    table_with_download(sel[["Item", "SelectedMethod", "Parameters", "MAPE", "MAD",
                             "RMSE", "Bias", "TrackingSignal", "Alert"]],
                        "Forecast_Selection.csv", key="dl_fcsel")

    st.markdown("#### 3.5 Forecast handed to the master schedule")
    table_with_download(fc.pivot(index="Period", columns="Item",
                                 values="Forecast").reset_index(),
                        "Forecast_Output.csv", key="dl_fc")


# ===========================================================================
# BLOCK 4 — INVENTORY PLANNING
# ===========================================================================
def page_inventory(R, agents):
    header("Block 4", "Inventory Planning",
           "EOQ, reorder point and safety stock for every item, then a projection "
           "of stock across the horizon to find exceptions before they happen.")
    steps(["4.1 Import Inventory Master", "4.2 Calculate Parameters",
           "4.3 Project Inventory", "4.4 Identify Exceptions", "4.5 Output Report"])

    p, proj, exc = (R["inventory_params"], R["inventory_projection"],
                    R["inventory_exceptions"])

    cards([
        ("Inventory value", f"{p['InventoryValue'].sum():,.0f}", f"{len(p)} items"),
        ("Below safety stock", f"{int((p['OnHand'] < p['SafetyStock_Master']).sum())}",
         "items right now"),
        ("Avg periods of supply",
         f"{p['PeriodsOfSupply'].replace([float('inf')], None).dropna().mean():.1f}",
         "across all items"),
        ("Projected stockouts",
         f"{int((exc['ExceptionType'] == 'Projected Stockout').sum()) if not exc.empty else 0}",
         "items over the horizon"),
        ("Exceptions", f"{len(exc)}", "inventory module"),
    ])

    st.markdown("")
    st.markdown("#### 4.2 Stock position against safety stock and reorder point")
    view = st.radio("Show", ["Finished goods & sub-assemblies", "All items"],
                    horizontal=True, label_visibility="collapsed")
    d = p if view == "All items" else p[p["BOMLevel"] <= 1]
    d = d.sort_values("BOMLevel")

    fig = go.Figure()
    fig.add_trace(go.Bar(x=d["Item"], y=d["OnHand"], name="On hand",
                         marker=dict(color=T.CATEGORICAL[0], line=dict(width=0)),
                         hovertemplate="%{x}<br>On hand %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=d["Item"], y=d["SafetyStock_Master"], name="Safety stock",
                             mode="markers",
                             marker=dict(color=T.STATUS["warning"], size=10, symbol="line-ew",
                                         line=dict(color=T.STATUS["warning"], width=3)),
                             hovertemplate="%{x}<br>Safety stock %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=d["Item"], y=d["ReorderPoint"], name="Reorder point",
                             mode="markers",
                             marker=dict(color=T.CATEGORICAL[1], size=10, symbol="line-ew",
                                         line=dict(color=T.CATEGORICAL[1], width=3)),
                             hovertemplate="%{x}<br>Reorder point %{y:,.0f}<extra></extra>"))
    fig.update_layout(**T.layout(None, height=370, barmode="group"))
    fig.update_yaxes(title="Units")
    fig.update_xaxes(tickangle=-40)
    show(fig)
    note("A bar shorter than its orange reorder-point marker means the item should "
         "already be on order. A bar below the yellow safety-stock marker is a live "
         "exception.")

    st.markdown("#### 4.3 Projected inventory over the horizon")
    item = st.selectbox("Item", sorted(p["Item"]), key="inv_item")
    g = proj[proj["Item"] == item].sort_values("Period")
    if not g.empty:
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(x=g["Period"], y=g["Receipts"], name="Receipts",
                              marker=dict(color=T.CATEGORICAL[2], line=dict(width=0)),
                              hovertemplate="Period %{x}<br>Receipts %{y:,.0f}<extra></extra>"))
        fig2.add_trace(go.Bar(x=g["Period"], y=-g["Demand"], name="Demand",
                              marker=dict(color=T.CATEGORICAL[1], line=dict(width=0)),
                              customdata=g["Demand"],
                              hovertemplate="Period %{x}<br>Demand %{customdata:,.0f}<extra></extra>"))
        fig2.add_trace(go.Scatter(x=g["Period"], y=g["Closing"], name="Closing balance",
                                  mode="lines+markers",
                                  line=dict(color=T.CATEGORICAL[0], width=2),
                                  marker=dict(size=8, color=T.CATEGORICAL[0]),
                                  hovertemplate="Period %{x}<br>Closing %{y:,.0f}<extra></extra>"))
        fig2.add_hline(y=float(g["SafetyStock"].iloc[0]),
                       line=dict(color=T.STATUS["warning"], width=2, dash="dash"),
                       annotation_text="safety stock",
                       annotation_font=dict(size=10, color=T.MUTED))
        fig2.add_hline(y=0, line=dict(color=T.STATUS["critical"], width=1))
        fig2.update_layout(**T.layout(f"{item}", height=350, barmode="relative",
                                      hovermode="x unified"))
        fig2.update_xaxes(title="Period")
        fig2.update_yaxes(title="Units")
        show(fig2)
        table(g[["Period", "Opening", "Receipts", "Demand", "Closing", "OnOrder",
                 "SafetyStock", "ReorderPoint", "OrderPlaced"]])

    st.markdown("#### 4.5 Inventory parameters")
    table_with_download(p[["Item", "Description", "BOMLevel", "SourceType", "OnHand",
                           "AvgPeriodDemand", "LeadTime", "EOQ", "SafetyStock_Master",
                           "SafetyStock_Statistical", "ReorderPoint", "LotSizeRule",
                           "PeriodsOfSupply", "InventoryValue"]],
                        "Inventory_Parameters.csv", height=380, key="dl_invp")

    if not exc.empty:
        st.markdown("#### 4.4 Inventory exceptions")
        e = exc.copy()
        e.insert(0, "", e["Severity"].map(T.SEVERITY_ICON))
        table_with_download(e[["", "Severity", "Item", "ExceptionType", "Period",
                               "Message", "RecommendedAction"]],
                            "Inventory_Exceptions.csv", key="dl_invx")


# ===========================================================================
# BLOCK 5 — MASTER PRODUCTION SCHEDULING
# ===========================================================================
def page_mps(R, agents):
    header("Block 5", "Master Production Scheduling",
           "The projected available balance, the rough-cut capacity test, and the "
           "revision loop that runs until the schedule is feasible.")
    steps(["5.1 Inputs to MPS", "5.2 Create / Revise MPS", "5.3 Calculate PAB",
           "5.4 Check Feasibility", "5.5 MPS OK?"])

    mps, plan, hist = R["mps"], R["capacity_plan"], R["mps_history"]
    feasible = R["mps_feasible"]

    cards([
        ("Feasibility", "Feasible" if feasible else "Infeasible",
         f"after {R['mps_iterations']} iteration(s)"),
        ("Total MPS units", f"{mps['MPSQty'].sum():,.0f}",
         f"{int((mps['MPSQty'] > 0).sum())} production periods"),
        ("Peak utilisation", f"{plan['Utilisation'].max() * 100:.0f}%",
         f"at {plan.loc[plan['Utilisation'].idxmax(), 'WorkCentre']}"),
        ("Capacity overloads", f"{int(plan['Overloaded'].sum())}", "work-centre periods"),
        ("Open issues", f"{len(R['mps_issues'])}", "from the feasibility check"),
    ])

    st.markdown("")
    st.markdown("#### 5.5 The feasibility loop")
    if len(hist) > 1:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=hist["Iteration"], y=hist["CapacityOverloads"],
                             name="Capacity overloads",
                             marker=dict(color=T.CATEGORICAL[1], line=dict(width=0)),
                             text=hist["CapacityOverloads"], textposition="outside",
                             textfont=dict(size=11, color=T.INK_2),
                             hovertemplate="Iteration %{x}<br>%{y} overload(s)<extra></extra>"))
        fig.add_trace(go.Scatter(x=hist["Iteration"], y=hist["PeakUtilisation"] * 100,
                                 name="Peak utilisation %", mode="lines+markers",
                                 line=dict(color=T.CATEGORICAL[0], width=2),
                                 marker=dict(size=9, color=T.CATEGORICAL[0]),
                                 hovertemplate="Iteration %{x}<br>Peak %{y:.0f}%<extra></extra>"))
        fig.add_hline(y=100, line=dict(color=T.STATUS["critical"], width=1, dash="dash"),
                      annotation_text="100% capacity",
                      annotation_font=dict(size=10, color=T.MUTED))
        fig.update_layout(**T.layout("Overloads and peak utilisation by iteration", height=310))
        fig.update_xaxes(title="Iteration", dtick=1)
        fig.update_yaxes(title="Count / percent")
        show(fig)
        note(f"The first-pass schedule had {int(hist.iloc[0]['CapacityOverloads'])} "
             f"capacity overload(s) at {hist.iloc[0]['PeakUtilisation']*100:.0f}% peak "
             f"utilisation. Each iteration pulls production into an earlier period with "
             f"spare capacity — trading a little extra inventory for a feasible plan — "
             f"until the schedule {'clears' if feasible else 'stops improving'} at "
             f"{hist.iloc[-1]['PeakUtilisation']*100:.0f}%.",
             "good" if feasible else "crit")
    table(hist)

    st.markdown("#### 5.3 Master production schedule and PAB")
    item = st.selectbox("Product", sorted(mps["Item"].unique()), key="mps_item")
    g = mps[mps["Item"] == item].sort_values("Period")

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=g["Period"], y=g["MPSQty"], name="MPS quantity",
                          marker=dict(color=T.CATEGORICAL[0], line=dict(width=0)),
                          hovertemplate="Period %{x}<br>MPS %{y:,.0f}<extra></extra>"))
    fig2.add_trace(go.Scatter(x=g["Period"], y=g["GrossDemand"], name="Gross demand",
                              mode="lines+markers", line=dict(color=T.CATEGORICAL[1], width=2),
                              marker=dict(size=8, color=T.CATEGORICAL[1]),
                              hovertemplate="Period %{x}<br>Demand %{y:,.0f}<extra></extra>"))
    fig2.add_trace(go.Scatter(x=g["Period"], y=g["PAB"], name="Projected available balance",
                              mode="lines+markers", line=dict(color=T.CATEGORICAL[2], width=2),
                              marker=dict(size=8, color=T.CATEGORICAL[2]),
                              hovertemplate="Period %{x}<br>PAB %{y:,.0f}<extra></extra>"))
    fig2.add_hline(y=float(g["SafetyStock"].iloc[0]),
                   line=dict(color=T.STATUS["warning"], width=2, dash="dash"),
                   annotation_text="safety stock",
                   annotation_font=dict(size=10, color=T.MUTED))
    fig2.update_layout(**T.layout(f"{item}", height=350, hovermode="x unified"))
    fig2.update_xaxes(title="Period")
    fig2.update_yaxes(title="Units")
    show(fig2)

    table(g[["Period", "Forecast", "CustomerOrders", "GrossDemand", "OpeningPAB",
             "MPSQty", "PAB", "SafetyStock", "ATP"]])
    note("**PAB(t) = PAB(t−1) + MPS(t) − max(forecast, customer orders)**. "
         "ATP is what sales may still promise: the quantity arriving in a period "
         "minus the orders already booked against it before the next receipt.")
    download(mps, "MPS_Output.csv", "⬇ Download MPS_Output.csv", key="dl_mps")

    st.markdown("#### 5.4 Rough-cut capacity plan")
    pivot = plan.pivot(index="WorkCentre", columns="Period", values="Utilisation")
    fig3 = go.Figure(go.Heatmap(
        z=pivot.values * 100, x=[f"P{c}" for c in pivot.columns], y=pivot.index,
        colorscale=T.SEQUENTIAL_BLUE, zmin=0,
        zmax=max(100, plan["Utilisation"].max() * 100),
        colorbar=dict(title=dict(text="% util", font=dict(size=11, color=T.MUTED)),
                      tickfont=dict(size=10, color=T.MUTED), thickness=12),
        hovertemplate="%{y} · %{x}<br>%{z:.0f}% utilisation<extra></extra>",
        xgap=2, ygap=2))
    fig3.update_layout(**T.layout("Utilisation by work centre and period",
                                  height=290, showlegend=False))
    show(fig3)
    over = plan[plan["Overloaded"]]
    if over.empty:
        note("No work centre exceeds its available hours in any period — the master "
             "schedule is executable.", "good")
    else:
        note(f"{len(over)} work-centre period(s) still exceed available capacity even "
             f"after level-loading.", "crit")
    table_with_download(plan, "Capacity_Plan.csv", key="dl_cap")


# ===========================================================================
# BLOCK 6 — BOM EXPLOSION
# ===========================================================================
def page_bom(R, agents):
    header("Block 6", "BOM Explosion",
           "The multi-level product structure, low-level coding, and the gross "
           "requirements the master schedule generates.")
    steps(["6.1 Import BOM Structure", "6.2 Explode MPS Through BOM",
           "6.3 Calculate Gross Requirements", "6.4 Output Report"])

    tree, gr = R["bom_tree"], R["gross_requirements"]
    lvl = pd.Series(tree.low_level_code)

    cards([
        ("Items in structure", f"{len(tree.all_items)}", "across the BOM"),
        ("BOM levels", f"{tree.max_level() + 1}", f"level 0 to {tree.max_level()}"),
        ("Parent–component links", f"{len(R['data']['bom'])}", "relationships"),
        ("Shared components",
         f"{sum(1 for i in tree.all_items if len(tree.parents.get(i, [])) > 1)}",
         "used by more than one parent"),
        ("Gross requirement rows", f"{len(gr)}", "item × period"),
    ])

    st.markdown("")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        st.markdown("#### 6.1 Indented bill of materials")
        roots = sorted([i for i in tree.all_items if not tree.parents.get(i)])
        root = st.selectbox("Product", roots, key="bom_root")
        ind = tree.indented_bom(root)
        table(ind[["Indent", "Level", "QtyPer", "LowLevelCode"]]
              .rename(columns={"Indent": "Structure", "QtyPer": "Qty per unit"}),
              height=430)
        note("Quantity-per compounds scrap at every level — the engine issues "
             "**qty ÷ (1 − scrap)** at each step, so the figure at the bottom of a "
             "deep branch is larger than a naive multiplication would give.")
    with c2:
        st.markdown("#### Where used")
        allitems = sorted(tree.all_items)
        shared = max(allitems, key=lambda i: len(tree.parents.get(i, [])))
        default = allitems.index(shared)
        comp = st.selectbox("Component", allitems, key="bom_comp", index=default)
        wu = tree.where_used(comp)
        if wu.empty:
            st.caption(f"{comp} is a top-level item — nothing consumes it.")
        else:
            table(wu)
            st.caption(f"Low-level code {tree.low_level_code.get(comp)} — MRP nets "
                       f"{comp} only after all {len(wu)} parent(s) are planned.")

        st.markdown("#### Items by low-level code")
        counts = lvl.value_counts().sort_index()
        fig = go.Figure(go.Bar(
            x=[f"Level {i}" for i in counts.index], y=counts.values,
            marker=dict(color=[T.colour_for(i) for i in range(len(counts))], line=dict(width=0)),
            text=counts.values, textposition="outside",
            textfont=dict(size=11, color=T.INK_2),
            hovertemplate="%{x}<br>%{y} items<extra></extra>"))
        fig.update_layout(**T.layout(None, height=250, showlegend=False))
        fig.update_yaxes(title="Items", range=[0, counts.max() * 1.25])
        show(fig)

    st.markdown("#### 6.3 Gross requirements from the master schedule")
    table_with_download(gr, "Gross_Requirements.csv", height=380, key="dl_gr")


# ===========================================================================
# BLOCK 7 — MRP
# ===========================================================================
def page_mrp(R, agents):
    header("Block 7", "Material Requirements Planning",
           "Time-phased netting for every item, in low-level-code order, with "
           "lead-time offsetting and the lot-sizing policy applied.")
    steps(["7.1 Inputs to MRP", "7.2 Run MRP Logic", "7.3 Apply Lot Sizing",
           "7.4 Generate MRP Output", "7.5 Identify Material Exceptions"])

    mrp, rel, exc = R["mrp"], R["releases"], R["mrp_exceptions"]

    cards([
        ("Items planned", f"{mrp['Item'].nunique()}", "all BOM levels"),
        ("Planned orders", f"{int((mrp['PlannedOrderReceipt'] > 0).sum())}", "receipts"),
        ("Order releases", f"{len(rel)}", "make and buy"),
        ("Past-due releases", f"{int(rel['LateRelease'].sum()) if not rel.empty else 0}",
         "cannot meet lead time"),
        ("Exceptions", f"{len(exc)}", "MRP module"),
    ])

    st.markdown("")
    st.markdown("#### 7.4 MRP record")
    order = sorted(mrp["Item"].unique(),
                   key=lambda i: (R["bom_tree"].low_level_code.get(i, 0), i))
    item = st.selectbox("Item (in MRP processing order)", order, key="mrp_item")

    m = R["data"]["inventory"].set_index("Item").loc[item]
    c = st.columns(6)
    for col, (lab, val) in zip(c, [
            ("Low-level code", R["bom_tree"].low_level_code.get(item, 0)),
            ("Source", "Make" if m["SourceType"] == "M" else "Buy"),
            ("On hand", f"{m['OnHand']:,.0f}"),
            ("Safety stock", f"{m['SafetyStock']:,.0f}"),
            ("Lead time", f"{int(m['LeadTime'])} period(s)"),
            ("Lot rule", m["LotSizeRule"])]):
        col.markdown(T.card(lab, val, ""), unsafe_allow_html=True)

    st.write("")
    st.dataframe(item_grid(mrp, item), use_container_width=True)

    g = mrp[mrp["Item"] == item].sort_values("Period")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=g["Period"], y=g["GrossRequirement"], name="Gross requirements",
                         marker=dict(color=T.CATEGORICAL[1], line=dict(width=0)),
                         hovertemplate="Period %{x}<br>GR %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Bar(x=g["Period"], y=g["PlannedOrderReceipt"],
                         name="Planned order receipts",
                         marker=dict(color=T.CATEGORICAL[2], line=dict(width=0)),
                         hovertemplate="Period %{x}<br>Receipt %{y:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=g["Period"], y=g["ProjectedAvailable"],
                             name="Projected available", mode="lines+markers",
                             line=dict(color=T.CATEGORICAL[0], width=2),
                             marker=dict(size=8, color=T.CATEGORICAL[0]),
                             hovertemplate="Period %{x}<br>Available %{y:,.0f}<extra></extra>"))
    fig.add_hline(y=float(m["SafetyStock"]),
                  line=dict(color=T.STATUS["warning"], width=2, dash="dash"),
                  annotation_text="safety stock",
                  annotation_font=dict(size=10, color=T.MUTED))
    fig.update_layout(**T.layout(f"{item}", height=340, barmode="group",
                                 hovermode="x unified"))
    fig.update_xaxes(title="Period")
    fig.update_yaxes(title="Units")
    show(fig)
    note("**Available = PAB(t−1) + scheduled receipts − gross requirements.** If that "
         "falls below safety stock, the shortfall becomes the net requirement, the lot "
         "rule converts it into a planned order receipt, and the release is offset "
         "backwards by the lead time.")

    pegs = R["pegging"]
    if pegs is not None and not pegs.empty:
        pp = pegs[pegs["Item"] == item]
        if not pp.empty:
            with st.expander("Pegging — what drove these requirements"):
                table(pp)

    st.markdown("#### 7.2 Planned order releases")
    if not rel.empty:
        late = rel[rel["LateRelease"]]
        if not late.empty:
            note(f"🔴 **{len(late)} past-due release(s).** "
                 + "; ".join(f"{r['Item']} needs {r['Quantity']:,.0f} units in period "
                             f"{int(r['DuePeriod'])} but its {int(r['LeadTime'])}-period "
                             f"lead time means it should already have been ordered"
                             for _, r in late.iterrows()) + ".", "crit")
        table_with_download(rel, "Planned_Order_Releases.csv", height=320, key="dl_rel")

    st.markdown("#### 7.4 Full MRP output")
    table_with_download(R["mrp_output"], "MRP_Output.csv", height=380, key="dl_mrp")


# ===========================================================================
# BLOCK 8 — PRODUCTION ORDER RELEASE
# ===========================================================================
def page_release(R, agents):
    header("Block 8", "Production Order Release",
           "Every planned release is material-checked, then either released to the "
           "shop floor or held with its constraint classified.")
    steps(["8.1 Identify Candidate Orders", "8.2 Check Material Availability",
           "8.3 Materials Available?", "8.4 Classify Constraint",
           "8.5 Release To Shop Floor"])

    po, pr, summ = R["production_orders"], R["purchase_reqs"], R["release_summary"]

    cards([
        ("Candidate orders", f"{summ.get('total_candidates', 0)}", "from MRP"),
        ("Released", f"{summ.get('released', 0)}", "material available"),
        ("On hold", f"{summ.get('on_hold', 0)}", "constrained"),
        ("Purchase requisitions", f"{summ.get('purchase_reqs', 0)}",
         f"{summ.get('purchase_value', 0):,.0f} value"),
        ("Expedites needed", f"{summ.get('expedite_count', 0)}", "past-due purchases"),
    ])

    st.markdown("")
    c1, c2 = st.columns([1, 1.4])
    with c1:
        st.markdown("#### 8.3 / 8.4 Release decision")
        counts = po["ConstraintType"].value_counts() if not po.empty else pd.Series(dtype=int)
        if not counts.empty:
            palette = {"None": T.STATUS["good"],
                       "Material Constrained": T.STATUS["critical"],
                       "Waiting for Supplier": T.STATUS["serious"],
                       "Capacity Constrained": T.STATUS["warning"]}
            labels = ["Released" if c == "None" else c for c in counts.index]
            fig = go.Figure(go.Bar(
                x=counts.values, y=labels, orientation="h",
                marker=dict(color=[palette.get(c, T.MUTED) for c in counts.index],
                            line=dict(width=0)),
                text=counts.values, textposition="outside",
                textfont=dict(size=11, color=T.INK_2),
                hovertemplate="%{y}<br>%{x} order(s)<extra></extra>"))
            fig.update_layout(**T.layout(None, height=250, showlegend=False))
            fig.update_xaxes(range=[0, counts.max() * 1.25], showticklabels=False)
            fig.update_yaxes(showgrid=False)
            show(fig)
    with c2:
        st.markdown("#### What is blocking the held orders")
        held = po[po["Status"] == "On Hold"] if not po.empty else pd.DataFrame()
        if held.empty:
            note("Nothing is on hold — every candidate order has its material.", "good")
        else:
            blockers = {}
            for _, o in held.iterrows():
                txt = str(o["Notes"]).replace("Missing:", "").replace("Awaiting:", "")
                for part in txt.split(","):
                    name = part.strip().split(" (")[0].strip()
                    if name and name != "All components available":
                        blockers[name] = blockers.get(name, 0) + 1
            if blockers:
                b = pd.DataFrame(sorted(blockers.items(), key=lambda kv: -kv[1]),
                                 columns=["Component", "Orders blocked"])
                fig = go.Figure(go.Bar(
                    x=b["Orders blocked"], y=b["Component"], orientation="h",
                    marker=dict(color=T.STATUS["critical"], line=dict(width=0)),
                    text=b["Orders blocked"], textposition="outside",
                    textfont=dict(size=11, color=T.INK_2),
                    hovertemplate="%{y} blocks %{x} order(s)<extra></extra>"))
                fig.update_layout(**T.layout(None, height=250, showlegend=False))
                fig.update_xaxes(range=[0, b["Orders blocked"].max() * 1.25],
                                 showticklabels=False)
                fig.update_yaxes(showgrid=False, autorange="reversed")
                show(fig)
                note(f"Clearing **{b.iloc[0]['Component']}** alone would release "
                     f"{b.iloc[0]['Orders blocked']} order(s) — the highest-leverage "
                     f"intervention available.", "warn")

    st.markdown("#### 8.5 Production orders")
    if not po.empty:
        f = st.multiselect("Filter by status", sorted(po["Status"].unique()),
                           default=sorted(po["Status"].unique()))
        table_with_download(po[po["Status"].isin(f)], "Production_Orders.csv",
                            height=360, key="dl_po")

    st.markdown("#### Purchase requisitions")
    if not pr.empty:
        table_with_download(pr, "Purchase_Requisitions.csv", height=300, key="dl_pr")


# ===========================================================================
# BLOCK 9 — ROUTING & CAPACITY PREPARATION
# ===========================================================================
def page_routing(R, agents):
    header("Block 9", "Routing & Capacity Preparation",
           "Released orders become an executable job list: every operation assigned "
           "to a work centre with its setup and run time.")
    steps(["9.1 Import Routing & Work Centre Data", "9.2 Import Machine Capacity",
           "9.3 Generate Executable Job List"])

    jobs, ops, cap = R["jobs"], R["operations"], R["data"]["capacity"]
    lo, hi = R["schedule_window"]

    cards([
        ("Jobs ready", f"{len(jobs)}", f"periods {lo}–{hi}"),
        ("Operations", f"{len(ops)}", "steps to schedule"),
        ("Work centres", f"{len(cap)}", "in the routing"),
        ("Total work", f"{ops['ProcessHrs'].sum():,.0f} h" if not ops.empty else "0 h",
         "setup + run"),
        ("Units to build", f"{jobs['Quantity'].sum():,.0f}" if not jobs.empty else "0",
         "across all jobs"),
    ])

    st.markdown("")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("#### 9.2 Capacity available per work centre")
        cc = cap.sort_values("AvailableHoursPerPeriod")
        fig = go.Figure(go.Bar(
            x=cc["AvailableHoursPerPeriod"], y=cc["WorkCentre"], orientation="h",
            marker=dict(color=T.CATEGORICAL[0], line=dict(width=0)),
            text=[f"{v:.0f} h" for v in cc["AvailableHoursPerPeriod"]],
            textposition="outside", textfont=dict(size=11, color=T.INK_2),
            customdata=cc[["Description", "NumMachines", "ShiftsPerDay", "Efficiency"]].values,
            hovertemplate=("%{y} — %{customdata[0]}<br>%{x:.1f} hours per period<br>"
                           "%{customdata[1]} machine(s), %{customdata[2]} shift(s), "
                           "%{customdata[3]:.0%} efficiency<extra></extra>")))
        fig.update_layout(**T.layout(None, height=280, showlegend=False))
        fig.update_xaxes(range=[0, cc["AvailableHoursPerPeriod"].max() * 1.25],
                         showticklabels=False)
        fig.update_yaxes(showgrid=False)
        show(fig)
        note("**Machines × hours per shift × shifts per day × working days × efficiency.** "
             "The smallest bar is the structural constraint on the whole plant.")
    with c2:
        st.markdown("#### 9.3 Work queued at each work centre")
        if not ops.empty:
            load = (ops.groupby("WorkCentre")["ProcessHrs"].sum()
                    .reindex(cap["WorkCentre"]).fillna(0).sort_values())
            fig2 = go.Figure(go.Bar(
                x=load.values, y=load.index, orientation="h",
                marker=dict(color=T.CATEGORICAL[2], line=dict(width=0)),
                text=[f"{v:.0f} h" for v in load.values], textposition="outside",
                textfont=dict(size=11, color=T.INK_2),
                hovertemplate="%{y}<br>%{x:.1f} hours of queued work<extra></extra>"))
            fig2.update_layout(**T.layout(None, height=280, showlegend=False))
            fig2.update_xaxes(range=[0, max(load.values) * 1.25], showticklabels=False)
            fig2.update_yaxes(showgrid=False)
            show(fig2)
            st.caption(f"Total work content in the {lo}–{hi} window, before sequencing.")

    st.markdown("#### 9.1 Routing master")
    table_with_download(R["data"]["routing"], "Routing_WorkCentre.csv", key="dl_rt")

    st.markdown("#### 9.2 Machine capacity")
    table_with_download(cap, "Machine_Capacity.csv", key="dl_mc")

    st.markdown("#### 9.3 Executable job list")
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Jobs")
        table_with_download(jobs, "Job_List.csv", height=320, key="dl_jobs")
    with c2:
        st.caption("Operations")
        table_with_download(ops, "Operations.csv", height=320, key="dl_ops")


# ===========================================================================
# BLOCK 10 — SHOP-FLOOR SCHEDULING
# ===========================================================================
def page_scheduling(R, agents):
    header("Block 10", "Shop-Floor Scheduling (Simulation)",
           "A discrete-event job-shop simulation: jobs queue at each work centre and "
           "are sequenced by the selected dispatching rule.")
    steps(["10.1 Select Dispatching Rule", "10.2 Run Scheduling Simulation",
           "10.3 Generate Gantt Chart", "10.4 Calculate Performance Measures",
           "10.5 Store Results"])

    rule = R["selected_rule"]
    gantt, jobs, wc = R["gantt"], R["job_results"], R["work_centre_results"]
    k = R["kpis"]

    if gantt.empty:
        note("No jobs in the current scheduling window. Widen it in the sidebar.", "warn")
        return

    st.caption(f"**{rule}** — {RULE_DESCRIPTIONS.get(rule, '')}")
    cards([
        ("Jobs scheduled", f"{k.get('sched_jobs')}", f"{len(gantt)} operations"),
        ("Makespan", f"{k.get('sched_makespan'):.0f} h",
         f"{k.get('sched_makespan_periods'):.1f} periods"),
        ("On-time delivery", f"{k.get('sched_otd')}%", f"{k.get('sched_tardy_jobs')} late"),
        ("Avg flow time", f"{k.get('sched_avg_flow'):.1f} h",
         f"waiting {jobs['WaitingTime'].mean():.1f} h"),
        ("Bottleneck", f"{k.get('sched_bottleneck')}",
         f"{wc['Utilisation'].max()*100:.0f}% utilised"),
    ])

    st.markdown("")
    st.markdown("#### 10.3 Gantt chart")
    by = st.radio("Colour by", ["Work centre", "Item"], horizontal=True,
                  label_visibility="collapsed")
    key = "WorkCentre" if by == "Work centre" else "Item"
    colours = T.series_colours(gantt[key].unique())

    fig = go.Figure()
    machines = sorted(gantt["Machine"].unique(), reverse=True)
    for name in sorted(gantt[key].unique()):
        g = gantt[gantt[key] == name]
        fig.add_trace(go.Bar(
            x=g["End"] - g["Start"], y=g["Machine"], base=g["Start"],
            orientation="h", name=name,
            marker=dict(color=colours[name], line=dict(color=T.SURFACE, width=2)),
            customdata=g[["JobID", "Item", "Operation", "Quantity", "Start", "End",
                          "WaitHrs"]].values,
            hovertemplate=("<b>%{customdata[0]}</b> · %{customdata[1]}<br>"
                           "%{customdata[2]}<br>Qty %{customdata[3]:,.0f}<br>"
                           "Start %{customdata[4]:.1f} h → end %{customdata[5]:.1f} h<br>"
                           "Waited %{customdata[6]:.1f} h<extra></extra>")))
    fig.update_layout(**T.layout(None, height=max(320, 26 * len(machines) + 90),
                                 barmode="overlay"))
    fig.update_xaxes(title="Hours from start of the planning window", showgrid=True,
                     gridcolor=T.GRID)
    fig.update_yaxes(title=None, categoryorder="array", categoryarray=machines,
                     showgrid=False)
    show(fig)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 10.4 Work centre utilisation")
        w = wc.sort_values("Utilisation", ascending=True)
        fig2 = go.Figure(go.Bar(
            x=w["Utilisation"] * 100, y=w["WorkCentre"], orientation="h",
            marker=dict(color=[T.STATUS["critical"] if b else T.CATEGORICAL[0]
                               for b in w["Bottleneck"]], line=dict(width=0)),
            text=[f"{v*100:.0f}%" for v in w["Utilisation"]], textposition="outside",
            textfont=dict(size=11, color=T.INK_2),
            customdata=w[["Description", "BusyHours", "IdleHours", "SetupHours"]].values,
            hovertemplate=("%{y} — %{customdata[0]}<br>Utilisation %{x:.0f}%<br>"
                           "Busy %{customdata[1]:.0f} h · idle %{customdata[2]:.0f} h<br>"
                           "Setup %{customdata[3]:.0f} h<extra></extra>")))
        fig2.update_layout(**T.layout(None, height=280, showlegend=False))
        fig2.update_xaxes(range=[0, 118], showticklabels=False)
        fig2.update_yaxes(showgrid=False)
        show(fig2)
        st.caption("🔴 marks the bottleneck — it sets plant throughput.")
    with c2:
        st.markdown("#### Job completion against due date")
        j = jobs.sort_values("Completion")
        fig3 = go.Figure(go.Bar(
            x=j["JobID"], y=j["Lateness"],
            marker=dict(color=[T.STATUS["critical"] if v > 0 else T.CATEGORICAL[2]
                               for v in j["Lateness"]], line=dict(width=0)),
            customdata=j[["Item", "Completion", "DueHrs", "Tardiness"]].values,
            hovertemplate=("<b>%{x}</b> · %{customdata[0]}<br>"
                           "Completed %{customdata[1]:.1f} h, due %{customdata[2]:.1f} h<br>"
                           "Lateness %{y:.1f} h<extra></extra>")))
        fig3.add_hline(y=0, line=dict(color=T.AXIS, width=1))
        fig3.update_layout(**T.layout(None, height=280, showlegend=False))
        fig3.update_xaxes(title="Job", showticklabels=False)
        fig3.update_yaxes(title="Lateness (h)")
        show(fig3)
        st.caption("Bars above the line are late. Green bars finished early.")

    st.markdown("#### 10.4 Job results")
    table_with_download(jobs, "Job_Results.csv", height=320, key="dl_jr")
    st.markdown("#### Work centre results")
    table_with_download(wc, "WorkCentre_Results.csv", key="dl_wcr")
    st.markdown("#### 10.3 Operation schedule")
    table_with_download(gantt, "Schedule_Gantt.csv", height=320, key="dl_gantt")


# ===========================================================================
# BLOCK 11 — PERFORMANCE COMPARISON
# ===========================================================================
def page_comparison(R, agents):
    header("Block 11", "Performance Comparison",
           "The identical job set is re-simulated under every dispatching rule, then "
           "compared on every performance measure.")
    steps(["11.1 Repeat Scheduling for All Rules", "11.2 Compare KPI Results",
           "11.3 Identify Best Rule by Criterion", "11.4 Dashboard View"])

    cmp, best = R["rule_comparison"], R["best_by_criterion"]
    if cmp.empty:
        note("No jobs to compare in the current window.", "warn")
        return

    metrics = [
        ("Makespan", "Makespan", "h", True),
        ("AvgFlowTime", "Average flow time", "h", True),
        ("AvgWaitingTime", "Average waiting time", "h", True),
        ("AvgTardiness", "Average tardiness", "h", True),
        ("OnTimeDeliveryPct", "On-time delivery", "%", False),
        ("AvgWIP", "Average WIP", "jobs", True),
    ]

    st.markdown("#### 11.2 Every rule on every measure")
    cols = st.columns(3)
    for i, (col, label, unit, lower_better) in enumerate(metrics):
        with cols[i % 3]:
            s = cmp[col]
            winner = s.idxmin() if lower_better else s.idxmax()
            fig = go.Figure(go.Bar(
                x=s.index, y=s.values,
                marker=dict(color=[T.STATUS["good"] if r == winner else T.CATEGORICAL[0]
                                   for r in s.index], line=dict(width=0)),
                text=[f"{v:,.1f}" for v in s.values], textposition="outside",
                textfont=dict(size=10, color=T.INK_2),
                hovertemplate="%{x}<br>%{y:,.2f} " + unit + "<extra></extra>"))
            fig.update_layout(**T.layout(
                f"{label} ({'lower' if lower_better else 'higher'} is better)",
                height=250, showlegend=False))
            fig.update_yaxes(title=unit, range=[0, s.max() * 1.22] if s.max() > 0 else None)
            show(fig)

    note("Green marks the winner on each measure. **No rule wins everything** — SPT "
         "runs short jobs first, which minimises flow time and WIP but pushes long "
         "jobs past their due dates; FCFS and EDD protect due dates at the cost of "
         "flow time. Choosing a rule is choosing which of these you care about.")

    st.markdown("#### 11.3 Best rule by criterion")
    table_with_download(best, "Best_Rule_By_Criterion.csv", key="dl_best")

    st.markdown("#### 11.2 Full comparison table")
    table_with_download(cmp.reset_index(), "Rule_Comparison.csv", height=260, key="dl_cmp")

    st.markdown("#### Rule definitions")
    table(pd.DataFrame([{"Rule": r, "Description": RULE_DESCRIPTIONS[r]}
                        for r in DISPATCH_RULES if r in RULE_DESCRIPTIONS]))

    st.markdown("#### KPI formula reference")
    table(kpi_definitions(), height=380)


# ===========================================================================
# BLOCK 12 — EXCEPTION MANAGEMENT & MANAGERIAL DECISION
# ===========================================================================
def page_exceptions(R, agents):
    header("Block 12", "Exception Management & Managerial Decision",
           "Exceptions detected across every module, each with executable recommended "
           "actions. Approving one re-plans the factory.")
    steps(["12.1 Detect Exceptions", "12.2 Show Recommended Actions",
           "12.3 Manager Decision", "12.4 Implement Decision in System"])

    exc = R["exceptions"]
    log = get_decision_log()

    cards([
        ("Open exceptions", f"{len(exc)}", "all modules"),
        ("Critical", f"{int((exc['Severity'] == 'Critical').sum()) if not exc.empty else 0}",
         "need action now"),
        ("High", f"{int((exc['Severity'] == 'High').sum()) if not exc.empty else 0}",
         "need attention"),
        ("Modules affected", f"{exc['Module'].nunique() if not exc.empty else 0}", "of 6"),
        ("Decisions logged", f"{len(log.entries)}", "this session"),
    ])

    if exc.empty:
        note("No exceptions detected — the plan is clean.", "good")
        return

    st.markdown("")
    c1, c2 = st.columns(2)
    with c1:
        counts = exc["Severity"].value_counts()
        fig = go.Figure(go.Bar(
            x=counts.index, y=counts.values,
            marker=dict(color=[T.SEVERITY_COLOUR.get(s, T.MUTED) for s in counts.index],
                        line=dict(width=0)),
            text=counts.values, textposition="outside",
            textfont=dict(size=11, color=T.INK_2),
            hovertemplate="%{x}: %{y} exception(s)<extra></extra>"))
        fig.update_layout(**T.layout("By severity", height=250, showlegend=False))
        fig.update_yaxes(range=[0, counts.max() * 1.25])
        show(fig)
    with c2:
        mc = exc["Module"].value_counts()
        fig2 = go.Figure(go.Bar(
            x=mc.values, y=mc.index, orientation="h",
            marker=dict(color=T.CATEGORICAL[0], line=dict(width=0)),
            text=mc.values, textposition="outside",
            textfont=dict(size=11, color=T.INK_2),
            hovertemplate="%{y}: %{x} exception(s)<extra></extra>"))
        fig2.update_layout(**T.layout("By module", height=250, showlegend=False))
        fig2.update_xaxes(range=[0, mc.max() * 1.3], showticklabels=False)
        fig2.update_yaxes(showgrid=False)
        show(fig2)

    st.markdown("#### 12.1 Exception register")
    sev_filter = st.multiselect("Severity", ["Critical", "High", "Medium", "Low"],
                                default=["Critical", "High", "Medium", "Low"])
    view = exc[exc["Severity"].isin(sev_filter)].copy()
    view.insert(0, "", view["Severity"].map(T.SEVERITY_ICON))
    table(view[["", "ExceptionID", "Module", "Item", "ExceptionType", "Severity",
                "Period", "Message", "RecommendedAction"]], height=320)
    download(exc, "Exception_Register.csv", "⬇ Download Exception_Register.csv",
             key="dl_exc")

    st.markdown("#### 12.2 – 12.4 Recommended actions and manager decision")
    if view.empty:
        st.caption("No exceptions match the filter.")
        return

    ids = view["ExceptionID"].tolist()
    chosen = st.selectbox("Exception", ids, format_func=lambda x: (
        f"{x} — {view[view['ExceptionID'] == x]['Item'].iloc[0]}: "
        f"{view[view['ExceptionID'] == x]['ExceptionType'].iloc[0]}"))
    ex = exc[exc["ExceptionID"] == chosen].iloc[0]

    kind = {"Critical": "crit", "High": "warn", "Medium": "warn"}.get(ex["Severity"], "note")
    note(f"{T.SEVERITY_ICON.get(ex['Severity'])} **{ex['ExceptionType']} — {ex['Item']}** "
         f"({ex['Module']}, period {ex['Period']})<br>{ex['Message']}", kind)

    im = R["data"]["inventory"].set_index("Item").to_dict("index")
    options = recommended_actions(ex, im, R["data"]["supplier"])
    labels = [o["label"] for o in options]
    pick = st.radio("Recommended actions", labels, key=f"act_{chosen}")
    opt = next(o for o in options if o["label"] == pick)
    st.caption(opt["description"])
    if opt["overrides"]:
        st.code(json.dumps(opt["overrides"], indent=2), language="json")
        st.caption(f"Approving this re-runs the pipeline from the **{opt['rerun_from']}** "
                   f"block onward.")

    d1, d2, d3 = st.columns([1, 1, 2])
    decision = d1.selectbox("Decision", ["Approved", "Modified", "Rejected"])
    who = d2.text_input("Decided by", value="Planner")
    notes_txt = d3.text_input("Notes", placeholder="Reason, authority, follow-up…")

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Record decision", type="primary"):
        log.record(chosen, ex["Item"], ex["ExceptionType"], decision, opt["label"],
                   opt["overrides"], opt["rerun_from"], notes_txt, who)
        st.cache_resource.clear()
        st.success(f"Recorded: {decision} — {opt['label']}.")
        st.rerun()
    if b2.button("Clear all decisions"):
        log.clear()
        st.cache_resource.clear()
        st.rerun()

    if log.entries:
        st.markdown("#### 12.3 Decision log")
        table_with_download(log.to_frame(), "Decision_Log.csv", key="dl_dlog")
        active = log.active_overrides()
        if active:
            st.markdown("#### 12.4 Overrides currently applied")
            st.code(json.dumps(active, indent=2), language="json")
            note(f"These are live — the plan across the whole dashboard has been "
                 f"re-planned from the **{log.earliest_rerun()}** block.", "good")


# ===========================================================================
# BLOCK 13 — SCENARIO SIMULATION & REPORTING
# ===========================================================================
def page_scenarios(R, agents):
    header("Block 13", "Scenario Simulation & Reporting",
           "Disruption scenarios run through the entire pipeline and are compared "
           "against the current plan.")
    steps(["13.1 Run What-If / Disruption Scenarios", "13.2 Analyse Impact",
           "13.3 Document Results", "13.4 Prepare Deliverables",
           "13.5 Final Presentation"])

    library = build_scenarios(R["data"], R)
    st.markdown("#### 13.1 Scenario library")
    note("These scenarios are **built from your data** — the surge lands on your "
         "highest-volume product, the delay hits your least reliable supplier, and "
         "the breakdown takes out your tightest work centre.")
    table(scenario_catalogue(library))

    names = [n for n in library if n != "Baseline"]
    name = st.selectbox("Scenario", names)
    spec = library[name]
    st.caption(spec["description"])
    with st.expander("Overrides this scenario applies"):
        st.code(json.dumps(spec["overrides"], indent=2), language="json")

    if st.button("Run scenario", type="primary", key="run_scenario"):
        with st.spinner("Re-planning the factory under this scenario…"):
            st.session_state["scenario_out"] = agents.run_scenario(
                name, R["schedule_window"], library=library)
            st.session_state["scenario_name"] = name

    out = st.session_state.get("scenario_out")
    if out:
        nm = st.session_state.get("scenario_name", "Scenario")
        res, comp = out["result"], out["comparison"]

        st.markdown(f"#### 13.2 Impact — {nm}")
        delta = res["health_score"] - R["health_score"]
        cards([
            ("Plan health", f"{res['health_score']}/100",
             f"{delta:+.1f} vs baseline {R['health_score']}"),
            ("MPS", "Feasible" if res["kpis"].get("mps_feasible") else "Infeasible",
             f"{res['kpis'].get('mps_iterations')} iteration(s)"),
            ("Orders on hold", f"{res['kpis'].get('orders_on_hold')}",
             f"baseline {R['kpis'].get('orders_on_hold')}"),
            ("On-time delivery", f"{res['kpis'].get('sched_otd')}%",
             f"baseline {R['kpis'].get('sched_otd')}%"),
            ("Exceptions", f"{res['kpis'].get('exceptions_total')}",
             f"baseline {R['kpis'].get('exceptions_total')}"),
        ])
        st.markdown("")
        note(out["narrative"], "crit" if delta < -10 else "warn" if delta < 0 else "good")

        changed = comp[comp["Impact"] != "No change"].copy()
        if not changed.empty:
            plot = changed[changed["ChangePct"].notna()].sort_values("ChangePct")
            if not plot.empty:
                fig = go.Figure(go.Bar(
                    x=plot["ChangePct"], y=plot["KPI"], orientation="h",
                    marker=dict(color=[T.STATUS["critical"] if i == "Worse"
                                       else T.STATUS["good"] if i == "Improved"
                                       else T.MUTED for i in plot["Impact"]],
                                line=dict(width=0)),
                    customdata=plot[["Baseline", "Scenario", "Impact"]].values,
                    hovertemplate=("%{y}<br>%{customdata[0]:,.2f} → %{customdata[1]:,.2f}"
                                   "<br>%{x:+.1f}% — %{customdata[2]}<extra></extra>")))
                fig.add_vline(x=0, line=dict(color=T.AXIS, width=1))
                fig.update_layout(**T.layout(
                    "Percentage change vs baseline — red is worse, green is better",
                    height=max(300, 26 * len(plot) + 90), showlegend=False))
                fig.update_xaxes(title="% change")
                fig.update_yaxes(showgrid=False)
                show(fig)
            st.markdown("#### 13.3 Documented results")
            table_with_download(
                changed[["KPI", "Baseline", "Scenario", "Change", "ChangePct", "Impact"]],
                f"Scenario_{nm[:20].replace(' ', '_')}.csv", key="dl_scen")

    st.markdown("---")
    st.markdown("#### 13.1 Ask a what-if in plain English")
    st.caption("The scenario agent parses the request into engine overrides, runs the "
               "whole pipeline, and reports the difference. It always shows you how it "
               "read your request so you can correct it.")

    examples = _whatif_examples(R)
    ex_pick = st.selectbox("Examples", ["— type your own —"] + examples)
    default = "" if ex_pick.startswith("—") else ex_pick
    q = st.text_input("Your what-if", value=default, key="whatif_text",
                      placeholder="what if …")

    if st.button("Run what-if", key="run_whatif") and q.strip():
        with st.spinner("Interpreting and re-planning…"):
            out2 = agents.what_if(q, R["schedule_window"])
        if not out2["ok"]:
            note(out2["explanation"], "warn")
        else:
            note(f"**{out2['explanation']}**", "note")
            st.code(json.dumps(out2["overrides"], indent=2), language="json")
            res2 = out2["result"]
            d2 = res2["health_score"] - R["health_score"]
            cards([
                ("Plan health", f"{res2['health_score']}/100", f"{d2:+.1f}"),
                ("MPS", "Feasible" if res2["kpis"].get("mps_feasible") else "Infeasible", ""),
                ("Orders on hold", f"{res2['kpis'].get('orders_on_hold')}",
                 f"baseline {R['kpis'].get('orders_on_hold')}"),
                ("On-time delivery", f"{res2['kpis'].get('sched_otd')}%",
                 f"baseline {R['kpis'].get('sched_otd')}%"),
                ("Exceptions", f"{res2['kpis'].get('exceptions_total')}",
                 f"baseline {R['kpis'].get('exceptions_total')}"),
            ])
            st.markdown("")
            note(out2["narrative"], "crit" if d2 < -10 else "warn" if d2 < 0 else "good")
            ch = out2["comparison"]
            table(ch[ch["Impact"] != "No change"][
                ["KPI", "Baseline", "Scenario", "Change", "ChangePct", "Impact"]])

    st.markdown("---")
    st.markdown("#### 13.4 Prepare deliverables")
    c1, c2, c3 = st.columns(3)
    c1.download_button("📗 Everything as Excel", data=data_io.to_excel(R),
                       file_name="Control_Tower_Full.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True, key="xl_b13")
    c2.download_button("🗜 Everything as ZIP of CSVs", data=data_io.to_zip(R),
                       file_name="Control_Tower_All.zip", mime="application/zip",
                       use_container_width=True, key="zip_b13")
    c3.download_button("📁 Just the 9 input files", data=data_io.inputs_zip(),
                       file_name="Control_Tower_Inputs.zip", mime="application/zip",
                       use_container_width=True, key="inzip_b13")


# ===========================================================================
# ALL DATA — every input and output table in one place
# ===========================================================================
def page_all_data(R, agents):
    header("All Data", "Every Table in One Place",
           "All nine input files and all twenty result tables, searchable, with "
           "download buttons on each.")

    inputs = {f"{f['step']}  {f['label']}": data_io.read_active(f["key"])
              for f in data_io.FILES}
    outputs = data_io.output_tables(R)

    total_rows = (sum(len(d) for d in inputs.values())
                  + sum(len(d) for d in outputs.values() if d is not None))
    with_rows = len([d for d in inputs.values() if not d.empty])
    cards([
        ("Input tables", f"{len(inputs)}",
         f"{with_rows} with data, {len(inputs) - with_rows} template"),
        ("Result tables", f"{len([d for d in outputs.values() if d is not None and not d.empty])}",
         "generated"),
        ("Total rows", f"{total_rows:,}", "across every table"),
        ("Items", f"{len(R['data']['inventory'])}", "in the plan"),
        ("Plan health", f"{R['health_score']}/100", "current run"),
    ])

    st.markdown("")
    c1, c2, c3 = st.columns(3)
    c1.download_button("📗 Download everything as Excel", data=data_io.to_excel(R),
                       file_name="Control_Tower_Full.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True, key="xl_all")
    c2.download_button("🗜 Download everything as ZIP", data=data_io.to_zip(R),
                       file_name="Control_Tower_All.zip", mime="application/zip",
                       use_container_width=True, key="zip_all")
    c3.download_button("📁 Download the 9 input files", data=data_io.inputs_zip(),
                       file_name="Control_Tower_Inputs.zip", mime="application/zip",
                       use_container_width=True, key="inzip_all")

    st.markdown("")
    tab_in, tab_out, tab_index = st.tabs(
        ["📥 Input data (9 tables)", "📤 Results (20 tables)", "🔎 Table index"])

    def browser(tables, prefix):
        names = [n for n, d in tables.items() if d is not None and not d.empty]
        if not names:
            st.caption("Nothing to show yet.")
            return
        c1, c2 = st.columns([2, 3])
        pick = c1.selectbox("Table", names, key=f"{prefix}_pick")
        df = tables[pick]
        term = c2.text_input("Search this table", key=f"{prefix}_search",
                             placeholder="Type to filter rows — e.g. an item code")
        view = df
        if term.strip():
            mask = df.astype(str).apply(
                lambda col: col.str.contains(term.strip(), case=False, na=False))
            view = df[mask.any(axis=1)]
        st.caption(f"{len(view):,} of {len(df):,} rows · {len(df.columns)} columns")
        table(view, height=460)
        download(view if term.strip() else df,
                 f"{pick.split('  ')[-1].replace(' ', '_')}.csv",
                 "⬇ Download this table", key=f"{prefix}_dl")

    with tab_in:
        st.caption("The nine files the plan is built from. Replace any of them on the "
                   "Block 2 page.")
        browser(inputs, "in")

    with tab_out:
        st.caption("Every table the planning run produced, in flow-chart order.")
        browser(outputs, "out")

    with tab_index:
        st.caption("What every table is, and which block produced it.")
        rows = []
        for f in data_io.FILES:
            df = data_io.read_active(f["key"])
            rows.append({"Type": "Input", "Block": "2", "Table": f["label"],
                         "Rows": len(df), "Columns": len(df.columns),
                         "What it holds": f["help"]})
        meta = {
            "Forecast": ("3", "Forward demand forecast per product per period"),
            "Forecast Accuracy": ("3", "Every method scored on every product"),
            "Forecast Selection": ("3", "The model chosen per product and why"),
            "Inventory Params": ("4", "EOQ, reorder point, safety stock per item"),
            "Inventory Projection": ("4", "Stock projected across the horizon"),
            "MPS": ("5", "Master schedule with PAB and available-to-promise"),
            "Capacity Plan": ("5", "Hours required vs available per work centre"),
            "Gross Requirements": ("6", "BOM explosion of the master schedule"),
            "MRP Output": ("7", "The MRP grid: GR, SR, PAB, NR, PORcpt, PORel"),
            "Planned Releases": ("7", "Orders to release, with lead-time offset"),
            "Production Orders": ("8", "Make orders, released or held with a reason"),
            "Purchase Reqs": ("8", "Buy orders with supplier and value"),
            "Job List": ("9", "Released orders as schedulable jobs"),
            "Schedule Gantt": ("10", "Every operation with start and end time"),
            "Job Results": ("10", "Flow time, lateness and tardiness per job"),
            "Work Centre Results": ("10", "Utilisation, busy, idle and setup hours"),
            "Rule Comparison": ("11", "All dispatching rules on all measures"),
            "Best Rule": ("11", "Winning rule for each criterion"),
            "Exceptions": ("12", "Every exception with severity and action"),
            "KPI Summary": ("11", "One row with every headline figure"),
        }
        for name, df in outputs.items():
            if df is None:
                continue
            blk, desc = meta.get(name, ("-", ""))
            rows.append({"Type": "Result", "Block": blk, "Table": name,
                         "Rows": len(df), "Columns": len(df.columns),
                         "What it holds": desc})
        table(pd.DataFrame(rows), height=560)


# ===========================================================================
# AGENT LAYER
# ===========================================================================
def page_agents(R, agents):
    header("Agent Layer", "Multi-Agent Analysis",
           "Eight specialised agents read the plan, post findings to a shared "
           "blackboard, and a coordinator synthesises them into a briefing.")

    note("**Agents never do arithmetic.** Every figure below is read from a table the "
         "engine computed, so any number can be traced back to an MRP row or a "
         "simulation result.")

    st.markdown("#### The crew")
    table(agents.roster())

    st.markdown("#### Management briefing")
    st.markdown(agents.briefing())

    st.markdown("#### All findings")
    f = agents.findings_frame()
    if not f.empty:
        f.insert(0, "", f["Severity"].map(T.SEVERITY_ICON))
        sev = st.multiselect("Severity", ["Critical", "High", "Medium", "Low", "Info"],
                             default=["Critical", "High", "Medium", "Low", "Info"],
                             key="agent_sev")
        table_with_download(f[f["Severity"].isin(sev)], "Agent_Findings.csv",
                            height=340, key="dl_af")

    st.markdown("#### Proposed actions, ranked by leverage")
    a = agents.actions_frame()
    if not a.empty:
        show_a = a.copy()
        show_a["Overrides"] = show_a["Overrides"].apply(json.dumps)
        table_with_download(show_a, "Agent_Actions.csv", height=300, key="dl_aa")

    st.markdown("#### Inter-agent messages")
    table(agents.conversation())

    st.markdown("---")
    st.markdown("#### Ask the plan a question")
    suggestions = _agent_questions(R)
    pick = st.selectbox("Suggested questions", ["— type your own —"] + suggestions)
    default = "" if pick.startswith("—") else pick
    q = st.text_input("Question", value=default, key="agent_q",
                      placeholder="Ask about any item, work centre or module…")

    if q.strip():
        ans = agents.ask(q)
        note(ans["answer"], "note")
        if ans["table"] is not None and not ans["table"].empty:
            st.caption(f"Source: `{ans['source']}`")
            table(ans["table"], height=320)



# ===========================================================================
# Data-derived helpers - nothing in the dashboard names a specific factory
# ===========================================================================
def _finished_items(R):
    """Items that are never consumed by anything else."""
    bom = R["data"]["bom"]
    components = set(bom["ComponentItem"].astype(str)) if not bom.empty else set()
    return [i for i in R["data"]["inventory"]["Item"].astype(str)
            if i not in components]


def _busiest_item(R):
    d = R["data"]["demand"]
    if d is None or d.empty:
        fin = _finished_items(R)
        return fin[0] if fin else None
    return d.groupby("Item")["Demand"].sum().idxmax()


def _tightest_work_centre(R):
    plan = R.get("capacity_plan")
    if plan is not None and not plan.empty:
        return plan.groupby("WorkCentre")["Utilisation"].mean().idxmax()
    cap = R["data"]["capacity"]
    return cap.sort_values("AvailableHoursPerPeriod").iloc[0]["WorkCentre"] \
        if not cap.empty else None


def _most_shared_component(R):
    tree = R["bom_tree"]
    if not tree.all_items:
        return None
    return max(tree.all_items, key=lambda i: len(tree.parents.get(i, [])))


def _worst_supplier(R):
    sup = R["data"].get("supplier")
    if sup is None or sup.empty or "Reliability" not in sup.columns:
        return None
    return sup.sort_values("Reliability").iloc[0]


def _whatif_examples(R):
    """Example what-ifs phrased in the vocabulary of the loaded factory."""
    top, wc = _busiest_item(R), _tightest_work_centre(R)
    fin = _finished_items(R)
    other = next((i for i in fin if i != top), top)
    sup = _worst_supplier(R)
    out = []
    if top:
        out.append(f"what if {top} demand rises 40% in weeks 3 to 6")
    if other and other != top:
        out.append(f"what if {other} demand collapses by 30%")
    if sup is not None:
        out.append(f"what if {sup['Supplier']} slips by 3 weeks")
    if wc:
        out += [f"what if {wc} runs at 50% in weeks 4 and 5",
                f"what if we add a machine at {wc}"]
    if other:
        out.append(f"what if there is a rush order of 500 {other} in period 2")
    return out


def _agent_questions(R):
    """Suggested questions using this dataset's own item and work centre names."""
    top = _busiest_item(R)
    wc = _tightest_work_centre(R)
    shared = _most_shared_component(R)
    made = R["data"]["inventory"]
    made = made[made["SourceType"] == "M"]["Item"].astype(str).tolist()
    an_item = made[0] if made else top
    q = ["which work centre is the bottleneck?", "why are orders on hold?"]
    if an_item:
        q.append(f"what is the MRP position for {an_item}?")
    if shared:
        q.append(f"where is {shared} used?")
    if top:
        q.append(f"what forecast model is used for {top}?")
    q.append("which dispatching rule should we use?")
    if wc:
        q.append(f"tell me about {wc} capacity")
    q.append("how is the plan doing overall?")
    return q


# ===========================================================================
# MAIN
# ===========================================================================
PAGES = {
    "Overview":                                 ("🏭", page_overview),
    "1 · Understand Requirements":              ("📘", page_block1),
    "2 · Prepare Input Data":                   ("📥", page_inputs),
    "3 · Demand Forecasting":                   ("📈", page_forecast),
    "4 · Inventory Planning":                   ("📦", page_inventory),
    "5 · Master Production Scheduling":         ("🗓️", page_mps),
    "6 · BOM Explosion":                        ("🌳", page_bom),
    "7 · Material Requirements Planning":       ("⚙️", page_mrp),
    "8 · Production Order Release":             ("🚀", page_release),
    "9 · Routing & Capacity Preparation":       ("🛠️", page_routing),
    "10 · Shop-Floor Scheduling":               ("📊", page_scheduling),
    "11 · Performance Comparison":              ("🏆", page_comparison),
    "12 · Exception Management":                ("⚠️", page_exceptions),
    "13 · Scenario Simulation":                 ("🔮", page_scenarios),
    "All Data":                                 ("🗂️", page_all_data),
    "Agent Layer":                              ("🤖", page_agents),
}


def main():
    data_io.ensure_active()
    log = get_decision_log()
    overrides = log.active_overrides()

    with st.sidebar:
        st.markdown("### 🏭 Control Tower")
        st.caption("Integrated Manufacturing Operations")
        st.markdown("---")

        page = st.radio("Navigate", list(PAGES),
                        format_func=lambda p: f"{PAGES[p][0]}  {p}",
                        label_visibility="collapsed")

        st.markdown("---")
        st.markdown("**Planning controls**")
        window = st.slider("Scheduling window (periods)", 1, 12, (1, 8),
                           help="Which periods of released orders go to the "
                                "shop-floor simulation.")
        rule = st.selectbox("Dispatching rule", ["Auto (best on-time)"] + DISPATCH_RULES,
                            help="Auto picks the rule with the best on-time delivery.")
        rule_arg = None if rule.startswith("Auto") else rule

        st.markdown("---")
        st.markdown("**Your data**")
        custom = [f["key"] for f in data_io.FILES if data_io.is_custom(f["key"])]
        if custom:
            st.caption(f"🟢 {len(custom)} file(s) replaced with your own.")
            if st.button("Reset to sample data", use_container_width=True):
                data_io.reset_to_sample()
                st.cache_resource.clear()
                st.rerun()
        else:
            st.caption("Using the built-in sample dataset.")
        st.caption("Import and export live on the **2 · Prepare Input Data** page.")

        if overrides:
            st.markdown("---")
            st.markdown("**Active decisions**")
            n = len([e for e in log.entries
                     if e["Decision"] in ("Approved", "Modified")])
            st.caption(f"{n} approved decision(s) are changing this plan.")
            if st.button("Reset to baseline plan", use_container_width=True):
                log.clear()
                st.cache_resource.clear()
                st.rerun()

    ov_json = json.dumps(overrides, sort_keys=True)
    fp = data_io.fingerprint()

    try:
        R = cached_pipeline(fp, ov_json, tuple(window), rule_arg)
    except Exception as e:                                        # noqa: BLE001
        st.error("The planning run failed on the current data.")
        note(f"**{type(e).__name__}:** {e}", "crit")
        st.caption("This almost always means an uploaded file does not line up with "
                   "the others — for example a BOM that refers to an item missing from "
                   "the Inventory Master. The cross-file checks on the Block 2 page "
                   "will point at the mismatch.")
        c1, c2 = st.columns([1, 3])
        if c1.button("Reset to sample data", type="primary"):
            data_io.reset_to_sample()
            st.cache_resource.clear()
            st.rerun()
        with st.expander("Technical detail"):
            st.code(traceback.format_exc())
        return

    agents = cached_agents(R, f"{fp}|{ov_json}|{window}|{rule_arg}")

    with st.sidebar:
        st.markdown("---")
        st.caption(f"Ran in {R['runtime_seconds']}s · health {R['health_score']}/100")

    PAGES[page][1](R, agents)


if __name__ == "__main__":
    main()

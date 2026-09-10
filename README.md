# Integrated Manufacturing Operations Control Tower

An end-to-end MRP II planning system: demand forecasting → inventory planning →
master production scheduling → BOM explosion → material requirements planning →
order release → shop-floor scheduling → performance comparison → exception
management → scenario simulation.

Built as a **deterministic Python engine** with an **interactive Streamlit
dashboard** and a **multi-agent analysis layer** on top.

```
┌──────────────────────────────────────────────────────────────────┐
│  9 input CSV files  (bicycle manufacturing case)                 │
└───────────────────────────┬──────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  DETERMINISTIC ENGINE   every number is computed and auditable   │
│                                                                  │
│  forecasting → inventory → mps → bom → mrp → order_release       │
│              → scheduling → kpi → exceptions → scenarios         │
└───────────────┬────────────────────────────────┬─────────────────┘
                ▼                                ▼
┌───────────────────────────┐   ┌──────────────────────────────────┐
│  STREAMLIT DASHBOARD      │   │  MULTI-AGENT LAYER               │
│  16 pages · import/export │   │  8 agents + coordinator          │
│  Gantt, KPIs, what-if     │   │  briefing · Q&A · what-if        │
└───────────────────────────┘   └──────────────────────────────────┘
```

---

## Quick start

```bash
pip install -r requirements.txt

python generate_data.py          # Block 2 — build & validate the 9 input files
python run_pipeline.py           # run all 13 blocks, print KPIs, export results
streamlit run dashboard/app.py   # open the interactive control tower
```

Other useful commands:

```bash
python run_pipeline.py --scenarios              # run every what-if scenario
python run_pipeline.py --scenario "Machine Breakdown - Painting"
python run_pipeline.py --rule EDD --window 1-12
python -m unittest discover -s tests -v         # 123 tests
```

---

## Why this architecture

The assignment allows Power BI, Excel, agentic or multi-agent approaches. Four
steps in the flow chart decide the choice:

| Step | What it needs | Excel | Power BI (DAX) | Python |
|---|---|---|---|---|
| 6.2 BOM explosion, ≥3 levels | recursive tree traversal, low-level coding | painful | not feasible | natural |
| 7.2 MRP logic | time-phased netting, lead-time offsetting | painful | not feasible | natural |
| 5.3 PAB | row-recursive (each period depends on the last) | natural | very weak | natural |
| 10.2 Shop-floor scheduling | discrete-event job-shop simulation | not feasible | not feasible | natural |

Power BI is a **presentation** layer — it reads results, it does not compute MRP.
So the engine is Python, and the dashboard sits on top of it. The engine also
exports clean result CSVs, so a Power BI dashboard can be layered on the same
numbers if the brief requires one (see `outputs/`).

**The agent layer sits on top, never inside.** Agents are unreliable at
arithmetic and time-phased logic, so no agent computes a requirement, a balance
or a schedule. They read the engine's tables, interpret them, and propose
actions expressed as executable overrides. Every figure an agent quotes can be
traced back to an MRP row — which is what makes the system defensible in a viva.

---

## Repository layout

```
control_tower/
├── generate_data.py            Block 2 — builds and validates the 9 input files
├── run_pipeline.py             CLI runner: baseline + scenarios
│
├── data/                       the 9 input CSV files
├── outputs/                    every result table, exported as CSV
│
├── engine/                     ← the deterministic planning engine
│   ├── config.py               planning parameters and thresholds
│   ├── io_utils.py             import / validate / export
│   ├── forecasting.py          Block 3   MA, WMA, SES, Holt, Holt-Winters
│   ├── inventory.py            Block 4   EOQ, ROP, safety stock, projection
│   ├── mps.py                  Block 5   PAB, rough-cut capacity, 5.5 loop
│   ├── bom.py                  Block 6   multi-level explosion, low-level codes
│   ├── mrp.py                  Block 7   netting, lot sizing, lead-time offset
│   ├── order_release.py        Blocks 8–9 material check, job list
│   ├── scheduling.py           Blocks 10–11 job-shop simulation, 5 rules
│   ├── kpi.py                  Block 11  cross-module KPIs, health score
│   ├── exceptions.py           Block 12  detection, actions, decision log
│   ├── scenarios.py            Block 13  override engine + scenario library
│   ├── lotsizing.py            LFL / FOQ / EOQ / POQ policies
│   └── pipeline.py             orchestrates all thirteen blocks
│
├── agents/                     ← the multi-agent layer
│   ├── base.py                 Agent, Finding, Action, Blackboard
│   ├── specialists.py          6 domain agents
│   ├── scenario_agent.py       plain-English what-if → overrides → run → diff
│   └── coordinator.py          query agent + crew coordinator + briefing
│
├── dashboard/
│   ├── app.py                  16-page Streamlit control tower
│   ├── data_io.py              import / validate / export your own data
│   └── theme.py                validated colour system and chart defaults
│
├── samples/
│   └── furniture_factory/      a second, completely different plant
│
├── data_active/                your working copy (created on first run)
│
├── tests/
│   ├── test_engine.py          64 tests over the planning mathematics
│   ├── test_data_io.py         59 tests: import, column mapping, messy
│   │                           formats, and a full non-bicycle factory
│   └── smoke_dashboard.py      headless-browser test of all 16 pages
│
└── docs/
    ├── ARCHITECTURE.md         module ↔ flow-chart block map
    ├── FORMULAS.md             every formula used, with worked examples
    ├── VIVA_NOTES.md           design decisions, findings, limitations
    └── screenshots/            one screenshot per dashboard page
```

---

## The case study

**Bicycle manufacturing**, chosen because a bicycle has a naturally deep bill of
materials and obvious shared components.

- **3 finished products**, each with a deliberately different demand pattern
- **32 items** across **4 BOM levels** (0–3)
- **6 work centres**, 18 routing operations
- **36 periods** of demand history, **12-period** planning horizon

```
BIKE-MTB                                       level 0
├── FRAME-MTB                                  level 1
│   ├── TUBESET-MTB                            level 2
│   │   └── ALU-TUBE-STK                       level 3
│   └── PAINT-PWD
├── WHEEL-26  ×2
│   ├── RIM-26 · HUB-STD · SPOKE ×32 · TYRE-26 level 2
├── DRIVE-21S
│   └── CHAIN · CRANKSET · DERAILLEUR
├── BRAKE-DISC
└── SADDLE-ASSY
```

Shared components are the interesting part: `SPOKE` and `HUB-STD` feed all three
wheels, `CHAIN` and `CRANKSET` feed all three drivetrains, `DERAILLEUR` feeds
two. This is what makes low-level coding necessary rather than decorative.

### Deliberate design of the data

The dataset is engineered so the system has something real to find:

| Design choice | Purpose |
|---|---|
| MTB trends up, road bikes are seasonal, kids bikes are flat with promo spikes | different forecasting models win for different products |
| WC03 (painting) has 1 machine on 1 shift = 45 h/period | a genuine bottleneck, so the 5.5 feasibility loop actually fires |
| DERAILLEUR, TYRE-700C and FRAME-ROAD are under-stocked | traceable material exceptions rather than a wall of red |
| SUP-06 has 80% reliability and a 5-period lead time | supplier-risk findings with a real consequence |
| Stock levels derived by rolling demand down the BOM | a spoke used 66× per bike automatically gets 66× the stock |

---

## What the system produces (baseline run)

```
Plan health score          : 77.7/100
Forecast average MAPE      : 7.53%      MTB→MA(5), ROAD→Holt-Winters, KIDS→SES
MPS feasible               : Yes, after 4 level-loading iterations
Rough-cut peak utilisation : 99.3%      bottleneck WC03 (Painting & Curing)
Planned order releases     : 130        2 past due
Orders released / on hold  : 69 / 23
Dispatching rule selected  : FCFS       96.4% on-time, makespan 683 h
Open exceptions            : 31         5 critical
```

Three findings the system surfaces that are worth understanding:

1. **The MPS feasibility loop genuinely converges.** The first-pass schedule has
   8 capacity overloads at 110% peak utilisation. Four rounds of level-loading —
   pulling production into earlier periods with spare capacity — bring it to
   99.3% and zero overloads. The convergence is shown on the dashboard.

2. **MA(5) wins on MAPE for mountain bikes but is badly biased.** Its tracking
   signal is 20 against a limit of ±4, because a moving average structurally lags
   a trend. Selecting on MAPE alone (as the flow chart specifies) hides this;
   the tracking-signal alert catches it, and the forecast agent recommends Holt
   instead. This is a real weakness of MAPE-based selection, not a bug.

3. **Rough-cut capacity overstates the peak.** RCCP peaks at 99% while the
   detailed simulation averages 37%. RCCP loads every component into the same
   period as its parent; MRP then spreads that work backwards by each item's
   lead time. Both numbers are right — they answer different questions.

---

## The dashboard — one page per flow-chart block

The page list follows the assignment flow chart in order, block 1 to block 13.

| Page | What it shows |
|---|---|
| Overview | Health score, KPI tiles, block-by-block status, top exceptions |
| **1 · Understand Requirements** | Objectives, scope, deliverables, assumptions, the end-to-end framework |
| **2 · Prepare Input Data** | The nine files — **browse, import your own, export, validate** |
| **3 · Demand Forecasting** | Actual vs fitted vs forecast, all 5 methods scored, alerts |
| **4 · Inventory Planning** | Position vs safety stock and ROP, horizon projection, exceptions |
| **5 · Master Production Scheduling** | PAB, ATP, the 5.5 convergence loop, capacity heatmap |
| **6 · BOM Explosion** | Indented BOM, where-used, low-level codes, gross requirements |
| **7 · Material Requirements Planning** | Full MRP grid per item, pegging, planned releases, past-due alerts |
| **8 · Production Order Release** | Release decision, what is blocking held orders |
| **9 · Routing & Capacity Preparation** | Capacity vs queued work per work centre, the job list |
| **10 · Shop-Floor Scheduling** | Gantt by machine, utilisation, lateness per job |
| **11 · Performance Comparison** | All 5 rules on 6 measures, best-by-criterion, formula reference |
| **12 · Exception Management** | Register, recommended actions, **manager decision that re-plans** |
| **13 · Scenario Simulation** | 7 disruption scenarios + plain-English what-if |
| **All Data** | **Every input and result table in one place**, searchable, all downloadable |
| Agent Layer | Briefing, findings, ranked actions, Q&A |

---

## Running it on your own data

The system ships with a sample bicycle factory, but it will plan **your** factory.

**Nothing in the system is tied to bicycles.** Finished goods, BOM levels,
make-vs-buy, the bottleneck, the scenario library and the agent's vocabulary are
all derived from whatever data is loaded. `samples/furniture_factory/` is a
second, completely different plant — 2 products, 11 items, 3 BOM levels, 3 work
centres, 24 periods — provided so you can prove that in about a minute.

**Import** — on the *2 · Prepare Input Data* page, the **Import your data** tab
takes a **CSV or Excel** file with *any* column names, in three steps:

1. **Read** — CSV, Excel (any sheet), or tab-separated
2. **Map** — your columns are matched to the fields the planner needs
   (`Part No` → `Item`, `Stock On Hand` → `OnHand`, `Usage Quantity` → `QtyPer`).
   The guess is shown as a set of dropdowns so you can correct anything wrong.
3. **Fill** — anything you have not got is filled with a documented default, and
   every default is listed back to you so nothing is silently invented.

Only a handful of fields are genuinely essential:

| File | You must supply | Everything else |
|---|---|---|
| Demand History | Item, Period, Demand | — |
| Customer Orders | Item, Period, Quantity | order id generated |
| Inventory Master | Item, OnHand, LeadTime | 11 fields defaulted |
| Bill of Materials | ParentItem, ComponentItem, QtyPer | scrap = 0 |
| Supplier Lead Times | Supplier, Item | lead time from the item master |
| Routing | Item, WorkCentre | op sequence numbered 10, 20, 30 |
| Machine Capacity | WorkCentre | 1 machine, one 8-hour shift, 5 days |

**Make-vs-buy and BOM level are derived from the bill of materials**, so you
never classify parts by hand. A percentage typed as `85` instead of `0.85` is
detected and corrected rather than refused.

A file missing an essential field is refused with a readable reason and never
overwrites your working data. A **cross-file check** then catches errors that
only appear once the files are combined — a BOM referencing a missing item, a
routing pointing at a work centre with no capacity, too little demand history.

Accepting a file re-runs the entire pipeline immediately. *Reset to sample data*
in the sidebar puts everything back.

**Export** — every table on every page has its own download button, and three
buttons give you everything at once:

| Button | What you get |
|---|---|
| 📗 Everything as Excel | One workbook, one sheet per table — inputs first, then all 20 result tables |
| 🗜 Everything as ZIP | The same content as CSVs, in `inputs/` and `outputs/` folders |
| 📁 Just the 9 input files | The current inputs as a template to edit and re-upload |

The Excel workbook is the easiest thing to hand in, and it is also what you would
point Power BI at if the brief asks for a Power BI dashboard.

---

## The manager decision loop is real

Block 12.4 says "implement decision in system". On the Exceptions page, choosing
a recommended action and approving it:

1. writes the decision to `outputs/decision_log.json` with who, when and why
2. converts the action into an engine override, e.g. `{"lead_time": {"DERAILLEUR": 2}}`
3. re-runs the pipeline from the affected block
4. updates **every page** of the dashboard with the re-planned factory

The sidebar shows how many decisions are live and offers a reset. This is the
same override mechanism the scenario engine uses — approving a recommendation
and running a what-if are the same operation with different inputs.

---

## The multi-agent layer

Eight agents on a shared blackboard, each watching one slice of the plan:

| Agent | Watches |
|---|---|
| ForecastAgent | accuracy, bias, model suitability |
| InventoryAgent | cover, stockout risk, working capital |
| SupplyAgent | supplier risk, purchase exposure, expedites |
| CapacityAgent | bottleneck identification, overload, level-loading |
| ScheduleAgent | dispatching rule trade-offs, tardiness, setup loss |
| ExceptionAgent | cross-module root-cause analysis |
| ScenarioAgent | plain-English what-if → overrides → run → diff |
| QueryAgent | answers questions from the result tables |

The **Coordinator** runs the crew, then reasons over the combined picture —
including spotting when several agents independently flag the same item, which
raises confidence that it is a real constraint. In the baseline run, three agents
independently flag `DERAILLEUR`.

Plain-English what-ifs the scenario agent parses:

```
"what if mountain bike demand rises 40% in weeks 3 to 6"
"what if the derailleur supplier slips by 3 weeks"
"what if the paint line runs at 50% in weeks 4 and 5"
"what if we add a machine at the paint line"
"what if there is a rush order of 500 kids bikes in period 2"
```

Parsing is rule-based rather than model-based on purpose: it is inspectable, it
is reproducible in a live demonstration, and it needs **no API key and no network
access**. The agent always shows how it read the request, so a wrong reading is
visible and correctable rather than silent.

---

## Verification

```bash
python -m unittest discover -s tests -v     # 123 tests, all passing
```

The tests assert **identities that must hold in any correct MRP II system**, not
just that the code runs:

- PAB recursion: `PAB(t) = PAB(t−1) + MPS(t) − max(forecast, orders)` on every row
- MRP netting: `PA(t) = PA(t−1) + SR + PORcpt − GR` on every row
- Net requirement equals the shortfall below safety stock
- Planned receipts obey the lot-size rule and always cover the requirement
- Releases are offset backwards by exactly the lead time; clamped ones are flagged
- **Dependent demand reconciles**: each child's gross requirement equals the sum
  over its parents of their planned releases × qty-per ÷ (1 − scrap)
- Items are processed in ascending low-level-code order
- No machine is double-booked; operations respect routing sequence; no job starts
  before release
- Every rule schedules identical total work — rules change sequence, not content
- Overrides never mutate the source data
- A timed outage is not counted twice across the two capacity models

A further 26 tests cover the data layer a user actually touches: a bad upload is
rejected without damaging the working dataset, a good upload really does change
the plan, reset restores the baseline exactly, and a downloaded template passes
its own validation when re-uploaded.

Plus `tests/smoke_dashboard.py`, which drives all 16 dashboard pages in a headless
browser and fails if any renders an exception.

---

## Deploying

The system has no API keys, no database and no external services, and it builds
its own dataset on first run, so it deploys to anything that runs Python.

```bash
# Streamlit Community Cloud — free, gives a shareable URL
#   push to GitHub, then at share.streamlit.io set the
#   main file path to  dashboard/app.py

# or any container host
docker build -t control-tower .
docker run -p 8501:8501 control-tower
```

Full instructions, including Render / Hugging Face Spaces and a
pre-presentation checklist, are in **`docs/DEPLOYMENT.md`**.

---

## Documentation

- **`docs/ARCHITECTURE.md`** — every module mapped to its flow-chart block
- **`docs/FORMULAS.md`** — every formula, with worked examples from this dataset
- **`docs/VIVA_NOTES.md`** — design decisions, defensible answers, honest limitations
- **`docs/DEPLOYMENT.md`** — hosting options, resource needs, troubleshooting

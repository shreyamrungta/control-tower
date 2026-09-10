# Architecture

## Flow chart → code map

Every numbered node of the assignment flow chart is implemented in a named
function. This table is the index.

| Flow chart | Module | Function |
|---|---|---|
| **2.1–2.9** Prepare input dataset files | `generate_data.py` | `main()` |
| 2. Validate & import | `generate_data.py`, `engine/io_utils.py` | `validate()`, `load_all()` |
| **3.1** Import historical demand | `engine/forecasting.py` | `run_forecasting()` |
| **3.2** Run forecasting methods | `engine/forecasting.py` | `moving_average()`, `weighted_moving_average()`, `single_exponential_smoothing()`, `holt_linear()`, `holt_winters()` |
| **3.3** Evaluate forecast accuracy | `engine/forecasting.py` | `accuracy_metrics()` |
| **3.4** Select best model | `engine/forecasting.py` | `run_forecasting()` — lowest MAPE |
| **3.5** Generate forecast + alert | `engine/forecasting.py` | `run_forecasting()` |
| **4.1** Import inventory master | `engine/inventory.py` | `run_inventory_planning()` |
| **4.2** Calculate parameters | `engine/inventory.py` | `calculate_parameters()` |
| **4.3** Project inventory | `engine/inventory.py` | `project_inventory()` |
| **4.4** Identify exceptions | `engine/inventory.py` | `identify_exceptions()` |
| **4.5** Output inventory report | `engine/pipeline.py` | `export_outputs()` |
| **5.1** Inputs to MPS | `engine/mps.py` | `build_demand_input()` |
| **5.2** Create / revise MPS | `engine/mps.py` | `build_mps()` |
| **5.3** Calculate PAB | `engine/mps.py` | `build_mps()`, `_add_atp()` |
| **5.4** Check MPS feasibility | `engine/mps.py` | `rough_cut_capacity()`, `check_feasibility()` |
| **5.5** MPS OK? → revise loop | `engine/mps.py` | `run_mps()`, `_level_load()` |
| **6.1** Import BOM structure | `engine/bom.py` | `BOMTree.__init__()`, `_compute_low_level_codes()` |
| **6.2** Explode MPS through BOM | `engine/bom.py` | `explode()`, `explode_gross_requirements()` |
| **6.3** Calculate gross requirements | `engine/bom.py` | `explode_gross_requirements()` |
| **6.4** Output gross requirements | `engine/pipeline.py` | `export_outputs()` |
| **7.1** Inputs to MRP | `engine/mrp.py` | `run_mrp()` |
| **7.2** Run MRP logic | `engine/mrp.py` | `run_mrp()` |
| **7.3** Apply lot sizing | `engine/lotsizing.py` | `apply_lot_size()` |
| **7.4** Generate MRP output | `engine/mrp.py` | `mrp_output_table()`, `item_grid()` |
| **7.5** Identify material exceptions | `engine/mrp.py` | `_material_exceptions()` |
| **8.1** Identify candidate orders | `engine/order_release.py` | `run_order_release()` |
| **8.2** Check material availability | `engine/order_release.py` | `run_order_release()` |
| **8.3** Materials available? | `engine/order_release.py` | `run_order_release()` |
| **8.4** Classify constraint | `engine/order_release.py` | `run_order_release()` |
| **8.5** Release to shop floor | `engine/order_release.py` | `run_order_release()` |
| **9.1–9.3** Routing, capacity, job list | `engine/order_release.py` | `build_job_list()` |
| **10.1** Select dispatching rule | `engine/scheduling.py` | `_select()` |
| **10.2** Run scheduling simulation | `engine/scheduling.py` | `simulate()` |
| **10.3** Generate Gantt data | `engine/scheduling.py` | `simulate()` → gantt frame |
| **10.4** Calculate performance measures | `engine/scheduling.py` | `_job_results()`, `_work_centre_results()`, `_kpis()` |
| **10.5** Store results per rule | `engine/scheduling.py` | `run_all_rules()` |
| **11.1** Repeat for all rules | `engine/scheduling.py` | `run_all_rules()` |
| **11.2** Compare KPI results | `engine/scheduling.py` | `compare_rules()` |
| **11.3** Best rule by criterion | `engine/scheduling.py` | `best_rule_by_criterion()` |
| **11.4** Dashboard view | `engine/kpi.py`, `dashboard/app.py` | `build_kpi_summary()`, `page_overview()` |
| **12.1** Detect exceptions | `engine/exceptions.py` | `detect_*()`, `consolidate()` |
| **12.2** Show recommended actions | `engine/exceptions.py` | `recommended_actions()` |
| **12.3** Manager decision | `engine/exceptions.py` | `DecisionLog.record()` |
| **12.4** Implement decision | `engine/scenarios.py`, `engine/pipeline.py` | `apply_data_overrides()`, `run_pipeline()` |
| **13.1** Run what-if scenarios | `engine/scenarios.py` | `SCENARIOS`, `apply_data_overrides()` |
| **13.2** Analyse impact | `engine/scenarios.py` | `compare_to_baseline()` |
| **13.3** Document results | `engine/scenarios.py` | `summarise_impact()` |
| **13.4** Prepare deliverables | `engine/pipeline.py` | `export_outputs()` |
| **13.5** Presentation & demo | `dashboard/app.py` | all 13 pages |

---

## Data flow

```
Demand_History ──► forecasting ──► forecast ──┐
                                              │
Customer_Orders ──────────────────────────────┤
                                              ▼
Inventory_Master ──► inventory ──► EOQ/ROP ──► MPS ──► PAB + capacity test
                                                │            │
                                                │      ┌─────┘  (5.5 loop)
                                                │      ▼
BOM ──────────────────────► bom_tree ──► gross requirements
                                │               │
                                ▼               ▼
                          low-level codes ──►  MRP ──► planned order releases
                                                        │
                        Supplier_LeadTime ───────────────┤
                                                        ▼
                                              order release (8.3 decision)
                                                        │
                            Routing + Machine_Capacity ─┤
                                                        ▼
                                               job list ──► scheduling simulation
                                                                 │
                                                                 ▼
                                                        KPIs ──► rule comparison
                                                                 │
                    every module's exceptions ──────────────────► exception register
                                                                 │
                                                                 ▼
                                              manager decision / what-if
                                                                 │
                                                                 └──► re-run pipeline
```

---

## Key design decisions

### The pipeline is a pure function

`run_pipeline(data, overrides)` has no hidden state. This is what makes what-if
analysis and approved manager decisions trivially re-runnable: a scenario is
just a different `overrides` argument, and it re-plans the entire factory in
about two seconds.

### One override mechanism serves two blocks

Block 12.4 ("implement decision in system") and Block 13.1 ("run what-if
scenarios") are the same operation with different inputs. Both produce an
override payload; `apply_data_overrides()` executes it. This is why approving a
recommended action genuinely re-plans the factory rather than just logging a tick.

### Two capacity models, deliberately

| | Rough-cut capacity (Block 5) | Shop-floor simulation (Block 10) |
|---|---|---|
| Time | period buckets | continuous hours |
| Load placement | every component in its parent's period | spread by lead-time offset |
| Purpose | is the MPS sizeable? | is the schedule executable? |
| Baseline result | 99.3% peak | 37% average, 62% peak |

Both are correct; they answer different questions. RCCP deliberately ignores
lead-time offsetting because it is a *sizing* check made before MRP has run.
The dashboard surfaces this gap explicitly rather than hiding it.

A **timed outage** (a machine down in specific periods) is applied precisely,
period by period, in RCCP — where capacity is held per period. The simulation
holds machines as a fixed pool with no calendar, so there the same outage is
applied as the equivalent horizon-average derate. Doing it in exactly one place
per model is what stops the outage being counted twice; a test asserts this.

### Numeric precision

Planning tables keep three decimal places internally. Fractional quantities are
real — they carry compounded scrap factors down the BOM — and rounding them to
whole units in the ledger would make the stored numbers disagree with the
identities that produced them. Reports round to two places for legibility.

### Agents never do arithmetic

Agents read engine tables, interpret them, and propose actions. No agent
computes a requirement, a balance or a schedule. Every figure an agent quotes
can be traced back to an MRP row. This is what keeps the system auditable while
still providing a judgement layer.

### Rule-based natural-language parsing

The scenario agent parses what-ifs with rules, not a language model, so that it
is inspectable, reproducible in a live demonstration, and needs no API key or
network access. It always reports how it read the request, so a misreading is
visible rather than silent, and it refuses rather than guessing when it cannot
map a request onto a change the engine can make.

The design leaves a clean seam for a language model: `interpret()` returns an
override payload, so swapping in an LLM that emits the same payload changes
nothing downstream.

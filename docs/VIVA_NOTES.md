# Viva Notes

Design decisions, defensible answers to likely questions, and honest limitations.

---

## The five-minute demonstration

1. **Overview** — health score 77.7/100, the block-by-block status table, the
   critical exceptions. "Here is the whole plan in one screen."
2. **Forecasting** — switch to BIKE-ROAD, point out Holt-Winters winning because
   the demand is seasonal; then BIKE-MTB, where MA(5) wins on MAPE but the
   tracking-signal alert fires. "MAPE alone would have hidden a biased forecast."
3. **Master Schedule** — the 5.5 convergence chart: 8 overloads → 0 across four
   iterations. "This is the feedback loop in the flow chart, actually running."
4. **BOM** — the indented BOM for BIKE-MTB, 66.6 spokes per bike. "Scrap
   compounds at every level."
5. **MRP** — FRAME-MTB grid, walk one period by hand. Then the past-due release
   on DERAILLEUR.
6. **Order Release** — the blocker chart: DERAILLEUR alone holds 15 orders.
7. **Scheduling** — the Gantt, WC03 as the bottleneck.
8. **Rule Comparison** — SPT wins flow time, FCFS wins on-time delivery. "No
   rule wins everything; choosing one is choosing what you care about."
9. **Exceptions** — approve "expedite DERAILLEUR", watch the whole plan re-run.
10. **Agent Layer** — the briefing, then ask it a question live.

---

## Likely questions, and the answers

### "Why Python and not Power BI or Excel?"

Four steps decide it. Multi-level BOM explosion and MRP netting are recursive and
time-phased — DAX cannot express them and Excel manages them only painfully. The
PAB row is recursive, which DAX handles very badly. And the shop-floor
dispatching simulation is a discrete-event simulation, which neither tool can
run. Power BI is a presentation layer; it reads results, it does not compute MRP.

The engine exports clean result CSVs, so a Power BI dashboard can be layered on
exactly the same numbers if the brief demands one. That is the honest division:
Python computes, Power BI presents.

### "Where is the multi-agent part, and why doesn't it do the calculations?"

Eight agents on a shared blackboard — six domain specialists, a scenario agent
and a query agent, coordinated by a `Coordinator` that synthesises their findings.
Each runs a perceive → reason → act loop.

They deliberately do no arithmetic. Language models are unreliable at
time-phased arithmetic, and an MRP figure that cannot be traced is worthless.
So the deterministic engine computes and the agents interpret. The payoff is
visible in the briefing: three agents independently flag DERAILLEUR from
different evidence (stockout projection, supplier reliability, held-order root
cause), and the coordinator reports that convergence as raising confidence.

### "How does the agent understand plain English without an API key?"

Rule-based parsing: entity aliases, period ranges, percentage and absolute-level
detection. This was a deliberate choice — it is inspectable, reproducible in a
live demonstration, and works offline. The agent always reports how it read the
request, and refuses rather than guessing when it cannot map one.

There is a clean seam for a language model: `interpret()` returns an override
payload, so an LLM that emits the same payload plugs in with no downstream change.

### "Why does rough-cut capacity say 99% but the simulation says 37%?"

They answer different questions. Rough-cut capacity planning loads every
component into the same period as its parent, because it runs *before* MRP has
decided when things are actually made. MRP then offsets each item backwards by
its own lead time, spreading that work across several periods.

RCCP is the right number for sizing the master schedule; the simulation is the
right number for the shop floor. The dashboard states the gap explicitly rather
than hiding it, and the ScheduleAgent raises it as a finding.

### "Your on-time delivery went UP in the demand-surge scenario. Isn't that wrong?"

No — and this is the most interesting result in the system. Under the surge,
orders on hold rise from 23 to 77 because components are not available. Only 36
orders reach the shop floor instead of 69, so the schedule has less work to be
late with. OTD is flattered by the material shortage upstream.

The system catches this itself: the ExceptionAgent has a specific check for it,
and the scenario narrative says so in plain language — "read the held-order
count, not the OTD figure, as the true measure of this scenario." A dashboard
that only reported OTD would have shown a disruption as an improvement.

### "How do you know the MRP is correct?"

64 tests assert identities that must hold in any correct MRP II system, not just
that the code runs:

- the PAB recursion and the MRP netting identity, on **every** row
- net requirement equals the shortfall below safety stock
- planned receipts obey the lot rule and always cover the requirement
- releases are offset backwards by exactly the lead time
- **dependent demand reconciles** — each child's gross requirement equals the sum
  over its parents of their planned releases × qty-per ÷ (1 − scrap)
- items are processed in ascending low-level-code order
- no machine is double-booked, operations respect routing sequence, no job starts
  before release
- every dispatching rule schedules identical total work

Plus a headless-browser test that loads all 13 dashboard pages and fails on any
rendered exception.

### "Why is the plan health score 77.7 and not higher?"

Three items are deliberately under-stocked in the dataset — DERAILLEUR,
TYRE-700C and FRAME-ROAD — so that the exception machinery has something real to
find. A dataset where nothing ever goes wrong would demonstrate nothing. The
score decomposition is on the overview page: forecast accuracy 75, MPS
feasibility 100, material readiness 75, on-time delivery 96, exception load 24.

### "What happens when I approve a recommendation?"

It writes to `outputs/decision_log.json` with who, when and why; converts the
action into an engine override such as `{"lead_time": {"DERAILLEUR": 2}}`;
re-runs the pipeline from the affected block; and updates every page. The sidebar
shows how many decisions are live and offers a reset. It is the same mechanism
the scenario engine uses.

### "Why is WC03 the bottleneck?"

By design, and it is the realistic choice: painting and curing is one line on one
shift, 45 hours per period against 136–144 elsewhere, and **every frame** passes
through it. Both capacity models independently identify it, which is the kind of
corroboration you want before spending money on a constraint. The "second paint
line" scenario quantifies what relieving it is worth.

---

## Honest limitations

Stating these clearly is better than being caught by them.

1. **The shop-floor simulation has no calendar.** It runs on continuous hours
   with a scaling factor that makes total available time correct, but it does not
   model specific shift boundaries, weekends or breaks. A job can therefore
   appear to run across what would be a Sunday. Fixing this needs a working-time
   calendar per work centre.

2. **A timed machine outage is approximated in the simulation.** Rough-cut
   capacity applies it exactly, period by period. The simulation applies the
   equivalent horizon-average derate, because its machine pool has no calendar
   to place the outage in. So the *timing* of an outage is precise in Block 5 and
   averaged in Block 10.

3. **The level-loading heuristic is greedy, not optimal.** It pulls production
   backwards into the nearest period with slack, one overload at a time. It
   converges on this dataset in four iterations, but it is not guaranteed to find
   a feasible plan where one exists. A proper treatment would be a capacitated
   lot-sizing MILP.

4. **Dispatching rules are static.** The rule is chosen once and applied for the
   whole run. Real shops switch behaviour under pressure; a dynamic or
   composite rule would be the natural extension.

5. **No scheduled receipts in the base data.** This is a greenfield planning run,
   so the SR row is zero everywhere. The engine supports scheduled receipts
   (`run_mrp(..., scheduled_receipts=...)`); the dataset simply does not use them.

6. **Safety stock for components uses an assumed 25% coefficient of variation**
   where no forecast error is available. Finished goods use the actual forecast
   RMSE, which is properly derived; components inherit an assumption, which is
   documented in `inventory.py` rather than buried.

7. **Capacity is not constrained during MRP.** MRP is infinite-capacity, as it is
   in textbook MRP II — capacity is tested before it in rough-cut planning and
   after it in the simulation. Closing that loop fully would mean finite-capacity
   MRP, a much larger piece of work.

8. **One plant, one BOM, no alternates.** No alternate routings, no substitute
   components, no multi-site sourcing.

---

## If asked what you would do next

- A working-time calendar per work centre, which fixes limitations 1 and 2
  together
- Finite-capacity MRP, or at least a capacity check between Blocks 7 and 8
- Replace the greedy level-loader with a capacitated lot-sizing optimisation
- Multi-echelon safety stock instead of item-by-item
- Plug a language model into the `interpret()` seam for free-form what-ifs, while
  keeping every calculation in the deterministic engine

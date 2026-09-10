# Formula Reference

Every formula the system uses, with a worked example taken from the live
dataset. Any figure on the dashboard can be reproduced from this page by hand.

---

## Block 3 — Demand Forecasting

### Moving average (n periods)

```
F(t) = ( A(t−1) + A(t−2) + … + A(t−n) ) / n
```

### Weighted moving average

```
F(t) = w₁·A(t−1) + w₂·A(t−2) + w₃·A(t−3)        with Σw = 1
```
Default weights 0.5 / 0.3 / 0.2, most recent period weighted highest.

### Single exponential smoothing

```
F(t+1) = α·A(t) + (1 − α)·F(t)
```
Forward forecast is flat at the final level. α is grid-searched to minimise MSE.

### Holt's linear method (level + trend)

```
L(t) = α·A(t)         + (1 − α)·( L(t−1) + T(t−1) )
T(t) = β·( L(t) − L(t−1) ) + (1 − β)·T(t−1)
F(t+m) = L(t) + m·T(t)
```

### Holt-Winters additive (level + trend + seasonality, season length s)

```
L(t) = α·( A(t) − S(t−s) ) + (1 − α)·( L(t−1) + T(t−1) )
T(t) = β·( L(t) − L(t−1) ) + (1 − β)·T(t−1)
S(t) = γ·( A(t) − L(t) )   + (1 − γ)·S(t−s)
F(t+m) = L(t) + m·T(t) + S(t+m−s)
```
Requires at least two complete seasonal cycles; initialised from the first two.

### Accuracy metrics

```
error e(t) = A(t) − F(t)

MAD  = Σ|e| / n
MSE  = Σe² / n
RMSE = √MSE
MAPE = ( Σ |e / A| / n ) × 100
Bias = Σe / n                        (mean error)
Tracking signal = Σe / MAD           (keep within ±4)
```

Only periods where the model produced a forecast are scored, so every model is
compared on the same basis.

> **Worked example — BIKE-ROAD.** Holt-Winters (α=0.1, β=0.05, γ=0.4) scores
> MAPE 7.22%, MAD 7.57, RMSE 8.86, bias 0.06, tracking signal 0.19. It wins
> because road-bike demand is seasonal and no other model captures a season.

> **Why the tracking signal matters.** For BIKE-MTB, MA(5) wins on MAPE (5.84%)
> but has a tracking signal of 20.0 — it lags the upward trend and
> under-forecasts every single period. MAPE alone would hide this.

---

## Block 4 — Inventory Planning

```
Holding cost per unit-year   H   = HoldingCostRate × UnitCost
Economic order quantity      EOQ = √( 2·D·S / H )
Average period demand        d   = D / periods per year
Statistical safety stock     SS  = z · σ · √LeadTime          (z = 1.65 for 95%)
Reorder point                ROP = d × LeadTime + SafetyStock
Periods of supply                = OnHand / d
```

> **Worked example — CHAIN.**
> D = 24,701.9/yr, S = 250, unit cost 4.10, holding rate 0.22 → H = 0.902
> EOQ = √(2 × 24,701.9 × 250 / 0.902) = **3,700 units**
> d = 24,701.9 / 52 = 475.04/period, LT = 2, SS = 840
> ROP = 475.04 × 2 + 840 = **1,790 units**

Annual demand for a component is not guessed — it is the finished-goods forecast
rolled down the BOM, so a spoke used 66× per bike gets 66× the annual demand.

### Projection over the horizon

```
closing(t)  = opening(t) + receipts(t) − demand(t)
position    = closing(t) + on order
if position < ROP  → place an order, arriving at t + LeadTime
```

---

## Block 5 — Master Production Scheduling

### Projected available balance

```
PAB(t) = PAB(t−1) + MPS(t) − max( Forecast(t), CustomerOrders(t) )
```

`max(forecast, orders)` because booked orders above the forecast must still be
produced, and unconsumed forecast beyond the order book must still be planned.

### MPS quantity

```
projected = PAB(t−1) − GrossDemand(t)
if projected < SafetyStock:
    net need = SafetyStock − projected
    MPS(t)   = lot_size( net need )
else:
    MPS(t)   = 0
```

### Available to promise

```
Period 1        ATP = OnHand + MPS(1) − orders up to the next MPS receipt
MPS period t    ATP = MPS(t)          − orders from t to the next receipt
Other periods   ATP = 0
```
A negative ATP is carried **back** and consumes the nearest earlier positive ATP
— stock already on hand can cover a later order. Without this look-back step ATP
shows spurious negatives.

> **Worked example — BIKE-ROAD, periods 1–3.**
>
> | P | Forecast | Orders | Gross | Opening PAB | MPS | PAB | ATP |
> |---|---|---|---|---|---|---|---|
> | 1 | 105 | 123 | 123 | 390 | 0 | 267 | 169 |
> | 2 | 123 | 114 | 123 | 267 | 16 | 160 | 0 |
> | 3 | 142 | 104 | 142 | 160 | 142 | 160 | 38 |
>
> Period 1: 390 + 0 − 123 = **267**. Safety stock is 160, and 267 > 160, so no
> MPS is needed.
> Period 2: 267 − 123 = 144, which is below 160, so MPS = 160 − 144 = **16**,
> giving PAB = 267 + 16 − 123 = **160**.
> ATP: raw period-2 ATP would be 16 − 114 = −98. The look-back consumes 98 of
> period 1's 267, leaving 169 and 0 — and 390 + 16 − 237 = 169 confirms it.

### Rough-cut capacity planning

```
required hours(wc, t) = Σ over items routed to wc of
                        ( setup + quantity × run time per unit )

available hours       = machines × hours/shift × shifts/day × days/week × efficiency

utilisation           = required / available
```

> WC03 = 1 × 10 × 1 × 5 × 0.90 = **45.0 hours per period** — the bottleneck.

### The 5.5 revision loop

If any work centre is overloaded, MPS quantity is pulled **backwards** into an
earlier period with spare capacity, and the plan is re-tested. This trades a
little extra inventory for a feasible schedule.

> In the baseline: 8 overloads at 110.2% peak → 2 → 1 → 0 at 99.3%, in four
> iterations.

---

## Block 6 — BOM Explosion

```
component required = parent quantity × QtyPer / ( 1 − ScrapPct )
```

Scrap is applied as **shrinkage**: to build one parent you must *issue* more than
the theoretical quantity, because a fraction of what is issued is scrapped.
It compounds at every level.

> **Worked example — spokes per mountain bike.**
> 2 wheels per bike at 1% assembly scrap → 2 / 0.99 = 2.0202 wheels
> 32 spokes per wheel at 3% spoke scrap → 32 / 0.97 = 32.9897 spokes
> Total = 2.0202 × 32.9897 = **66.646 spokes per bike**

### Low-level code

An item's low-level code is the **deepest** level at which it appears anywhere in
any BOM. MRP processes items in ascending low-level-code order, so that all of an
item's parents are planned before the item itself is netted.

> `SPOKE` is consumed by three different level-1 wheels, so its code is 2. If MRP
> planned it after only one wheel, two-thirds of its demand would be missed.

---

## Block 7 — Material Requirements Planning

The standard MRP record, computed period by period:

```
available(t)   = PA(t−1) + ScheduledReceipts(t) − GrossRequirements(t)
NetReq(t)      = max( 0, SafetyStock − available(t) )
PORcpt(t)      = lot_size( NetReq(t) )
PA(t)          = available(t) + PORcpt(t)
PORel(t − LT)  = PORcpt(t)                       ← lead-time offset
```

If `t − LT < 1` the release is clamped to period 1 and flagged **past due** — the
order needed to be placed before the planning horizon began.

### Lot sizing

```
LFL  Lot-for-lot      order exactly the net requirement
FOQ  Fixed order qty  ceil( net / Q ) × Q
EOQ  Economic order   ceil( net / EOQ ) × EOQ
POQ  Period order qty sum of net requirements over the next N periods
```

> **Worked example — FRAME-MTB.** On hand 610, safety stock 270, lead time 2,
> lot-for-lot.
>
> | | P3 | P4 | P5 | P6 |
> |---|---|---|---|---|
> | Gross requirements | 84.848 | 182.828 | 182.828 | 182.828 |
> | Projected available | 525.152 | 342.323 | 270.000 | 270.000 |
> | Net requirements | 0 | 0 | 110.505 | 182.828 |
> | Planned order receipts | 0 | 0 | 110.505 | 182.828 |
> | Planned order releases | 110.505 | 182.828 | 182.828 | 182.828 |
>
> Period 3: 610 − 84.848 = 525.152, above safety stock, so nothing is needed.
> Period 5: 342.323 − 182.828 = 159.495, which is below 270, so the net
> requirement is 270 − 159.495 = **110.505**. Lot-for-lot orders exactly that,
> and PA returns to exactly 270.
> The receipt is needed in period 5 with a 2-period lead time, so the release
> appears in period **3**.
>
> The fractional quantities are correct: they carry the compounded scrap factors
> down from the bike level.

---

## Block 10 — Shop-Floor Scheduling

### Calendar scaling

The simulation runs on a continuous clock of 80 hours per period, but a work
centre does not work all of those hours. Each operation is stretched by

```
calendar factor = ( hours per period × machines ) / AvailableHoursPerPeriod
```

so one simulated period of machine time represents exactly the available hours
declared in `Machine_Capacity.csv` — shifts, working days and efficiency
included. WC03's factor is 80 × 1 / 45 = **1.78**.

Without this, every machine would appear available 24/7 and the simulation would
contradict the rough-cut capacity plan.

### Dispatching rules

```
FCFS  earliest arrival at the queue
SPT   shortest operation processing time first
LPT   longest operation processing time first
EDD   earliest job due date first
CR    critical ratio = ( due − now ) / work remaining, lowest first
```

Ties are always broken by job id, so every run is reproducible.

### Performance measures

```
Makespan          = max(completion) − min(start)
Flow time         = completion − arrival
Waiting time      = flow time − processing time
Lateness          = completion − due                (negative = early)
Tardiness         = max( 0, lateness )
On-time delivery% = on-time jobs / total jobs × 100
Utilisation       = busy hours / ( makespan × machines )
Throughput        = units completed / elapsed periods
Average WIP       = Σ flow times / makespan          (Little's Law)
Takt time         = makespan / total units
```

A job is due at the **start** of its due period — a sub-assembly must be on the
shelf when the parent order begins, not at the end of that week.

> **Worked example — job PO-0003** (TUBESET-ROAD, 40.567 units).
> Arrival 0 h, due 80 h, completion 5.62 h.
> Flow time = 5.62 − 0 = 5.62 h. Processing 5.62 h, so waiting = 0 h.
> Lateness = 5.62 − 80 = −74.38 h → early, so tardiness = 0.

---

## Block 11 — Plan health score

A transparent weighted score, shown with its components on the dashboard:

| Component | Weight | Scoring |
|---|---|---|
| Forecast accuracy | 20% | 100% at MAPE 0, 0% at MAPE 30 |
| MPS feasibility | 20% | 100% if feasible, 0% if not |
| Material readiness | 20% | released / (released + held) |
| On-time delivery | 25% | OTD% directly |
| Exception load | 15% | 100% at zero, 0% at 25 critical+high |

---

## Block 12 — Exception thresholds

```
MAPE alert                MAPE > 20%
Forecast bias alert       | tracking signal | > 4
Excess inventory          on hand > 3 × safety stock
Safety stock too low      statistical SS > 1.5 × master SS
Capacity warning          utilisation ≥ 90%
Supplier risk             reliability < 85%
Critical component        purchased, lead time ≥ 4, feeds ≥ 2 parents
```

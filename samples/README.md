# Sample datasets

Two complete factories, to prove the system is not tied to one product.

| Folder | Factory | Products | Items | BOM levels | Work centres | Periods |
|---|---|---|---|---|---|---|
| `../data/` | Bicycles (built in) | 3 | 32 | 4 | 6 | 36 |
| `furniture_factory/` | Office furniture | 2 | 11 | 3 | 3 | 24 |

## Trying the furniture factory

Open the dashboard, go to **2 · Prepare Input Data → Import your data**, and
upload the eight files from `furniture_factory/` one at a time. The plan
re-runs after each one; upload the inventory master and the bill of materials
together and the whole factory changes.

Everything adapts on its own:

- forecasting picks a model for CHAIR and TABLE
- the BOM explosion finds 3 levels and spots that STEEL-TUBE feeds both the
  chair frame and the leg set
- the scenario library rebuilds itself around *this* factory's busiest product,
  least reliable supplier and tightest work centre
- the agent learns the vocabulary, so "what if the finishing line runs at 50%
  in week 4" resolves to work centre WC-C

**Reset to sample data** in the sidebar puts the bicycles back.

## Bringing your own

You do not need to match these column names. The importer matches your columns
to the fields the planner needs — `Part No` becomes `Item`, `Stock On Hand`
becomes `OnHand` — shows you the match so you can correct it, and fills
anything you have not got with a documented default.

Only these fields are genuinely essential:

| File | You must supply |
|---|---|
| Demand History | Item, Period, Demand |
| Customer Orders | Item, Period, Quantity |
| Inventory Master | Item, OnHand, LeadTime |
| Bill of Materials | ParentItem, ComponentItem, QtyPer |
| Supplier Lead Times | Supplier, Item |
| Routing | Item, WorkCentre |
| Machine Capacity | WorkCentre |

Make-vs-buy and BOM level are worked out from the bill of materials, so you
never have to classify parts by hand.

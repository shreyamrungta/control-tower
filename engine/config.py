"""Central configuration and shared constants for the Control Tower engine."""

import os

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.path.join(BASE_DIR, "data")
OUTPUT_DIR  = os.path.join(BASE_DIR, "outputs")

# ---------------------------------------------------------------------------
# Planning parameters
# ---------------------------------------------------------------------------
HORIZON_PERIODS   = 12       # forward planning horizon (weeks)
SEASON_LENGTH     = 12       # seasonal cycle length for Holt-Winters
SERVICE_LEVEL_Z   = 1.65     # 95% service level for statistical safety stock
PERIODS_PER_YEAR  = 52       # weekly buckets

# Finished goods are DERIVED from the bill of materials (anything that is never
# a component), never hard-coded - that is what lets the system plan any factory.

# ---------------------------------------------------------------------------
# Exception thresholds  (Block 3.5, 4.4, 12.1)
# ---------------------------------------------------------------------------
MAPE_ALERT_THRESHOLD   = 20.0    # % - forecast accuracy alert
EXCESS_INVENTORY_RATIO = 3.0     # on-hand > n x safety stock  -> excess
TRACKING_SIGNAL_LIMIT  = 4.0     # |TS| above this -> forecast bias alert
UTILISATION_WARN       = 0.90    # work centre utilisation warning level

# ---------------------------------------------------------------------------
# Dispatching rules evaluated in Block 10 / 11
# ---------------------------------------------------------------------------
DISPATCH_RULES = ["FCFS", "SPT", "EDD", "LPT", "CR"]

RULE_DESCRIPTIONS = {
    "FCFS": "First Come First Served - jobs sequenced by arrival time",
    "SPT":  "Shortest Processing Time - shortest operation first",
    "EDD":  "Earliest Due Date - most urgent due date first",
    "LPT":  "Longest Processing Time - longest operation first",
    "CR":   "Critical Ratio - (time remaining / work remaining), lowest first",
}

# Hours in one planning period, used to convert the capacity plan into the
# continuous time axis used by the discrete-event scheduler.
HOURS_PER_PERIOD = 80.0      # 2 shifts x 8 h x 5 days

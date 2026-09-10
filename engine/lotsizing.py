"""
Lot sizing policies - shared by Block 5 (MPS) and Block 7 (MRP).

    LFL  Lot-for-Lot      order exactly the net requirement
    FOQ  Fixed Order Qty  order in whole multiples of a fixed quantity
    EOQ  Economic Order Q order in whole multiples of the EOQ
    POQ  Period Order Qty cover N periods of net requirement in one order
"""

import math


def apply_lot_size(net_requirement, rule, lot_value, eoq=0, min_order_qty=0):
    """Return the order quantity for a given net requirement.

    Always returns 0 for a non-positive net requirement - MRP never plans an
    order when nothing is needed.
    """
    if net_requirement <= 0:
        return 0.0

    rule = (rule or "LFL").upper()

    if rule == "FOQ" and lot_value > 0:
        qty = math.ceil(net_requirement / lot_value) * lot_value
    elif rule == "EOQ" and eoq > 0:
        qty = math.ceil(net_requirement / eoq) * eoq
    else:                                    # LFL and any unknown rule
        qty = net_requirement

    if min_order_qty > 0:
        qty = max(qty, min_order_qty)
    return float(qty)


def period_order_quantity(net_requirements, start_index, periods_to_cover):
    """POQ helper - sum the net requirements of the next N periods."""
    end = min(start_index + periods_to_cover, len(net_requirements))
    return float(sum(net_requirements[start_index:end]))


def describe(rule, lot_value, eoq=0):
    rule = (rule or "LFL").upper()
    if rule == "FOQ":
        return f"FOQ ({lot_value:,.0f} units)"
    if rule == "EOQ":
        return f"EOQ ({eoq:,.0f} units)"
    return "Lot-for-Lot"

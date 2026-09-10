"""Data loading, validation and output helpers (Block 2 import step)."""

import os
import pandas as pd

from .config import DATA_DIR, OUTPUT_DIR

FILES = {
    "demand":    "Demand_History.csv",
    "orders":    "Customer_Orders.csv",
    "inventory": "Inventory_Master.csv",
    "bom":       "BOM.csv",
    "supplier":  "Supplier_LeadTime.csv",
    "mps":       "MPS.csv",
    "routing":   "Routing_WorkCentre.csv",
    "capacity":  "Machine_Capacity.csv",
    "prodorders": "Production_Orders.csv",
}


def load_all(data_dir: str = DATA_DIR) -> dict:
    """Import all nine input files and return them as a dict of DataFrames."""
    data = {}
    missing = []
    for key, fname in FILES.items():
        path = os.path.join(data_dir, fname)
        if not os.path.exists(path):
            missing.append(fname)
            continue
        data[key] = pd.read_csv(path)
    if missing:
        raise FileNotFoundError(
            f"Missing input file(s): {missing}. Run `python generate_data.py` first."
        )
    return data


def item_master(data: dict) -> dict:
    """Inventory master indexed by item code, for fast lookup in MRP."""
    return data["inventory"].set_index("Item").to_dict("index")


def save(df: pd.DataFrame, name: str, output_dir: str = OUTPUT_DIR) -> str:
    """Write a result table to the outputs folder and return its path."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, name)
    df.to_csv(path, index=False)
    return path

"""
BLOCK 3 - DEMAND FORECASTING ENGINE
==============================================================================
3.1 Import historical demand
3.2 Run forecasting methods   (MA, WMA, SES, Holt, Holt-Winters)
3.3 Evaluate forecast accuracy (MAD, MSE, RMSE, MAPE, Bias, Tracking Signal)
3.4 Select best model per product (lowest MAPE)
3.5 Generate demand forecast + accuracy alert

Every model is implemented from first principles - no black-box library - so
that each number on the dashboard can be reproduced by hand in a viva.

Convention
----------
`fitted[t]` is the ONE-STEP-AHEAD forecast for period t made using data up to
and including period t-1.  Periods during a model's warm-up are np.nan and are
excluded from the error metrics, so all models are compared on the same basis.
"""

import numpy as np
import pandas as pd

from .config import (SEASON_LENGTH, HORIZON_PERIODS, MAPE_ALERT_THRESHOLD,
                     TRACKING_SIGNAL_LIMIT)


# ===========================================================================
# 3.2  FORECASTING METHODS
# ===========================================================================
def moving_average(y, n=3):
    """Simple moving average of the last n observations."""
    y = np.asarray(y, dtype=float)
    T = len(y)
    fitted = np.full(T, np.nan)
    for t in range(n, T):
        fitted[t] = y[t - n:t].mean()
    forecast = y[T - n:].mean()
    return fitted, np.full(HORIZON_PERIODS, forecast)


def weighted_moving_average(y, weights=(0.5, 0.3, 0.2)):
    """Weighted MA - most recent period carries the largest weight."""
    y = np.asarray(y, dtype=float)
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()
    n = len(w)
    T = len(y)
    fitted = np.full(T, np.nan)
    for t in range(n, T):
        window = y[t - n:t][::-1]          # most recent first
        fitted[t] = float(np.dot(window, w))
    forecast = float(np.dot(y[T - n:][::-1], w))
    return fitted, np.full(HORIZON_PERIODS, forecast)


def single_exponential_smoothing(y, alpha=0.3):
    """SES:  F(t+1) = alpha*A(t) + (1-alpha)*F(t).  Flat forward forecast."""
    y = np.asarray(y, dtype=float)
    T = len(y)
    fitted = np.full(T, np.nan)
    level = y[0]
    fitted[0] = np.nan                     # no forecast for the first period
    for t in range(1, T):
        fitted[t] = level                  # forecast for t, made at t-1
        level = alpha * y[t] + (1 - alpha) * level
    return fitted, np.full(HORIZON_PERIODS, level)


def holt_linear(y, alpha=0.3, beta=0.1):
    """Holt's double exponential smoothing - captures level AND trend."""
    y = np.asarray(y, dtype=float)
    T = len(y)
    fitted = np.full(T, np.nan)
    level = y[0]
    trend = y[1] - y[0] if T > 1 else 0.0
    for t in range(1, T):
        fitted[t] = level + trend          # forecast for t
        prev_level = level
        level = alpha * y[t] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
    horizon = np.array([level + (m + 1) * trend for m in range(HORIZON_PERIODS)])
    return fitted, np.maximum(horizon, 0)


def holt_winters(y, alpha=0.3, beta=0.1, gamma=0.2, s=SEASON_LENGTH):
    """Holt-Winters additive - level, trend and seasonality.

    Needs at least two complete seasonal cycles; returns NaN otherwise so the
    model-selection step simply skips it.
    """
    y = np.asarray(y, dtype=float)
    T = len(y)
    if T < 2 * s:
        return np.full(T, np.nan), np.full(HORIZON_PERIODS, np.nan)

    # --- initialisation from the first two seasonal cycles ------------------
    season_means = [y[i * s:(i + 1) * s].mean() for i in range(T // s)]
    level = season_means[0]
    trend = (season_means[1] - season_means[0]) / s
    seasonal = np.array([y[i] - level for i in range(s)], dtype=float)

    fitted = np.full(T, np.nan)
    for t in range(s, T):
        fitted[t] = level + trend + seasonal[t % s]
        prev_level = level
        level = alpha * (y[t] - seasonal[t % s]) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasonal[t % s] = gamma * (y[t] - level) + (1 - gamma) * seasonal[t % s]

    horizon = np.array([
        level + (m + 1) * trend + seasonal[(T + m) % s]
        for m in range(HORIZON_PERIODS)
    ])
    return fitted, np.maximum(horizon, 0)


# ===========================================================================
# 3.3  ACCURACY METRICS
# ===========================================================================
def accuracy_metrics(actual, fitted):
    """MAD, MSE, RMSE, MAPE, Bias (ME) and Tracking Signal.

    Only periods where the model produced a forecast are scored.
    """
    actual = np.asarray(actual, dtype=float)
    fitted = np.asarray(fitted, dtype=float)
    mask = ~np.isnan(fitted)
    if mask.sum() == 0:
        return dict(N=0, MAD=np.nan, MSE=np.nan, RMSE=np.nan,
                    MAPE=np.nan, Bias=np.nan, TrackingSignal=np.nan)

    a, f = actual[mask], fitted[mask]
    err = a - f
    mad = np.mean(np.abs(err))
    mse = np.mean(err ** 2)

    nz = a != 0                                   # guard against divide-by-zero
    mape = np.mean(np.abs(err[nz] / a[nz])) * 100 if nz.any() else np.nan
    bias = np.mean(err)
    ts = (np.sum(err) / mad) if mad > 0 else 0.0  # cumulative error / MAD

    return dict(N=int(mask.sum()), MAD=round(mad, 2), MSE=round(mse, 2),
                RMSE=round(np.sqrt(mse), 2), MAPE=round(mape, 2),
                Bias=round(bias, 2), TrackingSignal=round(ts, 2))


# ===========================================================================
# Parameter tuning - transparent grid search minimising MSE
# ===========================================================================
def _tune(func, y, grids):
    """Grid-search smoothing constants; returns (best_params, fitted, horizon)."""
    best = (np.inf, None, None, None)
    keys = list(grids)
    from itertools import product
    for combo in product(*(grids[k] for k in keys)):
        params = dict(zip(keys, combo))
        fitted, horizon = func(y, **params)
        m = accuracy_metrics(y, fitted)
        score = m["MSE"]
        if score is not None and not np.isnan(score) and score < best[0]:
            best = (score, params, fitted, horizon)
    return best[1], best[2], best[3]


GRID_A  = {"alpha": np.round(np.arange(0.05, 0.96, 0.05), 2)}
GRID_AB = {"alpha": np.round(np.arange(0.10, 0.91, 0.10), 2),
           "beta":  np.round(np.arange(0.05, 0.61, 0.05), 2)}
GRID_ABG = {"alpha": np.round(np.arange(0.10, 0.81, 0.10), 2),
            "beta":  np.round(np.arange(0.05, 0.41, 0.05), 2),
            "gamma": np.round(np.arange(0.10, 0.81, 0.10), 2)}


def run_all_methods(y):
    """3.2 - run every method against one product's history.

    Returns {method_name: {params, fitted, horizon, metrics}}
    """
    y = np.asarray(y, dtype=float)
    results = {}

    for n in (3, 5):
        fitted, horizon = moving_average(y, n)
        results[f"MA({n})"] = dict(params={"n": n}, fitted=fitted,
                                   horizon=horizon, metrics=accuracy_metrics(y, fitted))

    fitted, horizon = weighted_moving_average(y, (0.5, 0.3, 0.2))
    results["WMA(3)"] = dict(params={"weights": "0.5/0.3/0.2"}, fitted=fitted,
                             horizon=horizon, metrics=accuracy_metrics(y, fitted))

    p, fitted, horizon = _tune(single_exponential_smoothing, y, GRID_A)
    results["SES"] = dict(params=p, fitted=fitted, horizon=horizon,
                          metrics=accuracy_metrics(y, fitted))

    p, fitted, horizon = _tune(holt_linear, y, GRID_AB)
    results["Holt"] = dict(params=p, fitted=fitted, horizon=horizon,
                           metrics=accuracy_metrics(y, fitted))

    if len(y) >= 2 * SEASON_LENGTH:
        p, fitted, horizon = _tune(holt_winters, y, GRID_ABG)
        results["Holt-Winters"] = dict(params=p, fitted=fitted, horizon=horizon,
                                       metrics=accuracy_metrics(y, fitted))
    return results


# ===========================================================================
# 3.4 / 3.5  MAIN ENTRY POINT
# ===========================================================================
def run_forecasting(demand_df, horizon=HORIZON_PERIODS, criterion="MAPE",
                    forced_methods=None):
    """Full Block 3.

    forced_methods : optional {item: method_name} to override the automatic
                     selection - used when a planner approves the "force a
                     trend-capable model" recommendation in Block 12.

    Returns
    -------
    forecast_df   : Item, Period, Forecast, Method   (the forward plan)
    accuracy_df   : every model x every product with all error metrics
    selection_df  : the chosen model per product + alert flag
    fit_df        : actual vs fitted history for the selected model (charting)
    """
    forced_methods = forced_methods or {}
    forecast_rows, accuracy_rows, selection_rows, fit_rows = [], [], [], []

    for item, g in demand_df.groupby("Item"):
        g = g.sort_values("Period")
        y = g["Demand"].to_numpy(dtype=float)
        periods = g["Period"].to_numpy()

        results = run_all_methods(y)

        # ---- 3.3 record accuracy of every method --------------------------
        for method, r in results.items():
            row = {"Item": item, "Method": method,
                   "Parameters": _fmt_params(r["params"])}
            row.update(r["metrics"])
            accuracy_rows.append(row)

        # ---- 3.4 select best model (lowest MAPE by default) ---------------
        valid = {m: r for m, r in results.items()
                 if not np.isnan(r["metrics"].get(criterion, np.nan))}
        if item in forced_methods and forced_methods[item] in valid:
            best_method = forced_methods[item]
        else:
            best_method = min(valid, key=lambda m: valid[m]["metrics"][criterion])
        best = valid[best_method]

        # ---- 3.5 forward forecast -----------------------------------------
        for i, val in enumerate(best["horizon"][:horizon], start=1):
            forecast_rows.append({
                "Item": item, "Period": i,
                "Forecast": int(round(max(0, val))),
                "Method": best_method,
            })

        # actual vs fitted for the chart
        for p, a, f in zip(periods, y, best["fitted"]):
            fit_rows.append({"Item": item, "Period": int(p), "Actual": a,
                             "Fitted": None if np.isnan(f) else round(float(f), 1)})

        m = best["metrics"]
        alert = []
        if m["MAPE"] > MAPE_ALERT_THRESHOLD:
            alert.append(f"MAPE {m['MAPE']}% exceeds {MAPE_ALERT_THRESHOLD}% threshold")
        if abs(m["TrackingSignal"]) > TRACKING_SIGNAL_LIMIT:
            alert.append(f"Tracking signal {m['TrackingSignal']} indicates bias")

        selection_rows.append({
            "Item": item,
            "SelectedMethod": best_method,
            "Forced": item in forced_methods,
            "Parameters": _fmt_params(best["params"]),
            "MAPE": m["MAPE"], "MAD": m["MAD"], "RMSE": m["RMSE"],
            "Bias": m["Bias"], "TrackingSignal": m["TrackingSignal"],
            "Alert": "; ".join(alert) if alert else "OK",
            "AlertFlag": bool(alert),
        })

    return (pd.DataFrame(forecast_rows),
            pd.DataFrame(accuracy_rows),
            pd.DataFrame(selection_rows),
            pd.DataFrame(fit_rows))


def _fmt_params(p):
    if not p:
        return "-"
    return ", ".join(f"{k}={v}" for k, v in p.items())

#!/usr/bin/env python3
"""Brain Labs data-analysis bridge for Python-native alpha research.

This script has two roles:

1. Run inside Brain Labs with the platform `brain` package and export raw panel
   diagnostics as JSON.
2. Run locally to generate a pasteable Labs script, analyze CSV/synthetic data,
   and ingest Labs JSON into the repository tracking format.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.python_alpha_policy import field_role, is_auxiliary_group_key_field


DEFAULT_SENTINELS = [-999999999.0, -999999.0, -9999.0, -999.0, -99.0, -1.0, 0.0, 99.0, 999.0, 9999.0]
DEFAULT_FORUM_SOURCES = [
    {
        "article_id": "40736166342679",
        "title": "Python Alpha inspiration campaign: Python-native operators, AI-agent iteration, decorrelation/performance improvement",
    },
    {
        "article_id": "34425237001495",
        "title": "Brain Lab single-field exploration and preprocessing",
    },
    {
        "article_id": "33638658994711",
        "title": "Brain Labs aligned data-field correlation",
    },
    {
        "article_id": "37313013232023",
        "title": "Coverage and Date Coverage calculation caveats",
    },
    {
        "article_id": "34888787908631",
        "title": "Brain Lab field pruning with pairwise correlation",
    },
    {
        "article_id": "36444272754711",
        "title": "imbalance5 example and AI-assisted iteration notes",
    },
    {
        "article_id": "36672024628119",
        "title": "IND robust universe Sharpe optimization: diagnose gap, short backfill, decay, and stop rules",
    },
]

MAX_ECONOMIC_FIELDS = 2
ROBUST_GATE = 1.0
ROBUST_NEAR_GAP = 0.10
ROBUST_ABANDON_LEVEL = 0.50


def expected_target(region: str, universe: str, delay: int) -> dict[str, Any]:
    return {
        "region": region,
        "universe": universe,
        "delay": delay,
        "language_downstream": "PYTHON",
    }


def target_consistency(observed: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    checks = {
        key: observed.get(key) == expected.get(key)
        for key in ("region", "universe", "delay")
    }
    matched = all(checks.values())
    return {
        "expected_target": expected,
        "observed_target": observed,
        "matched": matched,
        "checks": checks,
        "decision_use": "production_decisive" if matched else "diagnostic_only",
    }


def is_pv_dataset(dataset_id: str) -> bool:
    lowered = dataset_id.lower()
    return lowered == "pv" or lowered.startswith("pv") or lowered.startswith("pricevolume")


def is_pv_field(field_id: str, description: str = "") -> bool:
    lowered = f"{field_id} {description}".lower()
    pv_tokens = (
        "price volume",
        "price-volume",
        "volume weighted",
        "trading volume",
        "shares traded",
        "dollar volume",
        "vwap",
    )
    return field_id.lower().startswith("pv") or any(token in lowered for token in pv_tokens)


def is_direct_market_data_field(field_id: str, description: str = "") -> bool:
    """Reject raw market-data fields while allowing named non-PV model scores."""
    lowered = f"{field_id} {description}".lower()
    allowed_model_phrases = (
        "value-momentum model",
        "value momentum model",
    )
    if any(phrase in lowered for phrase in allowed_model_phrases):
        return False
    direct_tokens = (
        "closing price",
        "opening price",
        "highest traded price",
        "lowest traded price",
        "daily highest",
        "daily lowest",
        "stock return",
        "total return",
        "return momentum",
        "price momentum",
        "price trend",
        "market capitalization",
        "market cap",
        "shares outstanding",
        "trading volume",
        "traded volume",
        "volume-weighted",
        "volume weighted",
        "vwap",
        "volatility",
        "beta",
        "money flow",
        "liquidity",
    )
    direct_id_prefixes = (
        "closing_price",
        "opening_price",
        "highest_price",
        "lowest_price",
        "trading_volume",
        "market_cap",
        "market_capitalization",
        "mktcap",
        "shares_outstanding",
    )
    normalized_id = field_id.lower()
    return (
        normalized_id.startswith(direct_id_prefixes)
        or normalized_id.endswith("_mktcap")
        or normalized_id.endswith("mktcap")
        or any(token in lowered for token in direct_tokens)
    )


def build_constraint_checks(
    dataset_id: str,
    mcp_fields: list[dict[str, Any]],
    observed_target: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    roles = {str(f.get("id", "")): field_role(f) for f in mcp_fields if f.get("id")}
    auxiliary_group_key_fields = [field_id for field_id, role in roles.items() if role == "auxiliary_group"]
    economic_fields = [
        field_id for field_id, role in roles.items() if role == "economic_matrix"
    ]
    invalid_type_fields = [
        field_id
        for field_id, role in roles.items()
        if role.startswith("forbidden_")
    ]
    pv_fields = [
        f.get("id")
        for f in mcp_fields
        if is_pv_field(str(f.get("id", "")), str(f.get("description", "")))
    ]
    direct_market_fields = [
        f.get("id")
        for f in mcp_fields
        if is_direct_market_data_field(str(f.get("id", "")), str(f.get("description", "")))
    ]
    violations = []
    if is_pv_dataset(dataset_id):
        violations.append("pv_dataset_forbidden")
    if pv_fields:
        violations.append("pv_fields_forbidden")
    if direct_market_fields:
        violations.append("direct_market_data_fields_forbidden")
    if invalid_type_fields:
        violations.append("non_matrix_fields_present")
    if len(economic_fields) > MAX_ECONOMIC_FIELDS:
        violations.append("too_many_economic_fields")
    consistency = target_consistency(observed_target, expected)
    if not consistency["matched"]:
        violations.append("target_mismatch")
    return {
        "max_economic_fields": MAX_ECONOMIC_FIELDS,
        "economic_fields": economic_fields,
        "economic_field_count": len(economic_fields),
        "auxiliary_group_key_fields": auxiliary_group_key_fields,
        "auxiliary_group_key_field_count": len(auxiliary_group_key_fields),
        "forbid_pv_fields": True,
        "matrix_only": False,
        "field_policy": "MATRIX economic fields; verified MATRIX/GROUP auxiliary group keys; VECTOR forbidden",
        "target_consistency": consistency,
        "pv_fields": pv_fields,
        "direct_market_data_fields": direct_market_fields,
        "non_matrix_fields": invalid_type_fields,
        "field_roles": roles,
        "violations": violations,
        "artifact_decision_use": "production_decisive" if not violations else "diagnostic_only",
    }


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def require_pandas_numpy():
    try:
        import numpy as np
        import pandas as pd
    except Exception as exc:  # pragma: no cover - exercised in environments without deps
        raise SystemExit(
            "This command needs numpy and pandas. In Brain Labs they are usually available; "
            "locally run with: uv run --with numpy --with pandas python scripts/labs_data_analysis_agent.py ..."
        ) from exc
    return np, pd


def finite_values(df: Any):
    np, _pd = require_pandas_numpy()
    arr = df.to_numpy(dtype=float, copy=False)
    mask = np.isfinite(arr)
    return arr[mask]


def summary(values: Any) -> dict[str, float | int | None]:
    np, _pd = require_pandas_numpy()
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "min": None,
            "p01": None,
            "p05": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    qs = np.nanpercentile(arr, [1, 5, 25, 50, 75, 95, 99])
    return {
        "count": int(arr.size),
        "mean": float(np.nanmean(arr)),
        "std": float(np.nanstd(arr)),
        "min": float(np.nanmin(arr)),
        "p01": float(qs[0]),
        "p05": float(qs[1]),
        "p25": float(qs[2]),
        "p50": float(qs[3]),
        "p75": float(qs[4]),
        "p95": float(qs[5]),
        "p99": float(qs[6]),
        "max": float(np.nanmax(arr)),
    }


def safe_ratio(num: float, den: float) -> float | None:
    if den == 0 or not math.isfinite(den):
        return None
    return float(num / den)


def avg_gap_days(index: Any, mask_df: Any) -> dict[str, float | int | None]:
    np, _pd = require_pandas_numpy()
    gaps = []
    date_values = _pd.to_datetime(index)
    for col in mask_df.columns:
        positions = np.flatnonzero(mask_df[col].to_numpy(dtype=bool, na_value=False))
        if positions.size < 2:
            continue
        dates = date_values[positions]
        deltas = (dates[1:] - dates[:-1]).days
        gaps.extend([float(x) for x in deltas if x > 0])
    return summary(gaps)


def classify_field(metrics: dict[str, Any]) -> str:
    coverage = metrics["coverage_by_date"]["mean"] or 0.0
    zero_ratio = metrics["zero_ratio"]
    finite = metrics["finite_summary"]
    change_rate = metrics["change_rate_by_date"]["mean"] or 0.0
    unique_count = metrics.get("approx_unique_count")
    p50 = finite.get("p50")
    min_v = finite.get("min")
    max_v = finite.get("max")
    p99 = finite.get("p99")

    if unique_count is not None and unique_count <= 2:
        return "binary_or_categorical"
    if unique_count is not None and unique_count <= 12:
        return "binary_or_categorical"
    if coverage > 0.6 and min_v is not None and max_v is not None and -0.05 <= min_v <= 1.05 and -0.05 <= max_v <= 1.05:
        if change_rate < 0.03:
            return "low_frequency_step"
        return "dense_bounded_score"
    if zero_ratio is not None and zero_ratio > 0.80:
        return "sparse_event"
    if coverage > 0.6 and change_rate < 0.03:
        return "low_frequency_step"
    if p50 not in (None, 0.0) and p99 is not None and abs(p99 / p50) > 100:
        return "ratio_or_scale_sensitive"
    return "dense_continuous"


def recommend_for_field(field_id: str, description: str, field_class: str, metrics: dict[str, Any]) -> dict[str, Any]:
    desc = (description or "").lower()
    if is_pv_field(field_id, description):
        decision = "reject"
        extractor = None
        reason = "pv or price-volume field is forbidden by the active research constraint"
    elif is_direct_market_data_field(field_id, description):
        decision = "reject"
        extractor = None
        reason = "direct market-data fields are excluded by the active no-PV/no-market-data constraint"
    elif field_class == "metadata_or_identifier":
        decision = "reject"
        extractor = None
        reason = "metadata or identifier-like field has no raw-data extraction edge"
    elif field_class == "sparse_event":
        decision = "accept"
        extractor = "STATEFUL_EVENT_DECAY"
        reason = "sparse event structure can use Python stateful inventory or event decay"
    elif field_class == "low_frequency_step":
        decision = "accept"
        extractor = "SURPRISE_VS_BASELINE"
        reason = "step-like updates can be converted into surprise versus persistent baseline"
    elif field_class == "dense_bounded_score":
        decision = "diagnostic_only"
        extractor = "DENSE_SCORE_REGIME_CHANGE"
        reason = "bounded score is likely engineered; validate regime changes before simulating raw level"
    elif "market cap" in desc or "capitalization" in desc or field_id.endswith("mktcap"):
        decision = "diagnostic_only"
        extractor = "ROBUST_RATIO"
        reason = "scale field is context only; keep only if it improves one economic mechanism"
    elif field_class == "ratio_or_scale_sensitive":
        decision = "diagnostic_only"
        extractor = "ROBUST_RATIO"
        reason = "scale-sensitive field needs safe denominator or robust clipping before use"
    else:
        decision = "diagnostic_only"
        extractor = "SURPRISE_VS_BASELINE"
        reason = "dense continuous field needs a non-level Python extraction edge"

    if "score" in field_id.lower() and "oil" in desc:
        extractor = "DENSE_SCORE_REGIME_CHANGE"
        reason = "oil-shock resilience score is crowded as a raw level; prefer regime-change extraction"

    return {
        "field": field_id,
        "decision": decision,
        "extractor_family": extractor,
        "reason": reason,
        "coverage_mean": metrics["coverage_by_date"]["mean"],
        "change_rate_mean": metrics["change_rate_by_date"]["mean"],
        "zero_ratio": metrics["zero_ratio"],
    }


def analyze_field(df: Any, field_id: str) -> dict[str, Any]:
    np, pd = require_pandas_numpy()
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df = df.copy()
            df.index = pd.to_datetime(df.index)
        except Exception:
            pass

    numeric = df.apply(pd.to_numeric, errors="coerce")
    rows, cols = numeric.shape
    total = rows * cols
    finite_mask = np.isfinite(numeric.to_numpy(dtype=float, copy=False))
    nan_mask = np.isnan(numeric.to_numpy(dtype=float, copy=False))
    values = finite_values(numeric)
    nonzero_values = values[values != 0]

    coverage_by_date = finite_mask.mean(axis=1) if cols else np.array([])
    coverage_by_instrument = finite_mask.mean(axis=0) if rows else np.array([])
    zero_by_date = ((numeric == 0).to_numpy(dtype=bool) & finite_mask).mean(axis=1) if cols else np.array([])
    nonzero_by_date = ((numeric != 0).to_numpy(dtype=bool) & finite_mask).mean(axis=1) if cols else np.array([])

    diff = numeric.diff()
    changed = (diff.abs() > 1e-12) & numeric.notna() & numeric.shift(1).notna()
    change_rate_by_date = changed.mean(axis=1).fillna(0.0).to_numpy(dtype=float)
    abs_sum = float(np.nansum(np.abs(values)))
    turnover_num = float(np.nansum(np.abs(diff.to_numpy(dtype=float, copy=False))))
    turnover_proxy = safe_ratio(turnover_num, abs_sum)

    sentinel_rates = {}
    for sentinel in DEFAULT_SENTINELS:
        if total:
            sentinel_rates[str(sentinel)] = float(np.nansum(numeric.to_numpy(dtype=float, copy=False) == sentinel) / total)

    finite_s = pd.Series(values)
    skew = float(finite_s.skew()) if values.size > 2 else None
    kurtosis = float(finite_s.kurt()) if values.size > 3 else None
    q = summary(values)
    q_nonzero = summary(nonzero_values)

    if values.size:
        p25 = q["p25"]
        p75 = q["p75"]
        if p25 is not None and p75 is not None:
            iqr = p75 - p25
            lo = p25 - 1.5 * iqr
            hi = p75 + 1.5 * iqr
            winsor_values = np.clip(values, lo, hi)
        else:
            winsor_values = values
    else:
        winsor_values = values

    unique_count = None
    if values.size:
        sample = values if values.size <= 50000 else values[:50000]
        unique_count = int(np.unique(sample).size)

    metrics = {
        "field": field_id,
        "shape": {"dates": int(rows), "instruments": int(cols), "cells": int(total)},
        "date_range": {
            "start": str(numeric.index.min().date()) if rows and hasattr(numeric.index.min(), "date") else None,
            "end": str(numeric.index.max().date()) if rows and hasattr(numeric.index.max(), "date") else None,
        },
        "finite_ratio": float(finite_mask.mean()) if total else None,
        "nan_ratio": float(nan_mask.mean()) if total else None,
        "zero_ratio": float(np.nansum((numeric.to_numpy(dtype=float, copy=False) == 0) & finite_mask) / total) if total else None,
        "nonzero_ratio": float(nonzero_values.size / total) if total else None,
        "sentinel_rates": sentinel_rates,
        "coverage_by_date": summary(coverage_by_date),
        "coverage_by_instrument": summary(coverage_by_instrument),
        "zero_by_date": summary(zero_by_date),
        "nonzero_by_date": summary(nonzero_by_date),
        "change_rate_by_date": summary(change_rate_by_date),
        "avg_change_gap_days": avg_gap_days(numeric.index, changed),
        "avg_nonzero_gap_days": avg_gap_days(numeric.index, (numeric != 0) & numeric.notna()),
        "turnover_proxy": turnover_proxy,
        "finite_summary": q,
        "nonzero_summary": q_nonzero,
        "winsorized_iqr_summary": summary(winsor_values),
        "skewness": skew,
        "kurtosis": kurtosis,
        "approx_unique_count": unique_count,
    }
    metrics["field_classification"] = classify_field(metrics)
    return metrics


def mean_corr(df1: Any, df2: Any) -> dict[str, Any]:
    np, _pd = require_pandas_numpy()
    df1, df2 = df1.align(df2, join="inner", axis=0)
    df1, df2 = df1.align(df2, join="inner", axis=1)
    ts_corrs = []
    for col in df1.columns:
        s1 = df1[col]
        s2 = df2[col]
        valid = s1.notna() & s2.notna()
        if valid.sum() < 3:
            continue
        if float(s1[valid].std()) == 0.0 or float(s2[valid].std()) == 0.0:
            continue
        corr = s1[valid].corr(s2[valid])
        if np.isfinite(corr):
            ts_corrs.append(float(corr))

    xs_corrs = []
    for idx in df1.index:
        s1 = df1.loc[idx]
        s2 = df2.loc[idx]
        valid = s1.notna() & s2.notna()
        if valid.sum() < 3:
            continue
        if float(s1[valid].std()) == 0.0 or float(s2[valid].std()) == 0.0:
            continue
        corr = s1[valid].corr(s2[valid])
        if np.isfinite(corr):
            xs_corrs.append(float(corr))

    return {
        "aligned_dates": int(df1.shape[0]),
        "aligned_instruments": int(df1.shape[1]),
        "mean_time_series_corr_by_instrument": summary(ts_corrs),
        "mean_cross_section_corr_by_date": summary(xs_corrs),
    }


def analyze_frames(
    frames: dict[str, Any],
    field_meta: dict[str, dict[str, Any]] | None,
    dataset_id: str,
    region: str,
    universe: str,
    delay: int,
) -> dict[str, Any]:
    metrics = {}
    classifications = {}
    recommendations = []
    for field_id, df in frames.items():
        field_metrics = analyze_field(df, field_id)
        metrics[field_id] = field_metrics
        classifications[field_id] = field_metrics["field_classification"]
        meta = (field_meta or {}).get(field_id, {})
        recommendations.append(
            recommend_for_field(
                field_id,
                str(meta.get("description", "")),
                field_metrics["field_classification"],
                field_metrics,
            )
        )

    corrs = {}
    ids = list(frames)
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            corrs[f"{left}__{right}"] = mean_corr(frames[left], frames[right])

    accepted = [r for r in recommendations if r["decision"] in {"accept", "diagnostic_only"}]
    rejected = [r for r in recommendations if r["decision"] == "reject"]
    return {
        "ts": utc_ts(),
        "target": expected_target(region, universe, delay),
        "dataset_id": dataset_id,
        "constraint_checks": build_constraint_checks(
            dataset_id,
            list((field_meta or {}).values()),
            expected_target(region, universe, delay),
            expected_target(region, universe, delay),
        ),
        "labs_metrics": metrics,
        "field_classification": classifications,
        "correlation_pruning": corrs,
        "accepted_mechanisms": accepted,
        "rejected_fields_or_mechanisms": rejected,
        "preprocessing_actions": build_preprocessing_actions(metrics, classifications, field_meta or {}),
        "alpha_optimization_plan": build_alpha_optimization_plan(
            dataset_id,
            metrics,
            classifications,
            corrs,
            field_meta or {},
        ),
        "python_alpha_implications": build_python_implications(dataset_id, accepted, classifications),
        "forum_sources": DEFAULT_FORUM_SOURCES,
    }


def build_preprocessing_actions(
    metrics: dict[str, dict[str, Any]],
    classifications: dict[str, str],
    field_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    actions = []
    for field_id, field_metrics in metrics.items():
        cls = classifications.get(field_id)
        description = str(field_meta.get(field_id, {}).get("description", ""))
        finite_summary = field_metrics.get("finite_summary", {})
        sentinels = {
            key: val
            for key, val in field_metrics.get("sentinel_rates", {}).items()
            if isinstance(val, (int, float)) and val > 0.001
        }
        action: dict[str, Any] = {
            "field": field_id,
            "classification": cls,
            "description": description,
            "missing_policy": "copy mutable slices, replace extreme negative sentinels with np.nan, then pasteurize to universe",
            "normalization_policy": "single cross-sectional centering or ranking only; avoid rank-of-rank and repeated standardization",
            "sentinel_rates_over_0p1pct": sentinels,
            "finite_p05_p50_p95": [
                finite_summary.get("p05"),
                finite_summary.get("p50"),
                finite_summary.get("p95"),
            ],
        }
        if is_pv_field(field_id, description) or is_direct_market_data_field(field_id, description):
            action["preferred_transform"] = "reject_before_alpha_design"
            action["implementation_hint"] = "exclude under the active no-PV/no-direct-market-data constraint"
        elif cls == "dense_bounded_score":
            action["preferred_transform"] = "bounded_score_transition_or_residual"
            action["implementation_hint"] = (
                "clip to the observed bounded support, preserve level information, and test one residualization "
                "against an explicit risk/context vector before trying more windows"
            )
        elif cls == "low_frequency_step":
            action["preferred_transform"] = "surprise_vs_persistent_baseline"
            action["implementation_hint"] = "compare current value with a trailing or stored baseline; do not treat stale level as daily signal"
        elif cls == "sparse_event":
            action["preferred_transform"] = "stateful_event_decay"
            action["implementation_hint"] = "use typed store only for event memory; otherwise keep a short platform lookback"
        elif cls == "ratio_or_scale_sensitive":
            action["preferred_transform"] = "risk_context_or_safe_ratio"
            action["implementation_hint"] = "use as auxiliary risk/context only unless it is the explicit economic field"
        else:
            action["preferred_transform"] = "diagnose_then_center"
            action["implementation_hint"] = "start with robust clipping and one cross-sectional centering step"
        actions.append(action)
    return actions


def build_alpha_optimization_plan(
    dataset_id: str,
    metrics: dict[str, dict[str, Any]],
    classifications: dict[str, str],
    corrs: dict[str, Any],
    field_meta: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    field_meta = field_meta or {}
    if is_pv_dataset(dataset_id):
        return [
            {
                "priority": 0,
                "candidate_family": "reject_pv_dataset",
                "why": "The active objective forbids pv / price-volume datasets and fields.",
                "python_design": "Do not simulate this dataset for the current IND validation run.",
                "stop_rule": "Switch to the next unlit non-pv MATRIX dataset.",
            }
        ]
    direct_market_fields = [
        field_id
        for field_id, meta in field_meta.items()
        if is_direct_market_data_field(field_id, str(meta.get("description", "")))
    ]
    if direct_market_fields and len(direct_market_fields) == len(field_meta):
        return [
            {
                "priority": 0,
                "candidate_family": "reject_direct_market_data_fields",
                "why": "All available MATRIX fields are direct price, return, volume, shares, market-cap, volatility, beta, liquidity, or raw momentum fields.",
                "python_design": "Do not simulate this dataset under the active no-PV/no-direct-market-data constraint.",
                "stop_rule": "Switch to a dataset with non-market economic content.",
                "rejected_fields": direct_market_fields,
            }
        ]
    robust_plan = build_ind_robust_repair_plan(metrics, classifications, field_meta)
    plan.extend(robust_plan)
    if dataset_id == "imbalance5" and "imb5_score" in metrics:
        score = metrics["imb5_score"]
        coverage = score.get("coverage_by_date", {}).get("mean")
        change_rate = score.get("change_rate_by_date", {}).get("mean")
        plan.append(
            {
                "priority": 1,
                "candidate_family": "score_transition_without_market_data_context",
                "why": (
                    "imb5_score is a dense bounded oil-resilience score, but the active constraint excludes "
                    "market-cap and other direct market-data fields as controls."
                ),
                "trigger_from_labs": {
                    "coverage_by_date_mean": coverage,
                    "change_rate_by_date_mean": change_rate,
                    "classification": classifications.get("imb5_score"),
                },
                "python_design": (
                    "latest-valid score backfill -> one bounded-score transition or baseline surprise -> "
                    "single cross-sectional centering/ranking -> universe pasteurization; do not use imb5_mktcap"
                ),
                "stop_rule": "run one candidate first; only mutate decay/truncation if robust and risk-neutral gates are close",
            }
        )
        if "imb5_mktcap" in metrics:
            corr = corrs.get("imb5_score__imb5_mktcap") or corrs.get("imb5_mktcap__imb5_score")
            plan.append(
                {
                    "priority": 2,
                    "candidate_family": "reject_imbalance_market_cap_context",
                    "why": "imb5_mktcap is direct market-cap data and is excluded by the active no-PV/no-direct-market-data constraint.",
                    "field_correlation_evidence": corr,
                    "python_design": "do not use size residualization, size buckets, or additive size exposure in this validation run",
                    "stop_rule": "switch to score-only preprocessing or a non-market auxiliary MATRIX group key",
                }
            )
    for pair, corr in corrs.items():
        xs = corr.get("mean_cross_section_corr_by_date", {})
        if xs.get("mean") is not None and abs(xs["mean"]) > 0.80:
            plan.append(
                {
                    "priority": 3,
                    "candidate_family": "duplicate_field_pruning",
                    "why": f"{pair} has high average cross-sectional correlation",
                    "field_correlation_evidence": corr,
                    "python_design": "keep one field unless the second field changes the mechanism through residualization",
                    "stop_rule": "do not simulate near-duplicates as separate alpha fields",
                }
            )
    for field_id, cls in classifications.items():
        meta = field_meta.get(field_id, {})
        description = str(meta.get("description", ""))
        if is_pv_field(field_id, description) or is_direct_market_data_field(field_id, description):
            plan.append(
                {
                    "priority": 0,
                    "candidate_family": "reject_forbidden_market_field",
                    "why": f"{field_id} is identified as pv/direct market data, which is forbidden for this objective.",
                    "python_design": "Exclude this field before candidate generation.",
                    "stop_rule": "No simulation with this field under the active constraint.",
                }
            )
            continue
        usage = max(int(meta.get("userCount") or 0), int(meta.get("alphaCount") or 0))
        crowded = usage >= 500
        if crowded:
            plan.append(
                {
                    "priority": 4,
                    "candidate_family": "python_decorrelation_from_crowded_field",
                    "field": field_id,
                    "why": (
                        "The forum's Python Alpha inspiration direction explicitly encourages improving an existing "
                        "Fast Expression-style idea or lowering correlation; high field usage means raw-level exposure "
                        "has elevated production-correlation risk."
                    ),
                    "python_design": (
                        "Use at most two MATRIX economic fields; convert the raw field into one Python-native vector "
                        "through surprise versus baseline, sparse-event memory, robust clipping, or residualization "
                        "against one explicit context field rather than copying a raw Fast Expression level."
                    ),
                    "stop_rule": "Do not enter SelfCorr/ProdCorr until hard IS and risk-neutral gates pass.",
                    "field_usage": {
                        "userCount": meta.get("userCount"),
                        "alphaCount": meta.get("alphaCount"),
                    },
                }
            )
        elif cls == "sparse_event":
            plan.append(
                {
                    "priority": 4,
                    "candidate_family": "low_crowding_stateful_event_decay",
                    "field": field_id,
                    "why": "Sparse non-pv MATRIX data can use Python store/state to express event memory with low field count.",
                    "python_design": "Build one event-decay vector from this field; optionally add one non-pv context field only if it explains coverage or regime.",
                    "stop_rule": "One diagnostic simulation before any parameter grid.",
                }
            )
    return plan


def build_ind_robust_repair_plan(
    metrics: dict[str, dict[str, Any]],
    classifications: dict[str, str],
    field_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Encode forum-derived IND robust-repair rules for Labs guidance.

    Labs does not know final simulation metrics, so this plan is conditional:
    apply only after a candidate has hard-IS strength but robust universe is the
    leading failure. The goal is to avoid open-ended grids.
    """
    plan: list[dict[str, Any]] = []
    for field_id, field_metrics in metrics.items():
        meta = field_meta.get(field_id, {})
        description = str(meta.get("description", ""))
        if is_pv_field(field_id, description) or is_direct_market_data_field(field_id, description):
            continue
        lowered = f"{field_id} {description}".lower()
        if "market cap" in lowered or "capitalization" in lowered or field_id.lower().endswith("mktcap"):
            continue
        cls = classifications.get(field_id)
        coverage = field_metrics.get("coverage_by_date", {}).get("mean")
        change_rate = field_metrics.get("change_rate_by_date", {}).get("mean")
        avg_gap = field_metrics.get("avg_change_gap_days", {}).get("p50")
        if cls in {"dense_bounded_score", "low_frequency_step", "dense_continuous"}:
            candidate_windows = [2, 5, 21, 63]
            if isinstance(avg_gap, (int, float)) and avg_gap > 0:
                candidate_windows = sorted({2, 5, int(max(2, min(63, round(avg_gap)))), 63})
            plan.append(
                {
                    "priority": 2,
                    "candidate_family": "ind_robust_gap_repair_protocol",
                    "field": field_id,
                    "forum_source": "36672024628119",
                    "why": (
                        "IND robust-universe failures often come from stale or overfilled data. "
                        "If a simulation has hard-IS strength and robust_universe_sharpe is close "
                        "to 1.0, test a short latest-valid/backfill axis and a short decay axis."
                    ),
                    "trigger_after_simulation": {
                        "level_1": f"{ROBUST_GATE - ROBUST_NEAR_GAP:.2f} <= robust_universe_sharpe < {ROBUST_GATE:.2f}",
                        "level_2": f"{ROBUST_ABANDON_LEVEL:.2f} <= robust_universe_sharpe < {ROBUST_GATE - ROBUST_NEAR_GAP:.2f}",
                        "abandon": f"robust_universe_sharpe < {ROBUST_ABANDON_LEVEL:.2f} unless a distinct mechanism is available",
                    },
                    "labs_evidence": {
                        "classification": cls,
                        "coverage_by_date_mean": coverage,
                        "change_rate_by_date_mean": change_rate,
                        "avg_change_gap_days_p50": avg_gap,
                    },
                    "python_design": (
                        "Keep the economic logic and fields fixed. Test at most one short latest-valid "
                        f"window from {candidate_windows} and one short platform decay from [2, 3, 5]."
                    ),
                    "risk_neutral_gate": "Do not run SelfCorr or ProdCorr unless risk_neutralized_sharpe > 1.58.",
                    "stop_rule": (
                        "Stop this mechanism if the robust repair does not also preserve turnover, latest-year "
                        "Sharpe, and strict risk-neutral Sharpe. Do not grid adjacent windows."
                    ),
                }
            )
    return plan


def normalize_metrics_for_guidance(labs_metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized = {}
    for field, metrics in labs_metrics.items():
        if not isinstance(metrics, dict):
            continue
        if "finite_summary" in metrics:
            normalized[field] = metrics
            continue
        normalized[field] = {
            "finite_ratio": metrics.get("finite_ratio"),
            "nan_ratio": metrics.get("nan_ratio"),
            "zero_ratio": metrics.get("zero_ratio"),
            "coverage_by_date": {"mean": metrics.get("coverage_by_date_mean")},
            "change_rate_by_date": {"mean": metrics.get("change_rate_by_date_mean")},
            "sentinel_rates": metrics.get("sentinel_rates", {}),
            "finite_summary": {
                "min": metrics.get("min"),
                "p05": metrics.get("p05"),
                "p50": metrics.get("p50"),
                "p95": metrics.get("p95"),
                "max": metrics.get("max"),
            },
        }
    return normalized


def merge_forum_sources(existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("article_id")): item for item in existing if isinstance(item, dict)}
    merged = []
    for source in DEFAULT_FORUM_SOURCES:
        article_id = str(source.get("article_id"))
        merged.append(by_id.pop(article_id, source))
    merged.extend(by_id.values())
    return merged


def build_python_implications(dataset_id: str, accepted: list[dict[str, Any]], classifications: dict[str, str]) -> list[str]:
    implications = []
    if dataset_id == "imbalance5" and "imb5_score" in classifications:
        implications.append(
            "For imbalance5, do not start from raw imb5_score level unless Labs shows unusually stable uncrowded behavior; prefer score regime change or surprise versus baseline."
        )
    for rec in accepted:
        extractor = rec.get("extractor_family")
        field = rec.get("field")
        if extractor == "STATEFUL_EVENT_DECAY":
            implications.append(f"{field}: use typed store only if sparse-event persistence is the core edge.")
        elif extractor == "DENSE_SCORE_REGIME_CHANGE":
            implications.append(f"{field}: test one platform-safe bounded-score transition diagnostic before window grids.")
        elif extractor == "SURPRISE_VS_BASELINE":
            implications.append(f"{field}: use a persistent or trailing baseline to avoid raw-level crowding.")
        elif extractor == "ROBUST_RATIO":
            implications.append(f"{field}: keep as context only if it forms one interpretable vector with the primary field.")
    return implications


def load_field_meta(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        records = data
    else:
        records = data.get("results", data.get("mcp_fields", []))
    return {str(item["id"]): item for item in records}


def load_evolution_review(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("evolution review must be a JSON object")
    return data


def load_dataset_records(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = data.get("results", data.get("recommendations", data.get("datasets", [])))
    else:
        records = []
    return [item for item in records if isinstance(item, dict)]


def load_os_clue_records(path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], {}
    if not isinstance(data, dict):
        return [], {}
    for key in ("low_self_high_prod_clues", "results", "alphas", "records", "clues"):
        records = data.get(key)
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)], data
    return [], data


def load_dataset_screen(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("dataset screen must be a JSON object")
    return data


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def first_numeric(record: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value: Any = record
        found = True
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                found = False
                break
        if found:
            parsed = coerce_float(value)
            if parsed is not None:
                return parsed
    return None


def first_text(record: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value: Any = record
        found = True
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                found = False
                break
        if found and value not in (None, ""):
            return str(value)
    return None


def normalize_os_clue_record(record: dict[str, Any]) -> dict[str, Any]:
    alpha_id = first_text(record, ["alpha_id", "id", "alphaId", "alpha.id"])
    self_corr = first_numeric(
        record,
        [
            "self_corr",
            "selfCorrelation",
            "self_correlation",
            "correlations.self",
            "checks.self_corr",
        ],
    )
    prod_corr = first_numeric(
        record,
        [
            "prod_corr",
            "production_corr",
            "productionCorrelation",
            "prodCorrelation",
            "correlations.production",
            "checks.prod_corr",
        ],
    )
    pyramid = first_text(record, ["pyramid", "category", "pyramid_category", "pyramid.name"])
    dataset_id = first_text(record, ["dataset_id", "datasetId", "dataset.id"])
    language = first_text(record, ["language", "settings.language", "simulationSettings.language"])
    max_trade = first_text(record, ["max_trade", "maxTrade", "settings.maxTrade", "simulationSettings.maxTrade"])
    code_clue = first_text(record, ["code_clue", "code", "expression", "alpha_expression", "description"]) or ""
    reason = first_text(record, ["reason", "notes", "rationale"]) or ""
    fields = record.get("fields", record.get("dataFields", record.get("data_fields", [])))
    if isinstance(fields, dict):
        fields = list(fields.values())
    if not isinstance(fields, list):
        fields = []
    return {
        "alpha_id": alpha_id,
        "self_corr": self_corr,
        "prod_corr": prod_corr,
        "pyramid": pyramid,
        "dataset_id": dataset_id,
        "language": language,
        "max_trade": max_trade,
        "code_clue": code_clue,
        "reason": reason,
        "fields": [item for item in fields if isinstance(item, dict)],
        "source_marks_usable": record.get("usable_under_active_unlit_no_pv_constraints"),
        "raw": record,
    }


def infer_dataset_id_from_text(text: str, dataset_screen_by_id: dict[str, dict[str, Any]]) -> str | None:
    lowered = text.lower()
    for dataset_id in sorted(dataset_screen_by_id, key=len, reverse=True):
        if dataset_id.lower() in lowered:
            return dataset_id
    return None


def screen_os_clues(
    clue_records: list[dict[str, Any]],
    dataset_screen: dict[str, Any],
    region: str,
    universe: str,
    delay: int,
    max_self_corr: float = 0.30,
    min_prod_corr: float = 0.64,
    forum_article_id: str = "40736166342679",
) -> dict[str, Any]:
    target = expected_target(region, universe, delay)
    dataset_rows = dataset_screen.get("datasets", []) if isinstance(dataset_screen, dict) else []
    dataset_screen_by_id = {
        str(row.get("dataset_id")): row
        for row in dataset_rows
        if isinstance(row, dict) and row.get("dataset_id")
    }
    unlit_categories = {
        str(row.get("category", "")).upper()
        for row in dataset_screen_by_id.values()
        if row.get("category")
    }

    screened = []
    for record in clue_records:
        clue = normalize_os_clue_record(record)
        text_blob = " ".join(
            str(clue.get(key) or "")
            for key in ("pyramid", "dataset_id", "language", "max_trade", "code_clue", "reason")
        )
        dataset_id = clue["dataset_id"] or infer_dataset_id_from_text(text_blob, dataset_screen_by_id)
        dataset_row = dataset_screen_by_id.get(dataset_id or "")
        blockers: list[str] = []

        if clue["self_corr"] is None or clue["prod_corr"] is None:
            blockers.append("missing_corr_metrics")
        else:
            if clue["self_corr"] >= max_self_corr:
                blockers.append("self_corr_not_low_enough")
            if clue["prod_corr"] < min_prod_corr:
                blockers.append("prod_corr_below_floor")

        if clue["source_marks_usable"] is False:
            blockers.append("source_marks_unusable")

        pyramid = str(clue.get("pyramid") or "").upper()
        pyramid_tokens = {token.strip() for token in pyramid.replace("/", " ").split() if token.strip()}
        if pyramid_tokens and unlit_categories and not (pyramid_tokens & unlit_categories):
            blockers.append("not_in_unlit_dataset_screen")
        if any(token in {"PV", "PRICEVOLUME"} for token in pyramid_tokens) or " return" in text_blob.lower():
            blockers.append("pv_or_returns_related")
        if is_pv_dataset(str(dataset_id or "")):
            blockers.append("pv_dataset_forbidden")
        if any(is_pv_field(str(field.get("id", "")), str(field.get("description", ""))) for field in clue["fields"]):
            blockers.append("pv_fields_forbidden")
        clue_roles = {str(field.get("id", "")): field_role(field) for field in clue["fields"] if field.get("id")}
        non_matrix_fields = [
            field_id
            for field_id, role in clue_roles.items()
            if role.startswith("forbidden_")
        ]
        if non_matrix_fields or "vector" in text_blob.lower():
            blockers.append("non_matrix_or_vector_evidence")

        economic_fields = [
            field_id
            for field_id, role in clue_roles.items()
            if role == "economic_matrix"
        ]
        if len(economic_fields) > MAX_ECONOMIC_FIELDS:
            blockers.append("too_many_economic_fields")

        language = str(clue.get("language") or "").upper()
        if language and language != "PYTHON":
            blockers.append("non_python_evidence_only")
        elif "fastexpr" in text_blob.lower() or "fast expression" in text_blob.lower():
            blockers.append("non_python_evidence_only")

        max_trade = str(clue.get("max_trade") or "").upper()
        if max_trade == "OFF":
            blockers.append("max_trade_off")

        if dataset_row:
            if dataset_row.get("decision") == "reject_or_hold":
                blockers.append("dataset_screen_reject_or_hold")
            if "review_blocks_simulation" in (dataset_row.get("blockers") or []):
                blockers.append("prior_review_blocks_simulation")
            if dataset_row.get("pv_fields"):
                blockers.append("dataset_screen_has_pv_fields")
            if dataset_row.get("non_matrix_fields"):
                blockers.append("dataset_screen_has_forbidden_field_types")
        elif dataset_screen_by_id:
            blockers.append("no_matching_unlit_dataset")

        blockers = sorted(set(blockers))
        passes_corr_pattern = not any(
            blocker in blockers
            for blocker in ("missing_corr_metrics", "self_corr_not_low_enough", "prod_corr_below_floor")
        )
        usable = passes_corr_pattern and not blockers
        screened.append(
            {
                "alpha_id": clue["alpha_id"],
                "self_corr": clue["self_corr"],
                "prod_corr": clue["prod_corr"],
                "passes_low_self_high_prod_pattern": passes_corr_pattern,
                "pyramid": clue["pyramid"],
                "dataset_id": dataset_id,
                "dataset_screen_decision": dataset_row.get("decision") if dataset_row else None,
                "dataset_screen_blockers": dataset_row.get("blockers") if dataset_row else None,
                "language": clue["language"],
                "max_trade": clue["max_trade"],
                "code_clue": clue["code_clue"],
                "reason": clue["reason"],
                "economic_fields": economic_fields,
                "non_matrix_fields": non_matrix_fields,
                "blockers": blockers,
                "usable_legal_unlit_python_candidate": usable,
                "use_as": "candidate_preflight" if usable else "diagnostic_clue_only",
            }
        )

    selected = [row for row in screened if row["passes_low_self_high_prod_pattern"]]
    usable_rows = [row for row in screened if row["usable_legal_unlit_python_candidate"]]
    blocker_counts: dict[str, int] = {}
    for row in screened:
        for blocker in row["blockers"]:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
    return {
        "ts": utc_ts(),
        "target": target,
        "forum_source": {
            "article_id": forum_article_id,
            "theme": "Python alpha decorrelation/performance improvement from existing alpha clues",
        },
        "thresholds": {
            "self_corr_lt": max_self_corr,
            "prod_corr_gte": min_prod_corr,
        },
            "constraints": {
                "max_economic_fields": MAX_ECONOMIC_FIELDS,
                "forbid_pv_fields": True,
                "matrix_only": False,
                "field_policy": "MATRIX economic fields; verified MATRIX/GROUP auxiliary group keys; VECTOR forbidden",
                "python_only": True,
                "max_trade": "ON",
            },
        "dataset_screen_summary": dataset_screen.get("summary", {}) if isinstance(dataset_screen, dict) else {},
        "clues_screened": len(screened),
        "low_self_high_prod_clues": len(selected),
        "usable_legal_unlit_python_candidate_found": bool(usable_rows),
        "correlation_gates_run": False,
        "summary": {
            "usable_legal_unlit_python_candidates": len(usable_rows),
            "diagnostic_only": sum(1 for row in screened if not row["usable_legal_unlit_python_candidate"]),
            "blocker_counts": blocker_counts,
        },
        "decision": {
            "simulation_allowed_from_clues": bool(usable_rows),
            "reason": (
                "At least one clue passes the low-Self/high-Prod pattern and all active legality screens."
                if usable_rows
                else "No clue is both a low-Self/high-Prod pattern and a legal unlit no-PV MATRIX Python candidate under the active target."
            ),
            "correlation_gates_run": False,
        },
        "clues": screened,
    }


def load_field_meta_specs(specs: list[str] | None) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for spec in specs or []:
        if "=" not in spec:
            data = json.loads(Path(spec).read_text())
            if not isinstance(data, dict):
                raise SystemExit("--field-meta-spec without '=' must point to a dataset_id -> metadata object")
            for dataset_id, records in data.items():
                if isinstance(records, dict) and ("mcp_fields" in records or "results" in records):
                    items = records.get("mcp_fields", records.get("results", []))
                elif isinstance(records, list):
                    items = records
                else:
                    items = []
                out[str(dataset_id)] = {str(item["id"]): item for item in items if isinstance(item, dict) and item.get("id")}
            continue
        dataset_id, path = spec.split("=", 1)
        out[dataset_id] = load_field_meta(path)
    return out


def screen_datasets(
    dataset_records: list[dict[str, Any]],
    field_meta_by_dataset: dict[str, dict[str, dict[str, Any]]],
    evolution_review: dict[str, Any],
    region: str,
    universe: str,
    delay: int,
    policy_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = expected_target(region, universe, delay)
    policy_overrides = policy_overrides or {}
    rows = []
    for dataset in dataset_records:
        dataset_id = str(dataset.get("id") or dataset.get("dataset_id") or "")
        if not dataset_id:
            continue
        fields = list(field_meta_by_dataset.get(dataset_id, {}).values())
        roles = {str(f.get("id", "")): field_role(f) for f in fields if f.get("id")}
        matrix_fields = [f for f in fields if field_role(f) == "economic_matrix"]
        auxiliary_group_fields = [f for f in fields if field_role(f) == "auxiliary_group"]
        non_matrix_fields = [field_id for field_id, role in roles.items() if role.startswith("forbidden_")]
        pv_fields = [
            f.get("id")
            for f in fields
            if is_pv_field(str(f.get("id", "")), str(f.get("description", "")))
        ]
        guidance = build_evolution_review_guidance(dataset_id, evolution_review)
        if dataset_id in policy_overrides:
            override = dict(policy_overrides[dataset_id])
            override.setdefault("available", True)
            override.setdefault("dataset_id", dataset_id)
            guidance = override
        candidate_fields = sorted(
            [
                f
                for f in matrix_fields
                if not is_pv_field(str(f.get("id", "")), str(f.get("description", "")))
                and field_role(f) == "economic_matrix"
            ],
            key=lambda f: (-int(f.get("alphaCount") or 0), -int(f.get("userCount") or 0), str(f.get("id", ""))),
        )[:MAX_ECONOMIC_FIELDS]

        blockers = []
        if is_pv_dataset(dataset_id):
            blockers.append("pv_dataset_forbidden")
        if not matrix_fields:
            blockers.append("no_matrix_economic_fields")
        if pv_fields:
            blockers.append("pv_fields_present")
        if non_matrix_fields:
            blockers.append("non_matrix_fields_present")
        if guidance.get("simulation_allowed") is False:
            blockers.append("review_blocks_simulation")

        if blockers:
            decision = "reject_or_hold"
        elif guidance.get("simulation_allowed") == "labs_gated":
            decision = "labs_required_before_simulation"
        elif guidance.get("simulation_allowed") == "requires_mechanism_preflight":
            decision = "mechanism_preflight_required"
        else:
            decision = "candidate_preflight_required"

        rows.append(
            {
                "dataset_id": dataset_id,
                "name": dataset.get("name") or dataset.get("dataset_name"),
                "category": dataset.get("category"),
                "dataset_field_count": dataset.get("fieldCount"),
                "matrix_field_count_loaded": len(matrix_fields),
                "auxiliary_group_field_count_loaded": len(auxiliary_group_fields),
                "candidate_field_limit": MAX_ECONOMIC_FIELDS,
                "candidate_fields_high_quality_live_popularity": [f.get("id") for f in candidate_fields],
                "candidate_fields_low_crowding": [f.get("id") for f in candidate_fields],
                "auxiliary_group_fields": [f.get("id") for f in auxiliary_group_fields],
                "pv_fields": pv_fields,
                "non_matrix_fields": non_matrix_fields,
                "field_roles": roles,
                "review_guidance": guidance,
                "blockers": blockers,
                "decision": decision,
                "target": expected,
            }
        )

    decision_rank = {
        "candidate_preflight_required": 0,
        "mechanism_preflight_required": 1,
        "labs_required_before_simulation": 2,
        "reject_or_hold": 3,
    }
    rows.sort(
        key=lambda row: (
            decision_rank.get(row["decision"], 99),
            -int(row.get("matrix_field_count_loaded") or 0),
            str(row.get("dataset_id")),
        )
    )
    return {
        "ts": utc_ts(),
        "target": expected,
        "max_economic_fields": MAX_ECONOMIC_FIELDS,
        "forbid_pv_fields": True,
        "matrix_only": False,
        "field_policy": "MATRIX economic fields; verified MATRIX/GROUP auxiliary group keys; VECTOR forbidden",
        "datasets_screened": len(rows),
        "datasets": rows,
        "summary": {
            "candidate_preflight_required": sum(1 for row in rows if row["decision"] == "candidate_preflight_required"),
            "mechanism_preflight_required": sum(1 for row in rows if row["decision"] == "mechanism_preflight_required"),
            "labs_required_before_simulation": sum(1 for row in rows if row["decision"] == "labs_required_before_simulation"),
            "reject_or_hold": sum(1 for row in rows if row["decision"] == "reject_or_hold"),
        },
    }


def build_evolution_review_guidance(dataset_id: str, review: dict[str, Any]) -> dict[str, Any]:
    dataset_reviews = review.get("dataset_reviews", {}) if isinstance(review, dict) else {}
    dataset_review = dataset_reviews.get(dataset_id)
    if not dataset_review:
        return {
            "available": False,
            "dataset_id": dataset_id,
            "decision": "no_review_record",
            "simulation_allowed": "requires_mechanism_preflight",
            "reason": "No strict evolution-review record was found for this dataset.",
        }

    policy = str(dataset_review.get("mutation_policy", ""))
    best = dataset_review.get("best") or {}
    blocking_policy = policy.startswith(("PIVOT_MECHANISM", "RUNTIME_PIVOT", "RESEARCH_ONLY"))
    exploit_policy = policy.startswith(("EXPLOIT_SINGLE_AXIS", "WATCHLIST_ONLY"))
    if blocking_policy:
        decision = "hold_without_new_labs_mechanism"
        simulation_allowed = False
        reason = "Prior target-matched Python simulations exhausted or pivoted this mechanism."
    elif exploit_policy:
        decision = "labs_gated_single_axis_only"
        simulation_allowed = "labs_gated"
        reason = "Review allows only one-axis exploitation; Labs evidence should name the axis before simulation."
    else:
        decision = "mechanism_preflight_required"
        simulation_allowed = "requires_mechanism_preflight"
        reason = "Review does not provide a controlled exploitation policy."

    return {
        "available": True,
        "dataset_id": dataset_id,
        "review_timestamp_utc": review.get("timestamp_utc"),
        "filters": review.get("filters"),
        "simulation_count": dataset_review.get("simulation_count"),
        "mutation_policy": policy,
        "decision": decision,
        "simulation_allowed": simulation_allowed,
        "reason": reason,
        "best": {
            "alpha_id": best.get("alpha_id"),
            "source_file": best.get("source_file"),
            "sharpe": best.get("sharpe"),
            "fitness": best.get("fitness"),
            "robust_universe_sharpe": best.get("robust_universe_sharpe"),
            "risk_neutralized_sharpe": best.get("risk_neutralized_sharpe"),
            "two_year_sharpe": best.get("two_year_sharpe"),
            "turnover": best.get("turnover"),
            "margin": best.get("margin"),
            "classification": best.get("classification"),
        },
    }


def prepend_review_guidance_plan(
    plan: list[dict[str, Any]],
    guidance: dict[str, Any],
) -> list[dict[str, Any]]:
    if not guidance or not guidance.get("available"):
        return plan
    item = {
        "priority": 0,
        "candidate_family": "evolution_review_simulation_gate",
        "why": guidance.get("reason"),
        "review_decision": guidance.get("decision"),
        "mutation_policy": guidance.get("mutation_policy"),
        "best_prior_metrics": guidance.get("best"),
        "python_design": (
            "Do not spend another create_simulation call unless Labs raw diagnostics identify a new "
            "mechanism or the review policy allows a named one-axis repair."
        ),
        "stop_rule": "No SelfCorr, ProdCorr, or submission unless hard IS, latest-year, robust, and risk-neutral gates pass.",
    }
    return [item, *plan]


def prepend_constraint_gate_plan(
    plan: list[dict[str, Any]],
    constraint_checks: dict[str, Any],
) -> list[dict[str, Any]]:
    violations = constraint_checks.get("violations", []) if isinstance(constraint_checks, dict) else []
    if not violations:
        return plan
    target_check = constraint_checks.get("target_consistency", {}) if isinstance(constraint_checks, dict) else {}
    item = {
        "priority": 0,
        "candidate_family": "constraint_gate_do_not_simulate",
        "why": "The Labs artifact violates active target or field constraints and is diagnostic-only.",
        "violations": violations,
        "target_consistency": target_check,
        "python_design": (
            "Do not use this artifact to justify create_simulation. Regenerate or ingest Labs diagnostics "
            "with the exact active target and only allowed MATRIX fields before alpha optimization."
        ),
        "stop_rule": "No SelfCorr, ProdCorr, property setting, or submission from diagnostic-only Labs evidence.",
    }
    return [item, *plan]


def command_run_labs(args: argparse.Namespace) -> None:
    np, pd = require_pandas_numpy()
    try:
        from brain import Brain
    except Exception as exc:
        raise SystemExit("run-labs must be executed inside Brain Labs where `from brain import Brain` is available.") from exc

    try:
        brain = Brain(region=args.region, delay=args.delay, universe=args.universe)
    except TypeError:
        brain = Brain()
    frames = {}
    field_meta = {}
    for field_id in args.fields:
        data_field = None
        errors = []
        try:
            data_field = brain.get_data_field(field_id)
            df = brain.get_data_frame(data_field)
        except Exception as exc:
            errors.append(f"get_data_field/get_data_frame(data_field): {exc}")
            df = None
            attempts = [
                {
                    "field_id": field_id,
                    "dataset_id": args.dataset_id,
                    "region": args.region,
                    "universe": args.universe,
                    "delay": args.delay,
                },
                {
                    "field_id": field_id,
                    "region": args.region,
                    "universe": args.universe,
                    "delay": args.delay,
                },
            ]
            for kwargs in attempts:
                try:
                    df = brain.get_data_frame(**kwargs)
                    break
                except Exception as inner_exc:
                    errors.append(f"get_data_frame({kwargs}): {inner_exc}")
            if df is None:
                raise RuntimeError(f"Could not load {field_id} in Brain Labs: " + " | ".join(errors))
        field_meta[field_id] = {
            "id": getattr(data_field, "id", field_id),
            "type": getattr(data_field, "type", "MATRIX"),
            "description": getattr(data_field, "description", ""),
            "load_errors": errors,
        }
        frames[field_id] = df
    result = analyze_frames(frames, field_meta, args.dataset_id, args.region, args.universe, args.delay)
    write_json(args.output, result)


def command_run_csv(args: argparse.Namespace) -> None:
    _np, pd = require_pandas_numpy()
    frames = {}
    for spec in args.csv:
        if "=" not in spec:
            raise SystemExit("--csv must use field_id=/path/file.csv")
        field_id, file_path = spec.split("=", 1)
        frames[field_id] = pd.read_csv(file_path, index_col=0, parse_dates=True)
    result = analyze_frames(frames, load_field_meta(args.field_meta), args.dataset_id, args.region, args.universe, args.delay)
    write_json(args.output, result)


def command_demo(args: argparse.Namespace) -> None:
    np, pd = require_pandas_numpy()
    rng = np.random.default_rng(args.seed)
    dates = pd.bdate_range("2016-01-04", periods=args.days)
    instruments = [f"stock_{i:04d}" for i in range(args.instruments)]
    base = rng.beta(2.0, 2.5, size=(args.days, args.instruments)).astype(float)
    regime = np.zeros_like(base)
    for i in range(args.instruments):
        drift = rng.normal(0.0, 0.01, size=args.days).cumsum()
        regime[:, i] = np.clip(base[:, i] * 0.7 + 0.3 * (drift - drift.min()) / (drift.max() - drift.min() + 1e-9), 0, 1)
    mask = rng.random(regime.shape) < 0.13
    regime[mask] = np.nan
    mktcap = np.exp(rng.normal(10.0, 1.2, size=(args.days, args.instruments)))
    mktcap[mask] = np.nan
    frames = {
        "imb5_score": pd.DataFrame(regime, index=dates, columns=instruments),
        "imb5_mktcap": pd.DataFrame(mktcap, index=dates, columns=instruments),
    }
    meta = {
        "imb5_score": {
            "id": "imb5_score",
            "description": "SHIELD-OIL composite score (0-1) indicating resilience/advantage in oil shock regimes",
        },
        "imb5_mktcap": {
            "id": "imb5_mktcap",
            "description": "Market capitalization of security in regional currency units",
        },
    }
    result = analyze_frames(frames, meta, "imbalance5", args.region, args.universe, args.delay)
    write_json(args.output, result)


def command_ingest(args: argparse.Namespace) -> None:
    labs = json.loads(Path(args.labs_json).read_text())
    meta = load_field_meta(args.field_meta)
    evolution_review = load_evolution_review(args.evolution_review)
    mcp_fields = list(meta.values())
    dataset_id = args.dataset_id or labs.get("dataset_id", "unknown")
    ts = args.ts or utc_ts()
    forum_sources = merge_forum_sources(labs.get("forum_sources", DEFAULT_FORUM_SOURCES))
    expected = expected_target(args.region, args.universe, args.delay)
    observed_target = labs.get("target", expected)
    artifact = {
        "ts": ts,
        "target": observed_target,
        "constraint_checks": build_constraint_checks(dataset_id, mcp_fields, observed_target, expected),
        "dataset_id": dataset_id,
        "mcp_fields": mcp_fields,
        "labs_metrics": labs.get("labs_metrics", {}),
        "field_classification": labs.get("field_classification", {}),
        "correlation_pruning": labs.get("correlation_pruning", {}),
        "accepted_mechanisms": labs.get("accepted_mechanisms", []),
        "rejected_fields_or_mechanisms": labs.get("rejected_fields_or_mechanisms", []),
        "preprocessing_actions": labs.get("preprocessing_actions", []),
        "alpha_optimization_plan": labs.get("alpha_optimization_plan", []),
        "evolution_review_guidance": build_evolution_review_guidance(dataset_id, evolution_review),
        "python_alpha_implications": labs.get("python_alpha_implications", []),
        "forum_sources": forum_sources,
        "runtime_validation": {
            "labs_json": str(Path(args.labs_json)),
            "field_meta_json": str(Path(args.field_meta)) if args.field_meta else None,
            "evolution_review_json": str(Path(args.evolution_review)) if args.evolution_review else None,
            "mode": args.mode,
        },
    }
    labs_direct_market_fields = [
        field_id
        for field_id in artifact["labs_metrics"]
        if is_direct_market_data_field(str(field_id), str(meta.get(str(field_id), {}).get("description", "")))
    ]
    if labs_direct_market_fields:
        checks = artifact["constraint_checks"]
        existing_direct = set(checks.get("direct_market_data_fields", []))
        checks["direct_market_data_fields"] = sorted(existing_direct | set(labs_direct_market_fields))
        if "direct_market_data_fields_forbidden" not in checks["violations"]:
            checks["violations"].append("direct_market_data_fields_forbidden")
        checks["artifact_decision_use"] = "diagnostic_only"
        artifact["python_alpha_implications"] = [
            item
            for item in artifact["python_alpha_implications"]
            if not any(field_id in str(item) for field_id in labs_direct_market_fields)
            and not any(token in str(item).lower() for token in ("market cap", "market-cap", "mktcap"))
        ]
        artifact["python_alpha_implications"].append(
            "Direct market-data fields are excluded before alpha design under the active constraint: "
            + ", ".join(labs_direct_market_fields)
        )
    allowed_mechanisms = []
    for rec in artifact["accepted_mechanisms"]:
        if not isinstance(rec, dict):
            continue
        field_id = str(rec.get("field", ""))
        field_meta_record = meta.get(field_id, {})
        description = str(field_meta_record.get("description", rec.get("reason", "")))
        if is_pv_field(field_id, description) or is_direct_market_data_field(field_id, description):
            rejected = dict(rec)
            rejected["decision"] = "reject"
            rejected["reason"] = (
                f"{field_id} is excluded by the active no-PV/no-direct-market-data constraint; "
                + str(rec.get("reason", ""))
            )
            artifact["rejected_fields_or_mechanisms"].append(rejected)
        else:
            allowed_mechanisms.append(rec)
    artifact["accepted_mechanisms"] = allowed_mechanisms
    guidance_metrics = normalize_metrics_for_guidance(artifact["labs_metrics"])
    if not artifact["preprocessing_actions"] and guidance_metrics:
        artifact["preprocessing_actions"] = build_preprocessing_actions(
            guidance_metrics,
            artifact["field_classification"],
            meta,
        )
    if not artifact["alpha_optimization_plan"] and guidance_metrics:
        artifact["alpha_optimization_plan"] = build_alpha_optimization_plan(
            dataset_id,
            guidance_metrics,
            artifact["field_classification"],
            artifact["correlation_pruning"],
            meta,
        )
    if artifact["evolution_review_guidance"].get("available"):
        artifact["alpha_optimization_plan"] = prepend_review_guidance_plan(
            artifact["alpha_optimization_plan"],
            artifact["evolution_review_guidance"],
        )
    if artifact["constraint_checks"]["violations"]:
        artifact["alpha_optimization_plan"] = prepend_constraint_gate_plan(
            artifact["alpha_optimization_plan"],
            artifact["constraint_checks"],
        )
        artifact["python_alpha_implications"].append(
            "This Labs artifact is diagnostic-only for production decisions until constraint violations are resolved: "
            + ", ".join(artifact["constraint_checks"]["violations"])
        )
    output = Path(args.output or f"tracking/runs/{ts}_labs_data_analysis_{dataset_id}.json")
    write_json(output, artifact)
    if args.markdown:
        write_markdown(Path(args.markdown), artifact)


def command_screen_datasets(args: argparse.Namespace) -> None:
    datasets = load_dataset_records(args.datasets_json)
    field_meta_by_dataset = load_field_meta_specs(args.field_meta_spec)
    evolution_review = load_evolution_review(args.evolution_review)
    policy_overrides = json.loads(Path(args.policy_override_json).read_text()) if args.policy_override_json else {}
    artifact = screen_datasets(
        datasets,
        field_meta_by_dataset,
        evolution_review,
        args.region,
        args.universe,
        args.delay,
        policy_overrides,
    )
    output = Path(args.output)
    write_json(output, artifact)
    if args.markdown:
        write_screen_markdown(Path(args.markdown), artifact)


def command_screen_os_clues(args: argparse.Namespace) -> None:
    clues, source = load_os_clue_records(args.clues_json)
    dataset_screen = load_dataset_screen(args.dataset_screen_json)
    artifact = screen_os_clues(
        clues,
        dataset_screen,
        args.region,
        args.universe,
        args.delay,
        args.max_self_corr,
        args.min_prod_corr,
        args.forum_article_id,
    )
    artifact["source"] = {
        "clues_json": str(Path(args.clues_json)),
        "dataset_screen_json": str(Path(args.dataset_screen_json)) if args.dataset_screen_json else None,
        "source_timestamp_utc": source.get("timestamp_utc") if isinstance(source, dict) else None,
        "source_objective": source.get("objective") if isinstance(source, dict) else None,
    }
    output = Path(args.output)
    write_json(output, artifact)
    if args.markdown:
        write_os_clue_screen_markdown(Path(args.markdown), artifact)


def command_emit_labs_script(args: argparse.Namespace) -> None:
    output = Path(args.output)
    fields_repr = repr(args.fields)
    body = Path(__file__).read_text()
    body = body.rsplit('if __name__ == "__main__":\n    main()\n', 1)[0].rstrip()
    invocation = f"""

# Brain Labs entry point generated by emit-labs-script.
if __name__ == "__main__":
    main([
        "run-labs",
        "--dataset-id", {args.dataset_id!r},
        "--region", {args.region!r},
        "--universe", {args.universe!r},
        "--delay", {str(args.delay)!r},
        "--output", {args.labs_output!r},
        *{fields_repr},
    ])
"""
    script = body + invocation
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(script)
    print(output)


def command_emit_notebook_exec(args: argparse.Namespace) -> None:
    script = Path(args.script).read_text()
    line = f"exec({script!r})"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(line + "\n")
        print(output)
    else:
        print(line)


def command_emit_summary_cell(args: argparse.Namespace) -> None:
    code = f"""
import json
with open({args.labs_output!r}) as fh:
    d = json.load(fh)
if "labs_metrics" in d:
    s = {{
        "target": d.get("target"),
        "dataset_id": d.get("dataset_id"),
        "constraint_checks": d.get("constraint_checks"),
        "fields": {{}},
        "correlations": {{}},
        "evolution_review_guidance": d.get("evolution_review_guidance"),
        "accepted_mechanisms": [
            {{
                "field": item.get("field"),
                "decision": item.get("decision"),
                "extractor_family": item.get("extractor_family"),
            }}
            for item in d.get("accepted_mechanisms", [])
        ],
        "optimization_plan": [
            {{
                "priority": item.get("priority"),
                "candidate_family": item.get("candidate_family"),
                "field": item.get("field"),
            }}
            for item in d.get("alpha_optimization_plan", [])
        ],
    }}
    for field, metrics in d.get("labs_metrics", {{}}).items():
        finite = metrics.get("finite_summary", {{}})
        s["fields"][field] = {{
            "classification": d.get("field_classification", {{}}).get(field),
            "finite_ratio": round(metrics.get("finite_ratio") or 0.0, 4),
            "zero_ratio": round(metrics.get("zero_ratio") or 0.0, 4),
            "coverage_mean": round(metrics.get("coverage_by_date", {{}}).get("mean") or 0.0, 4),
            "change_rate_mean": round(metrics.get("change_rate_by_date", {{}}).get("mean") or 0.0, 4),
            "p05_p50_p95": [
                round(finite.get(k) or 0.0, 4)
                for k in ["p05", "p50", "p95"]
            ],
        }}
    for pair, corr in d.get("correlation_pruning", {{}}).items():
        xs = corr.get("mean_cross_section_corr_by_date", {{}})
        ts = corr.get("mean_time_series_corr_by_instrument", {{}})
        s["correlations"][pair] = {{
            "xs_mean": round(xs.get("mean") or 0.0, 4),
            "ts_mean": round(ts.get("mean") or 0.0, 4),
            "aligned_dates": corr.get("aligned_dates"),
            "aligned_instruments": corr.get("aligned_instruments"),
        }}
else:
    s = {{
        "target": d.get("target"),
        "shape": d.get("shape"),
        "date_range": d.get("date_range"),
        "coverage_cell": round(d.get("coverage", {{}}).get("cell_finite_ratio", 0.0), 4),
        "coverage_date_p05": round(d.get("coverage", {{}}).get("by_date", {{}}).get("p05", 0.0), 4),
        "score_p05_p50_p95": [
            round(d.get("score_distribution", {{}}).get(k, 0.0), 4)
            for k in ["p05", "p50", "p95"]
        ],
        "score_mcap_corr_mean": round(d.get("score_mcap_xs_corr", {{}}).get("mean", 0.0), 4),
        "years": [
            {{
                k: y.get(k)
                for k in ["year", "coverage", "change_rate", "xs_std_mean", "mcap_xs_corr_mean"]
            }}
            for y in d.get("year_diagnostics", [])
        ],
        "extractors": {{}},
    }}
    for name, extractor in d.get("extractors", {{}}).items():
        c = extractor.get("concentration", {{}})
        s["extractors"][name] = {{
            "top10_p50": round(c.get("top10_abs_share", {{}}).get("p50", 0.0), 4),
            "effn_p50": round(c.get("effective_n", {{}}).get("p50", 0.0), 1),
            "hhi_p50": round(c.get("hhi", {{}}).get("p50", 0.0), 4),
            "mcap_corr_mean": round(extractor.get("mcap_xs_corr", {{}}).get("mean", 0.0), 4),
        }}
print(json.dumps(s, indent=2))
"""
    line = f"exec({dedent(code).strip()!r})"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(line + "\n")
        print(output)
    else:
        print(line)


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))
    print(path)


def write_markdown(path: Path, artifact: dict[str, Any]) -> None:
    lines = [
        f"# Labs Data Analysis: {artifact.get('dataset_id')}",
        "",
        f"- Timestamp: `{artifact.get('ts')}`",
        f"- Target: `{artifact.get('target')}`",
        "",
        "## Constraint Checks",
    ]
    checks = artifact.get("constraint_checks", {})
    if checks:
        lines.append(f"- Artifact use: `{checks.get('artifact_decision_use')}`")
        lines.append(f"- Max economic fields: `{checks.get('max_economic_fields')}`")
        lines.append(f"- Economic fields: `{checks.get('economic_fields', [])}`")
        lines.append(f"- Auxiliary group-key fields: `{checks.get('auxiliary_group_key_fields', [])}`")
        lines.append(f"- PV forbidden: `{checks.get('forbid_pv_fields')}`")
        lines.append(f"- Field policy: `{checks.get('field_policy')}`")
        lines.append(f"- Violations: `{checks.get('violations', [])}`")
        if checks.get("direct_market_data_fields"):
            lines.append(f"- Direct market-data fields rejected: `{checks.get('direct_market_data_fields')}`")
        target_check = checks.get("target_consistency", {})
        lines.append(f"- Target matched: `{target_check.get('matched')}`")
        if target_check and not target_check.get("matched"):
            lines.append(f"- Expected target: `{target_check.get('expected_target')}`")
    lines.extend([
        "",
        "## Field Classification",
    ])
    for field, cls in artifact.get("field_classification", {}).items():
        lines.append(f"- `{field}`: `{cls}`")
    lines.extend(["", "## Accepted / Diagnostic Mechanisms"])
    for rec in artifact.get("accepted_mechanisms", []):
        lines.append(f"- `{rec.get('field')}`: `{rec.get('decision')}` via `{rec.get('extractor_family')}` - {rec.get('reason')}")
    lines.extend(["", "## Preprocessing Actions"])
    for item in artifact.get("preprocessing_actions", []):
        lines.append(
            f"- `{item.get('field')}`: `{item.get('preferred_transform')}` - {item.get('implementation_hint')}"
        )
    guidance = artifact.get("evolution_review_guidance", {})
    if guidance:
        lines.extend(["", "## Evolution Review Guidance"])
        lines.append(f"- Available: `{guidance.get('available')}`")
        lines.append(f"- Decision: `{guidance.get('decision')}`")
        lines.append(f"- Simulation allowed: `{guidance.get('simulation_allowed')}`")
        lines.append(f"- Mutation policy: `{guidance.get('mutation_policy')}`")
        if guidance.get("best"):
            lines.append(f"- Best prior metrics: `{guidance.get('best')}`")
    lines.extend(["", "## Alpha Optimization Plan"])
    for item in artifact.get("alpha_optimization_plan", []):
        lines.append(f"- P{item.get('priority')} `{item.get('candidate_family')}`: {item.get('why')}")
    lines.extend(["", "## Python Alpha Implications"])
    for item in artifact.get("python_alpha_implications", []):
        lines.append(f"- {item}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print(path)


def write_screen_markdown(path: Path, artifact: dict[str, Any]) -> None:
    lines = [
        "# Dataset Screen",
        "",
        f"- Timestamp: `{artifact.get('ts')}`",
        f"- Target: `{artifact.get('target')}`",
        f"- Max economic fields: `{artifact.get('max_economic_fields')}`",
        f"- PV forbidden: `{artifact.get('forbid_pv_fields')}`",
        f"- Field policy: `{artifact.get('field_policy')}`",
        f"- Summary: `{artifact.get('summary')}`",
        "",
        "| Dataset | Decision | Matrix Fields | Candidate Fields | Review | Blockers |",
        "|---|---|---:|---|---|---|",
    ]
    for row in artifact.get("datasets", []):
        guidance = row.get("review_guidance", {})
        lines.append(
            "| {dataset} | {decision} | {count} | {fields} | {review} | {blockers} |".format(
                dataset=row.get("dataset_id"),
                decision=row.get("decision"),
                count=row.get("matrix_field_count_loaded"),
                fields=", ".join(row.get("candidate_fields_high_quality_live_popularity") or row.get("candidate_fields_low_crowding") or []),
                review=guidance.get("decision"),
                blockers=", ".join(row.get("blockers") or []),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print(path)


def write_os_clue_screen_markdown(path: Path, artifact: dict[str, Any]) -> None:
    lines = [
        "# OS Correlation Clue Screen",
        "",
        f"- Timestamp: `{artifact.get('ts')}`",
        f"- Target: `{artifact.get('target')}`",
        f"- Thresholds: `{artifact.get('thresholds')}`",
        f"- Constraints: `{artifact.get('constraints')}`",
        f"- Correlation gates run: `{artifact.get('correlation_gates_run')}`",
        f"- Usable legal unlit Python candidate found: `{artifact.get('usable_legal_unlit_python_candidate_found')}`",
        f"- Summary: `{artifact.get('summary')}`",
        "",
        "## Decision",
        "",
        f"- Simulation allowed from clues: `{artifact.get('decision', {}).get('simulation_allowed_from_clues')}`",
        f"- Reason: {artifact.get('decision', {}).get('reason')}",
        "",
        "| Alpha | SelfCorr | ProdCorr | Pattern | Use | Dataset | Blockers |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for row in artifact.get("clues", []):
        lines.append(
            "| {alpha} | {self_corr} | {prod_corr} | {pattern} | {use_as} | {dataset} | {blockers} |".format(
                alpha=row.get("alpha_id"),
                self_corr=row.get("self_corr"),
                prod_corr=row.get("prod_corr"),
                pattern=row.get("passes_low_self_high_prod_pattern"),
                use_as=row.get("use_as"),
                dataset=row.get("dataset_id"),
                blockers=", ".join(row.get("blockers") or []),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    emit = sub.add_parser("emit-labs-script", help="Generate a pasteable Brain Labs script")
    add_common(emit)
    emit.add_argument("--output", required=True)
    emit.add_argument("--labs-output", default="labs_data_analysis_output.json")
    emit.add_argument("fields", nargs="+")
    emit.set_defaults(func=command_emit_labs_script)

    notebook_exec = sub.add_parser(
        "emit-notebook-exec",
        help="Wrap a Labs script as one exec(...) line for remote Jupyter cells",
    )
    notebook_exec.add_argument("--script", required=True)
    notebook_exec.add_argument("--output")
    notebook_exec.set_defaults(func=command_emit_notebook_exec)

    summary_cell = sub.add_parser(
        "emit-summary-cell",
        help="Generate one exec(...) line that prints a compact Labs JSON summary",
    )
    summary_cell.add_argument("--labs-output", required=True)
    summary_cell.add_argument("--output")
    summary_cell.set_defaults(func=command_emit_summary_cell)

    run_labs = sub.add_parser("run-labs", help="Run inside Brain Labs using the brain package")
    add_common(run_labs)
    run_labs.add_argument("--output", required=True)
    run_labs.add_argument("fields", nargs="+")
    run_labs.set_defaults(func=command_run_labs)

    run_csv = sub.add_parser("run-csv", help="Analyze local CSV dataframes")
    add_common(run_csv)
    run_csv.add_argument("--field-meta")
    run_csv.add_argument("--output", required=True)
    run_csv.add_argument("--csv", action="append", required=True, help="field_id=/path/file.csv")
    run_csv.set_defaults(func=command_run_csv)

    demo = sub.add_parser("demo", help="Run synthetic imbalance5-like end-to-end metrics")
    add_common(demo)
    demo.add_argument("--output", required=True)
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--days", type=int, default=260)
    demo.add_argument("--instruments", type=int, default=300)
    demo.set_defaults(func=command_demo)

    ingest = sub.add_parser("ingest", help="Merge Labs JSON and MCP metadata into tracking artifact")
    add_common(ingest)
    ingest.add_argument("--labs-json", required=True)
    ingest.add_argument("--field-meta")
    ingest.add_argument("--output")
    ingest.add_argument("--markdown")
    ingest.add_argument("--evolution-review")
    ingest.add_argument("--ts")
    ingest.add_argument("--mode", default="labs_json_ingest")
    ingest.set_defaults(func=command_ingest)

    screen = sub.add_parser("screen-datasets", help="Screen datasets against field metadata, constraints, and evolution review")
    add_common(screen)
    screen.add_argument("--datasets-json", required=True)
    screen.add_argument("--field-meta-spec", action="append", help="dataset_id=/path/field_meta.json")
    screen.add_argument("--evolution-review")
    screen.add_argument("--policy-override-json")
    screen.add_argument("--output", required=True)
    screen.add_argument("--markdown")
    screen.set_defaults(func=command_screen_datasets)

    clue_screen = sub.add_parser(
        "screen-os-clues",
        help="Screen low-SelfCorr/high-ProdCorr OS clues against active Python legality constraints",
    )
    add_common(clue_screen)
    clue_screen.add_argument("--clues-json", required=True)
    clue_screen.add_argument("--dataset-screen-json")
    clue_screen.add_argument("--max-self-corr", type=float, default=0.30)
    clue_screen.add_argument("--min-prod-corr", type=float, default=0.64)
    clue_screen.add_argument("--forum-article-id", default="40736166342679")
    clue_screen.add_argument("--output", required=True)
    clue_screen.add_argument("--markdown")
    clue_screen.set_defaults(func=command_screen_os_clues)
    return parser


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-id", default="imbalance5")
    parser.add_argument("--region", default="USA")
    parser.add_argument("--universe", default="TOP3000")
    parser.add_argument("--delay", type=int, default=1)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

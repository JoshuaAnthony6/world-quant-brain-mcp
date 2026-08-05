"""Candidate-pool management for BRAIN regular alphas.

WHY THIS EXISTS
---------------
BRAIN accepts at most a handful of regular-alpha submissions per day (4 for RA),
but a research session can produce many more qualified alphas than that. Those
alphas have to wait somewhere, and while they wait two things are true:

1. A pyramid is "covered" by the alphas you *will* submit, not only by the ones
   already submitted. Planning needs submitted + pooled counted together.
2. Submitting one alpha **changes the production correlation of every other
   candidate**, because the newly submitted alpha joins the pool that production
   correlation is measured against.

Point 2 is the whole reason this module is not just a list. The invariant is:

    projected_prod_corr(B | submit S)
        = max( prod_corr_now(B),  max over A in S of |corr(A, B)| )

and likewise for self correlation (different threshold). So "guarantee that
submitting a candidate never pushes another candidate past 0.7" is equivalent to
"every pair inside the pool is already below 0.7" — an invariant that can be
enforced at admission time, when it is still cheap to act on, rather than
discovered on submission day when it is too late.

The pool therefore enforces, on every ``add``:
  * candidate's own production correlation      < prod_threshold  (default 0.70)
  * candidate's own self correlation            < self_threshold  (default 0.70)
  * |corr(candidate, every pool member)|        < prod_threshold  -- SAFETY
  * |corr(candidate, every pool member)|        < mutual_threshold-- DIVERSITY

Safety is a hard gate (violating it makes the pool self-defeating). Diversity is
a softer, user-chosen basket rule and can be relaxed with ``allow_diversity_fail``.

Correlations among your own alphas are computed locally from PnL, so admission
costs no BRAIN correlation slot. Only the candidate's own production correlation
touches the rate-limited endpoint, and it is cached in the entry.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Pyramid category order used for reporting. Mirrors BRAIN's category ids.
PYRAMID_ORDER: List[str] = [
    "pv", "risk", "earnings", "other", "option", "model", "fundamental",
    "institutions", "analyst", "shortinterest", "insiders", "socialmedia",
    "news", "sentiment", "macro", "imbalance", "broker",
]

PYRAMID_LABELS: Dict[str, str] = {
    "pv": "Price Volume", "risk": "Risk", "earnings": "Earnings",
    "other": "Other", "option": "Option", "model": "Model",
    "fundamental": "Fundamental", "institutions": "Institutions",
    "analyst": "Analyst", "shortinterest": "Short Interest",
    "insiders": "Insiders", "socialmedia": "Social Media", "news": "News",
    "sentiment": "Sentiment", "macro": "Macro", "imbalance": "Imbalance",
    "broker": "Broker",
}

DEFAULT_PROD_THRESHOLD = 0.70
DEFAULT_SELF_THRESHOLD = 0.70
DEFAULT_MUTUAL_THRESHOLD = 0.40
DEFAULT_PYRAMID_TARGET = 3
DEFAULT_DAILY_SUBMIT_CAP = 4

SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def pool_path() -> Path:
    """Resolve the on-disk pool file.

    Prefers CANDIDATE_POOL_FILE, then the directory of MCP_CONFIG_FILE (which is
    a docker volume in the shipped compose file, so the pool survives rebuilds),
    then a repo-local fallback.
    """
    explicit = os.environ.get("CANDIDATE_POOL_FILE")
    if explicit:
        return Path(explicit)
    cfg = os.environ.get("MCP_CONFIG_FILE")
    if cfg:
        return Path(cfg).parent / "candidate_pool.json"
    return Path(__file__).parent / "config" / "candidate_pool.json"


def load_pool() -> Dict[str, Any]:
    """Load the pool, returning an empty structure when absent or corrupt."""
    path = pool_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        # A corrupt pool must not take the server down; surface an empty pool
        # and leave the bad file in place for inspection.
        return {"schema_version": SCHEMA_VERSION, "entries": {}, "load_error": str(path)}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    data.setdefault("schema_version", SCHEMA_VERSION)
    return data


def save_pool(pool: Dict[str, Any]) -> None:
    """Atomically persist the pool."""
    path = pool_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pool["updated_at"] = _now()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".candidate_pool.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(pool, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Alpha metadata extraction
# --------------------------------------------------------------------------- #

def _num(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _normalize_details(details: Dict[str, Any]) -> Dict[str, Any]:
    """Accept either a raw BRAIN alpha object or main.py's slimmed form.

    ``brain_client.get_alpha_details`` returns the RAW object, where the metrics
    live under ``is`` and pyramid / two-year-Sharpe / sub-universe-Sharpe are
    buried inside ``is.checks``. main.py's ``_slim_alpha`` already knows how to
    dig those out, so reuse it rather than duplicating (and drifting from) that
    logic. The import is deferred because main.py imports this module.
    """
    if not isinstance(details, dict):
        return {}
    if "is" not in details and "regular" not in details:
        return details  # already slim
    try:
        from main import _slim_alpha  # noqa: PLC0415 - deferred to break the cycle
    except Exception:
        return details
    try:
        return _slim_alpha(details)
    except Exception:
        return details


def summarize_alpha(details: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a get_alpha_details payload to the fields the pool reasons about."""
    details = _normalize_details(details)
    settings = details.get("settings") or {}
    metrics = details.get("metrics") or details.get("is") or {}
    ra = details.get("ra") or {}
    pyr = details.get("pyramids") or {}
    pyr_list = pyr.get("list") or []

    pyramid = ra.get("pyramid_short")
    if not pyramid and pyr_list:
        # Names look like "GBR/D1/ANALYST".
        tail = str(pyr_list[0].get("name", "")).rsplit("/", 1)[-1]
        pyramid = tail.lower() or None

    multiplier = None
    if pyr_list:
        multiplier = _num(pyr_list[0].get("multiplier"))

    return {
        "alpha_id": details.get("id") or details.get("alpha_id"),
        "code": details.get("code"),
        "status": details.get("status"),
        "instrument_type": settings.get("instrumentType"),
        "region": settings.get("region"),
        "universe": settings.get("universe"),
        "delay": settings.get("delay"),
        "neutralization": settings.get("neutralization"),
        "decay": settings.get("decay"),
        "truncation": settings.get("truncation"),
        "max_trade": settings.get("maxTrade"),
        "pyramid": pyramid,
        "pyramid_multiplier": multiplier,
        "metrics": {
            "sharpe": _num(metrics.get("sharpe")),
            "fitness": _num(metrics.get("fitness")),
            "turnover": _num(metrics.get("turnover")),
            "returns": _num(metrics.get("returns")),
            "margin": _num(metrics.get("margin")),
            "drawdown": _num(metrics.get("drawdown")),
            "two_year_sharpe": _num(metrics.get("two_year_sharpe")),
            "sub_universe_sharpe": _num(metrics.get("sub_universe_sharpe")),
        },
        "failed_ra_count": ra.get("failed_ra_count"),
        "failed_ppa_count": ra.get("failed_ppa_count"),
    }


# --------------------------------------------------------------------------- #
# Correlation helpers
# --------------------------------------------------------------------------- #

async def pairwise_against_pool(
    client: Any,
    alpha_id: str,
    other_ids: Sequence[str],
    years: int = 4,
) -> Tuple[Dict[str, float], List[str]]:
    """|corr| of ``alpha_id`` against each id in ``other_ids``.

    Local PnL correlation; consumes no BRAIN correlation slot. Returns
    (mapping other_id -> abs correlation, list of ids whose PnL was unusable).
    """
    others = [o for o in dict.fromkeys(other_ids) if o and o != alpha_id]
    if not others:
        return {}, []

    result = await client.get_mutual_correlation(
        [alpha_id] + others, threshold=1.1, years=years
    )
    if result.get("error"):
        # Propagate as "unknown" rather than silently treating it as zero:
        # a missing correlation must never be read as "safe".
        return {}, list(others)

    matrix = result.get("matrix") or {}
    row = matrix.get(alpha_id) or {}
    out: Dict[str, float] = {}
    missing: List[str] = []
    for oid in others:
        val = row.get(oid)
        if val is None:
            missing.append(oid)
        else:
            out[oid] = abs(float(val))
    missing.extend([m for m in (result.get("missing_pnl") or []) if m in others and m not in missing])
    return out, missing


async def fetch_production_correlation(client: Any, alpha_id: str) -> Dict[str, Any]:
    """Production correlation for one alpha, normalised to {value, status}."""
    data = await client.check_correlation(alpha_id, "production", DEFAULT_PROD_THRESHOLD)
    checks = (data or {}).get("checks") or {}
    prod = checks.get("production") or {}
    status = prod.get("status") or (data or {}).get("status")
    return {
        "value": _num(prod.get("max_correlation")),
        "status": status or ("ok" if prod.get("max_correlation") is not None else "unavailable"),
        "message": prod.get("message") or (data or {}).get("message"),
    }


async def fetch_self_correlation(client: Any, alpha_id: str) -> Dict[str, Any]:
    """Local self correlation (submitted-OS pool, excluding Power Pool alphas)."""
    data = await client.check_self_correlation(
        alpha_id, threshold=DEFAULT_SELF_THRESHOLD, correlation_type="self"
    )
    checks = (data or {}).get("checks") or {}
    node = checks.get("self") or data or {}
    return {
        "value": _num(node.get("max_correlation")),
        "status": "ok" if node.get("max_correlation") is not None else "unavailable",
    }


# --------------------------------------------------------------------------- #
# Admission
# --------------------------------------------------------------------------- #

async def evaluate_candidate(
    client: Any,
    alpha_id: str,
    pool: Dict[str, Any],
    *,
    prod_threshold: float = DEFAULT_PROD_THRESHOLD,
    self_threshold: float = DEFAULT_SELF_THRESHOLD,
    mutual_threshold: float = DEFAULT_MUTUAL_THRESHOLD,
    refresh_prod: bool = True,
    years: int = 4,
) -> Dict[str, Any]:
    """Assess whether ``alpha_id`` may join the pool. Never mutates the pool."""
    entries: Dict[str, Any] = pool.get("entries", {})
    details = await client.get_alpha_details(alpha_id)
    summary = summarize_alpha(details or {})
    summary["alpha_id"] = summary.get("alpha_id") or alpha_id

    blockers: List[str] = []
    warnings: List[str] = []

    if summary.get("status") == "ACTIVE":
        blockers.append("alpha is already submitted (status ACTIVE)")

    # --- own production correlation ---------------------------------------- #
    existing = entries.get(alpha_id) or {}
    if refresh_prod or existing.get("prod_corr") is None:
        prod = await fetch_production_correlation(client, alpha_id)
    else:
        prod = {"value": existing.get("prod_corr"), "status": "cached"}
    if prod["value"] is None:
        warnings.append(
            f"production correlation unavailable ({prod.get('status')}); "
            "candidate admitted only with force=True"
        )
        blockers.append("production correlation unknown")
    elif prod["value"] >= prod_threshold:
        blockers.append(
            f"production correlation {prod['value']:.4f} >= {prod_threshold}"
        )

    # --- own self correlation ---------------------------------------------- #
    slf = await fetch_self_correlation(client, alpha_id)
    if slf["value"] is not None and slf["value"] >= self_threshold:
        blockers.append(f"self correlation {slf['value']:.4f} >= {self_threshold}")

    # --- against everything already pooled --------------------------------- #
    pool_ids = [pid for pid in entries if pid != alpha_id]
    pair_corr, missing = await pairwise_against_pool(client, alpha_id, pool_ids, years=years)

    safety_violations = [
        {"alpha_id": oid, "correlation": round(c, 4)}
        for oid, c in sorted(pair_corr.items(), key=lambda kv: -kv[1])
        if c >= prod_threshold
    ]
    diversity_violations = [
        {"alpha_id": oid, "correlation": round(c, 4)}
        for oid, c in sorted(pair_corr.items(), key=lambda kv: -kv[1])
        if mutual_threshold <= c < prod_threshold
    ]
    if safety_violations:
        blockers.append(
            "pairwise correlation >= prod threshold with "
            + ", ".join(f"{v['alpha_id']}({v['correlation']})" for v in safety_violations)
            + " — submitting either would push the other past the production gate"
        )
    if diversity_violations:
        warnings.append(
            "pairwise correlation >= mutual threshold with "
            + ", ".join(f"{v['alpha_id']}({v['correlation']})" for v in diversity_violations)
        )
    if missing:
        warnings.append(f"PnL unavailable for pairwise check against: {', '.join(missing)}")

    max_pair = max(pair_corr.values()) if pair_corr else 0.0

    return {
        "alpha_id": alpha_id,
        "summary": summary,
        "prod_corr": prod["value"],
        "prod_corr_status": prod.get("status"),
        "self_corr": slf["value"],
        "max_pairwise_vs_pool": round(max_pair, 4),
        "pairwise": {k: round(v, 4) for k, v in sorted(pair_corr.items(), key=lambda kv: -kv[1])},
        "safety_violations": safety_violations,
        "diversity_violations": diversity_violations,
        "missing_pnl": missing,
        "blockers": blockers,
        "warnings": warnings,
        "admissible": not blockers,
        "thresholds": {
            "prod": prod_threshold,
            "self": self_threshold,
            "mutual": mutual_threshold,
        },
    }


async def add_candidate(
    client: Any,
    alpha_id: str,
    *,
    note: Optional[str] = None,
    force: bool = False,
    allow_diversity_fail: bool = False,
    prod_threshold: float = DEFAULT_PROD_THRESHOLD,
    self_threshold: float = DEFAULT_SELF_THRESHOLD,
    mutual_threshold: float = DEFAULT_MUTUAL_THRESHOLD,
    refresh_prod: bool = True,
) -> Dict[str, Any]:
    """Evaluate and, if it passes, persist the candidate."""
    pool = load_pool()
    report = await evaluate_candidate(
        client, alpha_id, pool,
        prod_threshold=prod_threshold,
        self_threshold=self_threshold,
        mutual_threshold=mutual_threshold,
        refresh_prod=refresh_prod,
    )

    rejected_for = list(report["blockers"])
    if report["diversity_violations"] and not allow_diversity_fail:
        rejected_for.append(
            f"diversity: pairwise >= {mutual_threshold} with "
            + ", ".join(v["alpha_id"] for v in report["diversity_violations"])
        )

    if rejected_for and not force:
        return {
            "action": "add",
            "added": False,
            "alpha_id": alpha_id,
            "reasons": rejected_for,
            "report": report,
            "hint": "Pass force=true to admit anyway (records forced_reasons on the entry).",
        }

    entry = dict(report["summary"])
    entry.update({
        "prod_corr": report["prod_corr"],
        "prod_corr_checked_at": _now() if report.get("prod_corr_status") != "cached" else None,
        "self_corr": report["self_corr"],
        "max_pairwise_vs_pool": report["max_pairwise_vs_pool"],
        "note": note,
        "added_at": _now(),
        "forced": bool(rejected_for),
        "forced_reasons": rejected_for or None,
    })
    pool.setdefault("entries", {})[alpha_id] = entry
    save_pool(pool)

    return {
        "action": "add",
        "added": True,
        "alpha_id": alpha_id,
        "entry": entry,
        "warnings": report["warnings"],
        "forced": bool(rejected_for),
        "forced_reasons": rejected_for or None,
        "pool_size": len(pool["entries"]),
    }


def remove_candidates(alpha_ids: Iterable[str]) -> Dict[str, Any]:
    pool = load_pool()
    entries = pool.setdefault("entries", {})
    removed, absent = [], []
    for aid in alpha_ids:
        if entries.pop(aid, None) is not None:
            removed.append(aid)
        else:
            absent.append(aid)
    if removed:
        save_pool(pool)
    return {"action": "remove", "removed": removed, "not_found": absent,
            "pool_size": len(entries)}


def list_pool(
    region: Optional[str] = None,
    pyramid: Optional[str] = None,
) -> Dict[str, Any]:
    pool = load_pool()
    entries = pool.get("entries", {})
    rows = []
    for aid, e in entries.items():
        if region and str(e.get("region", "")).upper() != region.upper():
            continue
        if pyramid and str(e.get("pyramid", "")).lower() != pyramid.lower():
            continue
        rows.append(e)
    rows.sort(key=lambda e: (
        str(e.get("region") or ""),
        str(e.get("pyramid") or ""),
        -( (e.get("metrics") or {}).get("sharpe") or 0.0),
    ))
    return {
        "pool_size": len(entries),
        "returned": len(rows),
        "filters": {"region": region, "pyramid": pyramid},
        "entries": rows,
        "path": str(pool_path()),
    }


# --------------------------------------------------------------------------- #
# Pyramid coverage: submitted + pooled
# --------------------------------------------------------------------------- #

def _flatten_submitted(
    pyramid_alphas: Dict[str, Any],
    region: Optional[str],
    delay: Optional[int],
) -> Dict[str, int]:
    """Collapse a pyramid-alphas payload into {category: submitted_count}.

    Handles both shapes this codebase produces:
      * RAW  (brain_client): {"pyramids": [{category:{id,..}, region, delay, alphaCount}, ...]}
      * SLIM (main._slim_pyramids): {"pyramids": {region: {"D1": {cat: n}}}}
    """
    counts: Dict[str, int] = {}
    root = (pyramid_alphas or {}).get("pyramids")

    def bump(cat: Any, n: Any) -> None:
        if not cat:
            return
        try:
            counts[str(cat)] = counts.get(str(cat), 0) + int(n or 0)
        except (TypeError, ValueError):
            pass

    if isinstance(root, list):  # raw
        for p in root:
            if not isinstance(p, dict):
                continue
            if region and str(p.get("region", "")).upper() != region.upper():
                continue
            if delay is not None and p.get("delay") != delay:
                continue
            cat = p.get("category")
            bump(cat.get("id") if isinstance(cat, dict) else cat, p.get("alphaCount"))
        return counts

    if isinstance(root, dict):  # slim
        for reg, by_delay in root.items():
            if region and str(reg).upper() != region.upper():
                continue
            if not isinstance(by_delay, dict):
                continue
            for dkey, cats in by_delay.items():
                if delay is not None and str(dkey).upper() != f"D{delay}":
                    continue
                if not isinstance(cats, dict):
                    continue
                for cat, n in cats.items():
                    bump(cat, n)
    return counts


async def pyramid_coverage(
    client: Any,
    *,
    region: Optional[str] = None,
    delay: Optional[int] = None,
    target: int = DEFAULT_PYRAMID_TARGET,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """The 'true coverage' table: submitted + pool, per pyramid.

    ``target`` is how many SUBMITTED alphas a pyramid needs before it counts as
    lit. Pool entries do not light a pyramid on their own — they are the queue
    that can get it there, which is exactly why they are reported alongside.
    """
    pyramid_alphas = await client.get_pyramid_alphas(start_date, end_date)
    submitted = _flatten_submitted(pyramid_alphas, region, delay)

    pool = load_pool()
    pooled: Dict[str, int] = {}
    for e in pool.get("entries", {}).values():
        if region and str(e.get("region", "")).upper() != region.upper():
            continue
        if delay is not None and e.get("delay") != delay:
            continue
        cat = (e.get("pyramid") or "unknown").lower()
        pooled[cat] = pooled.get(cat, 0) + 1

    known = [c for c in PYRAMID_ORDER if c in submitted or c in pooled]
    extra = sorted((set(submitted) | set(pooled)) - set(PYRAMID_ORDER))
    cats = list(dict.fromkeys(known + extra))

    rows = []
    lit = 0
    reachable = 0
    for cat in cats:
        sub = submitted.get(cat, 0)
        poo = pooled.get(cat, 0)
        need = max(0, target - sub)
        if need == 0:
            status = "OS_SUFFICIENT"
            lit += 1
            reachable += 1
        elif poo >= need:
            status = f"NEEDS_{need}_SUBMISSIONS_FROM_POOL"
            reachable += 1
        else:
            status = f"SHORT_BY_{need - poo}_CANDIDATES"
        rows.append({
            "pyramid": cat,
            "label": PYRAMID_LABELS.get(cat, cat),
            "submitted": sub,
            "pool": poo,
            "total": sub + poo,
            "needed_submissions": need,
            "status": status,
        })

    return {
        "scope": {"region": region, "delay": delay, "target_per_pyramid": target},
        "rows": rows,
        "totals": {
            "pyramids": len(rows),
            "lit_by_submitted": lit,
            "reachable_with_pool": reachable,
            "not_reachable": len(rows) - reachable,
            "submitted_alphas": sum(submitted.values()),
            "pooled_alphas": sum(pooled.values()),
        },
        "note": (
            "A pyramid lights on SUBMITTED alphas only. 'reachable_with_pool' counts "
            "pyramids that would light if the listed pool candidates were submitted."
        ),
    }


# --------------------------------------------------------------------------- #
# Submission planning
# --------------------------------------------------------------------------- #

async def submission_plan(
    client: Any,
    *,
    max_submissions: int = DEFAULT_DAILY_SUBMIT_CAP,
    region: Optional[str] = None,
    delay: Optional[int] = None,
    target: int = DEFAULT_PYRAMID_TARGET,
    prod_threshold: float = DEFAULT_PROD_THRESHOLD,
    self_threshold: float = DEFAULT_SELF_THRESHOLD,
    resolve_conflicts: bool = False,
    years: int = 4,
) -> Dict[str, Any]:
    """Choose today's submission batch and prove it is safe for the rest of the pool.

    Safety proof, for every candidate B left in the pool after submitting S:
        projected_prod_corr(B) = max(prod_corr(B), max_{A in S} |corr(A,B)|)
        projected_self_corr(B) = max(self_corr(B), max_{A in S} |corr(A,B)|)
    Both must stay under their thresholds; a candidate that would be pushed over
    is reported in ``collateral_damage`` and the batch is trimmed to avoid it.

    A pool that was forced to accept a mutually-exclusive pair would otherwise
    deadlock — neither member can be submitted without destroying the other, so
    a purely protective planner submits neither, forever. Such pairs are surfaced
    in ``conflicts`` with a recommended keep/drop. ``resolve_conflicts=True`` acts
    on that recommendation: it submits the higher-priority member and marks the
    loser ``sacrificed`` (it stays in the pool; removing it is your call).
    """
    pool = load_pool()
    entries: Dict[str, Any] = pool.get("entries", {})

    def in_scope(e: Dict[str, Any]) -> bool:
        if region and str(e.get("region", "")).upper() != region.upper():
            return False
        if delay is not None and e.get("delay") != delay:
            return False
        return True

    scoped = {aid: e for aid, e in entries.items() if in_scope(e)}
    if not scoped:
        return {"plan": [], "reason": "no pool candidates in scope",
                "scope": {"region": region, "delay": delay}}

    # Full pairwise matrix across the WHOLE pool (out-of-scope entries can still
    # be damaged by an in-scope submission, so they must be considered too).
    all_ids = list(entries.keys())
    corr: Dict[str, Dict[str, float]] = {}
    missing_pairs: List[str] = []
    if len(all_ids) >= 2:
        res = await client.get_mutual_correlation(all_ids, threshold=1.1, years=years)
        if res.get("error"):
            missing_pairs = all_ids
        else:
            matrix = res.get("matrix") or {}
            for a in all_ids:
                corr[a] = {b: abs(float(v)) for b, v in (matrix.get(a) or {}).items()
                           if b != a and v is not None}
            missing_pairs = list(res.get("missing_pnl") or [])

    def pair(a: str, b: str) -> Optional[float]:
        return (corr.get(a) or {}).get(b)

    # Coverage need drives priority: a submission that lights a pyramid beats one
    # that adds a fourth alpha to an already-lit pyramid.
    coverage = await pyramid_coverage(
        client, region=region, delay=delay, target=target
    )
    need_by_pyramid = {r["pyramid"]: r["needed_submissions"] for r in coverage["rows"]}

    def priority(aid: str) -> Tuple[float, float]:
        e = scoped[aid]
        cat = (e.get("pyramid") or "unknown").lower()
        need = need_by_pyramid.get(cat, 0)
        sharpe = (e.get("metrics") or {}).get("sharpe") or 0.0
        mult = e.get("pyramid_multiplier") or 1.0
        # Higher is better: unmet pyramid need first, then multiplier, then Sharpe.
        return (need * 1000 + mult * 10, sharpe)

    ordered = sorted(scoped.keys(), key=lambda a: priority(a), reverse=True)

    # Rank once; both the selection loop and the conflict recommendation use it.
    rank = {aid: i for i, aid in enumerate(ordered)}

    selected: List[str] = []
    skipped: List[Dict[str, Any]] = []
    sacrificed: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    remaining_need = dict(need_by_pyramid)

    def damage_from(aid: str, ignore: Sequence[str]) -> List[Dict[str, Any]]:
        """Pool candidates that submitting ``aid`` would push past a gate."""
        out = []
        for other, oe in entries.items():
            if other == aid or other in selected or other in ignore:
                continue
            c = pair(aid, other)
            if c is None:
                continue
            proj_prod = max(oe.get("prod_corr") or 0.0, c)
            proj_self = max(oe.get("self_corr") or 0.0, c)
            if proj_prod >= prod_threshold or proj_self >= self_threshold:
                out.append({
                    "alpha_id": other,
                    "pairwise": round(c, 4),
                    "projected_prod_corr": round(proj_prod, 4),
                    "projected_self_corr": round(proj_self, 4),
                })
        return out

    for aid in ordered:
        if len(selected) >= max_submissions:
            break
        e = scoped[aid]

        # The batch members all land in the pool together, so they must be
        # mutually safe as well.
        clash = next(
            (s for s in selected
             if (pair(aid, s) is not None and pair(aid, s) >= prod_threshold)),
            None,
        )
        if clash:
            skipped.append({"alpha_id": aid, "reason": f"pairwise >= {prod_threshold} with selected {clash}",
                            "correlation": round(pair(aid, clash), 4)})
            continue

        damage = damage_from(aid, ignore=sacrificed)
        if damage:
            # A victim that ranks BELOW aid is a genuine either/or: the pool can
            # ship aid or that victim, never both. Record the trade-off, and take
            # it only when the caller asked us to.
            losers = [d["alpha_id"] for d in damage if rank.get(d["alpha_id"], -1) > rank[aid]]
            blockers = [d for d in damage if d["alpha_id"] not in losers]
            for d in damage:
                if d["alpha_id"] in losers:
                    conflicts.append({
                        "keep": aid, "drop": d["alpha_id"],
                        "correlation": d["pairwise"],
                        "reason": (
                            f"mutually exclusive: submitting {aid} lifts {d['alpha_id']} "
                            f"to prod {d['projected_prod_corr']} / self {d['projected_self_corr']}"
                        ),
                        "recommendation": (
                            f"keep {aid} (higher pyramid need / multiplier / Sharpe), "
                            f"remove {d['alpha_id']} from the pool"
                        ),
                    })
            if blockers or not resolve_conflicts:
                skipped.append({
                    "alpha_id": aid,
                    "reason": "would push other candidates past a gate",
                    "collateral_damage": damage,
                    "resolvable": bool(losers) and not blockers,
                })
                continue
            sacrificed.extend(losers)

        selected.append(aid)
        cat = (e.get("pyramid") or "unknown").lower()
        remaining_need[cat] = max(0, remaining_need.get(cat, 0) - 1)

    # Report the post-submission state of everything left behind.
    projected = []
    for other, oe in entries.items():
        if other in selected:
            continue
        incr = [pair(s, other) for s in selected]
        incr = [c for c in incr if c is not None]
        top = max(incr) if incr else 0.0
        proj_prod = max(oe.get("prod_corr") or 0.0, top)
        proj_self = max(oe.get("self_corr") or 0.0, top)
        projected.append({
            "alpha_id": other,
            "pyramid": oe.get("pyramid"),
            "prod_corr_now": oe.get("prod_corr"),
            "max_pairwise_vs_batch": round(top, 4),
            "projected_prod_corr": round(proj_prod, 4),
            "projected_self_corr": round(proj_self, 4),
            "still_safe": proj_prod < prod_threshold and proj_self < self_threshold,
            "sacrificed": other in sacrificed,
        })
    projected.sort(key=lambda r: -r["projected_prod_corr"])

    plan = []
    for i, aid in enumerate(selected, 1):
        e = scoped[aid]
        plan.append({
            "order": i,
            "alpha_id": aid,
            "pyramid": e.get("pyramid"),
            "region": e.get("region"),
            "delay": e.get("delay"),
            "sharpe": (e.get("metrics") or {}).get("sharpe"),
            "prod_corr": e.get("prod_corr"),
            "self_corr": e.get("self_corr"),
        })

    return {
        "scope": {"region": region, "delay": delay, "max_submissions": max_submissions,
                  "target_per_pyramid": target},
        "thresholds": {"prod": prod_threshold, "self": self_threshold},
        "plan": plan,
        "plan_size": len(plan),
        "skipped": skipped,
        "conflicts": conflicts,
        "sacrificed": sacrificed,
        "remaining_pool_after_batch": projected,
        # Deliberately sacrificed candidates are excluded: they were given up on
        # purpose, so counting them as breakage would hide real breakage.
        "all_remaining_safe": all(
            r["still_safe"] for r in projected if not r["sacrificed"]
        ) if projected else True,
        "pairwise_unavailable_for": missing_pairs,
        "coverage_before": coverage["rows"],
        "note": (
            "The agent does NOT submit. This is a plan; submit manually. "
            "projected_prod_corr = max(current prod corr, |corr| vs the submitted batch). "
            + (
                f"{len(conflicts)} mutually-exclusive pair(s) found — rerun with "
                "resolve_conflicts=true to submit the recommended member and give up the other."
                if conflicts and not resolve_conflicts else ""
            )
        ).strip(),
    }


async def sync_pool(client: Any, *, refresh_prod: bool = False) -> Dict[str, Any]:
    """Refresh entries: drop alphas that are now submitted, re-read metrics.

    ``refresh_prod`` re-queries production correlation for each entry. That endpoint
    is single-concurrency and slow, so it is opt-in.
    """
    pool = load_pool()
    entries: Dict[str, Any] = pool.get("entries", {})
    promoted, refreshed, errors = [], [], []

    for aid in list(entries.keys()):
        try:
            details = await client.get_alpha_details(aid)
        except Exception as exc:  # noqa: BLE001 - report, don't abort the sweep
            errors.append({"alpha_id": aid, "error": str(exc)})
            continue
        summary = summarize_alpha(details or {})
        if summary.get("status") == "ACTIVE":
            entries.pop(aid, None)
            promoted.append(aid)
            continue
        entry = entries[aid]
        entry.update({k: v for k, v in summary.items() if v is not None})
        if refresh_prod:
            prod = await fetch_production_correlation(client, aid)
            if prod["value"] is not None:
                entry["prod_corr"] = prod["value"]
                entry["prod_corr_checked_at"] = _now()
        refreshed.append(aid)

    save_pool(pool)
    return {
        "action": "sync",
        "promoted_to_submitted": promoted,
        "refreshed": refreshed,
        "errors": errors,
        "pool_size": len(entries),
        "refresh_prod": refresh_prod,
    }

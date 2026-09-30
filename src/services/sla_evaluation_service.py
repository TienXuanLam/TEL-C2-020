"""AgentCore Platform v1.0 — step 2: SLAEvaluationNode.

Deterministic per-slice SLA threshold evaluation and severity
classification. No LLM call — the breach decision is exact arithmetic
comparison against configured thresholds, mirroring the "math is
authoritative, not LLM-generated" principle used across prior templates.
Called from src/nodes/main_node.py::MainNode.execute(). See
docs/02_design.md "Step Detail" and source proposal
EngTemplate_TEL-C2-020.md §4 Step 2, §11 Risk R-1.
"""

from __future__ import annotations

from typing import Any

CRITICAL = "Critical"
WARNING = "Warning"
OK = "OK"
# T1-01: a slice with zero evaluated metrics (no threshold available for
# any present metric — sla_kb missing, or no customer/slice-type-default
# entry) must never be reported as OK, which implies "evaluated and
# compliant". UNASSESSED is a distinct terminal state, excluded from OK
# counts, and must be surfaced prominently in both reports.
UNASSESSED = "Unassessed"

# metric -> "lower_is_better" (latency/loss/jitter) or "higher_is_better" (throughput)
_METRIC_DIRECTION = {
    "latency_ms": "lower_is_better",
    "throughput_mbps": "higher_is_better",
    "packet_loss_pct": "lower_is_better",
    "jitter_ms": "lower_is_better",
}

# source proposal §4 Step 2: Warning if within 10% of threshold
_WARNING_MARGIN_RATIO = 0.10

_SEVERITY_RANK = {UNASSESSED: -1, OK: 0, WARNING: 1, CRITICAL: 2}


def evaluate_slices(
    normalized_records: list[dict[str, Any]], sla_kb: object | None = None
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Evaluate every slice's metrics against SLA thresholds.

    Returns (breach_records, slice_severity_summary). Every present metric
    with an available threshold is evaluated; a slice's worst-case
    severity is the max across its evaluated metrics (Unassessed < OK <
    Warning < Critical). A slice with zero evaluated metrics (T1-01) is
    UNASSESSED, never OK — "not evaluated" must never be reported as
    "evaluated and compliant".
    """
    breach_records: list[dict[str, Any]] = []
    slice_severity_summary: dict[str, str] = {}

    for record in normalized_records:
        slice_id = record["slice_id"]
        slice_type = record.get("slice_type", "unknown")
        thresholds, threshold_source = _load_thresholds(slice_id, slice_type, sla_kb)

        worst_severity = UNASSESSED
        evaluated_any_metric = False
        for metric, direction in _METRIC_DIRECTION.items():
            measured = record.get(metric)
            if measured is None:
                continue

            # T1-02: outlier-flagged values are retained for audit but are
            # not eligible to drive an SLA decision — quarantine as
            # Unassessed for this metric rather than feeding a known-bad
            # value into _classify_metric().
            if _metric_is_outlier(record, metric):
                breach_records.append(
                    {
                        "slice_id": slice_id,
                        "metric": metric,
                        "measured": measured,
                        "sla_threshold": None,
                        "delta": None,
                        "severity": UNASSESSED,
                        "threshold_source": "data_quality_invalid",
                    }
                )
                continue

            threshold = thresholds.get(metric)
            if threshold is None:
                continue

            severity, delta = _classify_metric(measured, threshold, direction)
            breach_records.append(
                {
                    "slice_id": slice_id,
                    "metric": metric,
                    "measured": measured,
                    "sla_threshold": threshold,
                    "delta": delta,
                    "severity": severity,
                    "threshold_source": threshold_source,
                }
            )
            evaluated_any_metric = True
            if _SEVERITY_RANK[severity] > _SEVERITY_RANK[worst_severity]:
                worst_severity = severity

        if not evaluated_any_metric:
            worst_severity = UNASSESSED
        slice_severity_summary[slice_id] = worst_severity

    return breach_records, slice_severity_summary


def _metric_is_outlier(record: dict[str, Any], metric: str) -> bool:
    data_quality = record.get("data_quality") or []
    return any(entry.startswith(f"{metric} out of expected range") for entry in data_quality)


def _load_thresholds(slice_id: str, slice_type: str, sla_kb: object | None) -> tuple[dict[str, float], str]:
    """Customer-specific threshold first, slice-type default fallback
    (source proposal Risk R-1). Never silently skips evaluation — falls
    back to an empty dict (no metrics evaluated) only if neither source
    has data, which callers can detect via absent breach_records entries.
    """
    if sla_kb is None:
        return {}, "unavailable"

    customer_thresholds = getattr(sla_kb, "get_customer_thresholds", lambda _sid: None)(slice_id)
    if customer_thresholds:
        return customer_thresholds, "customer"

    default_thresholds = getattr(sla_kb, "get_slice_type_defaults", lambda _st: None)(slice_type)
    if default_thresholds:
        return default_thresholds, "slice_type_default"

    return {}, "unavailable"


def _classify_metric(measured: float, threshold: float, direction: str) -> tuple[str, float]:
    """Returns (severity, delta). delta = measured - threshold, always
    signed the same way regardless of direction, so a positive delta
    consistently means "worse than threshold" for lower_is_better metrics
    and "better than threshold" is a negative delta — callers/reports
    interpret delta relative to direction.
    """
    delta = round(measured - threshold, 4)

    if direction == "lower_is_better":
        if measured > threshold:
            return CRITICAL, delta
        # measured <= threshold: OK. Warning zone is strictly inside the
        # margin below the threshold (measured < threshold), never at or
        # under an exact match.
        warning_bound = threshold * (1 - _WARNING_MARGIN_RATIO)
        if measured < threshold and measured > warning_bound:
            return WARNING, delta
        return OK, delta

    # higher_is_better (throughput)
    if measured < threshold:
        return CRITICAL, delta
    # measured >= threshold: OK. Warning zone is strictly above the
    # threshold and below the margin, never at or above an exact match.
    warning_bound = threshold * (1 + _WARNING_MARGIN_RATIO)
    if measured > threshold and measured < warning_bound:
        return WARNING, delta
    return OK, delta

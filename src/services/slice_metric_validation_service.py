"""AgentCore Platform v1.0 — step 1: SliceMetricValidationNode.

Pure domain logic for PreProcessNode: schema validation and unit
normalization of 5G network slice KPI records. No framework/agenticstar
imports. Called from src/nodes/pre_process_node.py::PreProcessNode.execute().
See docs/02_design.md "Step Detail" and source proposal
EngTemplate_TEL-C2-020.md §4 Step 1.
"""

from __future__ import annotations

from typing import Any

_REQUIRED_FIELDS = ("slice_id", "time_window")
_METRIC_FIELDS = ("latency_ms", "throughput_mbps", "packet_loss_pct", "jitter_ms")

# Functional S-1/S-2 input protection — an unbounded batch has no
# record-count cap anywhere else in the pipeline. Mirrors an established
# fleet pattern for bounding record-count on batch input. Kept
# consistent with PreProcessNode.MAX_RAW_INPUT_CHARS (a full-size batch of
# this many records must fit under that raw-input character cap — see that
# constant's docstring for why it's bounded well below what it might
# otherwise be).
MAX_KPI_RECORDS = 100


def validate_top_level_schema(payload: dict[str, Any], max_kpi_records: int = MAX_KPI_RECORDS) -> list[str]:
    """Return top-level payload errors (missing required keys); empty = valid."""
    errors: list[str] = []
    records = payload.get("kpi_records")
    if not records or not isinstance(records, list):
        errors.append("PreProcessNode: kpi_records is missing or not a list")
        return errors

    if len(records) > max_kpi_records:
        errors.append(
            f"PreProcessNode: kpi_records count ({len(records)}) exceeds "
            f"the maximum of {max_kpi_records} — split into multiple batches"
        )
    return errors


def validate_and_normalize_records(
    raw_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate required fields; normalize units; flag data quality issues.

    Returns (normalized_records, validation_errors). A record missing
    slice_id/time_window AND at least one KPI metric is excluded (never
    guessed); a record with a null/outlier individual metric is annotated
    with data_quality, never dropped (source proposal §4 Step 1, Risk R-2).
    """
    normalized_records: list[dict[str, Any]] = []
    validation_errors: list[dict[str, Any]] = []

    for record in raw_records:
        slice_id = record.get("slice_id", "<unknown>")

        missing_required = [f for f in _REQUIRED_FIELDS if not record.get(f)]
        has_any_metric = any(record.get(f) is not None for f in _METRIC_FIELDS)

        if missing_required or not has_any_metric:
            reason = f"missing required fields: {missing_required}" if missing_required else "no KPI metric present"
            validation_errors.append({"slice_id": slice_id, "error": reason})
            continue

        normalized_records.append(_normalize_record(record))

    return normalized_records, validation_errors


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    data_quality: list[str] = []

    latency_ms = _normalize_latency(record, data_quality)
    throughput_mbps = _normalize_throughput(record, data_quality)
    packet_loss_pct = _normalize_packet_loss(record, data_quality)
    jitter_ms = _normalize_jitter(record, data_quality)

    return {
        "slice_id": record["slice_id"],
        "slice_type": record.get("slice_type", "unknown"),
        "time_window": record["time_window"],
        "latency_ms": latency_ms,
        "throughput_mbps": throughput_mbps,
        "packet_loss_pct": packet_loss_pct,
        "jitter_ms": jitter_ms,
        "data_quality": data_quality,
    }


def _normalize_latency(record: dict[str, Any], data_quality: list[str]) -> float | None:
    if record.get("latency_ms") is not None:
        return _flag_if_outlier(record["latency_ms"], "latency_ms", 0, 10000, data_quality)
    if record.get("latency_us") is not None:
        return _flag_if_outlier(record["latency_us"] / 1000, "latency_ms", 0, 10000, data_quality)
    return None


def _normalize_throughput(record: dict[str, Any], data_quality: list[str]) -> float | None:
    if record.get("throughput_mbps") is not None:
        return _flag_if_outlier(record["throughput_mbps"], "throughput_mbps", 0, 100000, data_quality)
    if record.get("throughput_kbps") is not None:
        return _flag_if_outlier(record["throughput_kbps"] / 1000, "throughput_mbps", 0, 100000, data_quality)
    return None


def _normalize_packet_loss(record: dict[str, Any], data_quality: list[str]) -> float | None:
    if record.get("packet_loss_pct") is not None:
        return _flag_if_outlier(record["packet_loss_pct"], "packet_loss_pct", 0, 100, data_quality)
    sent = record.get("packets_sent")
    lost = record.get("packets_lost")
    if sent and lost is not None and sent > 0:
        pct = round((lost / sent) * 100, 4)
        return _flag_if_outlier(pct, "packet_loss_pct", 0, 100, data_quality)
    return None


def _normalize_jitter(record: dict[str, Any], data_quality: list[str]) -> float | None:
    if record.get("jitter_ms") is not None:
        return _flag_if_outlier(record["jitter_ms"], "jitter_ms", 0, 10000, data_quality)
    if record.get("jitter_us") is not None:
        return _flag_if_outlier(record["jitter_us"] / 1000, "jitter_ms", 0, 10000, data_quality)
    return None


def _flag_if_outlier(
    value: float, field_name: str, min_bound: float, max_bound: float, data_quality: list[str]
) -> float:
    if value < min_bound or value > max_bound:
        data_quality.append(f"{field_name} out of expected range: {value}")
    return value

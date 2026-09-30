"""AgentCore Platform v1.0"""

# Node contract (node-contract.md): extend FunctionNode; implement
# execute(state) -> dict; return ONLY changed fields; use AgentStatus enum.
#
# Dispatches step 1: SliceMetricValidationNode (schema validation, unit
# normalization, data_quality flagging). See docs/02_design.md "Data Flow"
# PreProcessNode section, source proposal EngTemplate_TEL-C2-020.md §4 Step 1.

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import slice_metric_validation_service

MIN_RAW_INPUT_CHARS = 20
# T1-04: S-2 must be a functional gate, not an unused annotation — a raw
# payload above this size is rejected outright before JSON parsing even
# begins (the record-count cap in validate_top_level_schema() only kicks
# in after parsing succeeds; this bound protects the parse step itself).
#
# Deliberately kept well below the framework's default PII scan's
# practical performance envelope: the framework runs
# shared.security.pii_detector.detect_pii() on user_input BEFORE this hook
# (see FunctionNode._security_gate_input()) on EVERY invocation, regardless
# of this cap — and that scan was measured at ~18s for a 100k-char input,
# ~4.8s for 50k, ~0.2s for 10k (super-linear, not the linear cost its
# regex-scan design implies — likely the "name" pattern backtracking on
# large non-matching text). 30_000 keeps the mandatory scan in the
# sub-second range while comfortably covering a MAX_KPI_RECORDS batch of
# real KPI JSON. This is a latent performance defect in the shared
# pii_detector utility itself (out of this project's scope to fix) —
# flagged in docs/07_operation_guide.md.
MAX_RAW_INPUT_CHARS = 30_000


class PreProcessNode(FunctionNode):
    """Step 1: slice KPI batch validation + unit normalization."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        # S-2 (docs/02_design.md "Security Design"): functional raw-input
        # size gate before JSON parsing. Slice KPI payloads contain no PII
        # (source proposal §10 Angle 5) — no domain PII scan needed beyond
        # the framework default. T1-04: an oversized raw payload is
        # rejected directly here (status=error set inside the hook, per
        # FunctionNode's S-2 contract — BaseNode.__call__() short-circuits
        # on state["status"]==ERROR immediately after this hook returns,
        # skipping execute() entirely), not merely flagged and forgotten.
        raw_input = state.get("user_input", "")
        if raw_input and len(raw_input) > MAX_RAW_INPUT_CHARS:
            state = dict(state)
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = state.get("error_log", []) + [
                f"PreProcessNode: user_input length ({len(raw_input)}) exceeds "
                f"the maximum of {MAX_RAW_INPUT_CHARS} characters"
            ]
            return state
        if raw_input and len(raw_input) < MIN_RAW_INPUT_CHARS:
            state = dict(state)
            state["_raw_input_suspiciously_short"] = True
        return state

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw_input = state.get("user_input", "")

        try:
            payload = json.loads(raw_input) if raw_input else {}
        except json.JSONDecodeError:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input is not valid JSON"],
            }

        top_level_errors = slice_metric_validation_service.validate_top_level_schema(payload)
        if top_level_errors:
            return {"status": AgentStatus.ERROR.value, "error_log": top_level_errors}

        normalized_records, validation_errors = slice_metric_validation_service.validate_and_normalize_records(
            payload.get("kpi_records", [])
        )

        data_quality_flag_count = sum(1 for r in normalized_records if r["data_quality"])

        emit_trace_event(
            "kpi_batch_validated",
            {
                "total_records": len(payload.get("kpi_records", [])),
                "normalized_records": len(normalized_records),
                "excluded_records": len(payload.get("kpi_records", [])) - len(normalized_records),
                "data_quality_flag_count": data_quality_flag_count,
            },
            state,
        )

        return {
            "normalized_kpi_records": json.dumps(normalized_records),
            "validation_errors": json.dumps(validation_errors),
            "status": AgentStatus.SUCCESS.value,
        }

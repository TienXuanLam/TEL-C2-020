"""AgentCore Platform v1.0"""

# Node contract (node-contract.md): extend FunctionNode; implement
# execute(state) -> dict; return ONLY changed fields; use AgentStatus enum.
#
# Dispatches step 2: SLAEvaluationNode (deterministic threshold comparison
# + severity classification, no LLM call). See docs/02_design.md "Data
# Flow" MainNode section, source proposal EngTemplate_TEL-C2-020.md §4 Step 2.

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import sla_evaluation_service


class MainNode(FunctionNode):
    """Step 2: deterministic SLA threshold evaluation + severity classification."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, sla_kb: object | None = None) -> None:
        self._sla_kb = sla_kb

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized_records = json.loads(state.get("normalized_kpi_records") or "[]")

        if not normalized_records:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["MainNode: missing normalized_kpi_records — PreProcessNode must run first"],
            }

        breach_records, slice_severity_summary = sla_evaluation_service.evaluate_slices(
            normalized_records, sla_kb=self._sla_kb
        )

        severity_counts = {"Critical": 0, "Warning": 0, "OK": 0}
        for severity in slice_severity_summary.values():
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

        emit_trace_event(
            "sla_evaluated",
            {
                "slice_count": len(slice_severity_summary),
                "severity_counts": severity_counts,
                "breach_record_count": len(breach_records),
            },
            state,
        )

        return {
            "breach_records": json.dumps(breach_records),
            "slice_severity_summary": json.dumps(slice_severity_summary),
            "status": AgentStatus.SUCCESS.value,
        }

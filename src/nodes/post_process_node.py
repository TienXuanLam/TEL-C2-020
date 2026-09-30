"""AgentCore Platform v1.0"""

# Node contract (node-contract.md): extend FunctionNode; implement
# execute(state) -> dict; return ONLY changed fields; use AgentStatus enum.
#
# Real S-3 gate slot. Dispatches step 3: ReportRenderNode (remediation
# ranking, dual JSON/Markdown report rendering, mandatory DRAFT watermark).
# See docs/02_design.md "DRAFT watermark guarantee", source proposal
# EngTemplate_TEL-C2-020.md §4 Step 3, §11 Risk R-6.

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import report_render_service


class PostProcessNode(FunctionNode):
    """Step 3: remediation ranking + dual report rendering + DRAFT watermark."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: object | None = None) -> None:
        self._llm = llm

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        # S-3 (docs/02_design.md "Security Design"): belt-and-suspenders
        # re-check — every report artifact must carry the DRAFT watermark,
        # independent of report_render_service's default (source proposal
        # Risk R-6, High severity). May raise per
        # how-to/customize-security-gates.md contract.
        raw_result = result.get("result")
        formatted_output = result.get("formatted_output")

        if isinstance(raw_result, str):
            response = json.loads(raw_result)
            if response.get("watermark") != report_render_service.DRAFT_WATERMARK:
                raise ValueError("S-3 violation: violation_report.json is missing the mandatory DRAFT watermark")
        if isinstance(formatted_output, str) and formatted_output:
            if report_render_service.DRAFT_WATERMARK not in formatted_output:
                raise ValueError("S-3 violation: violation_report.md is missing the mandatory DRAFT watermark")
        return result

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized_records = json.loads(state.get("normalized_kpi_records") or "[]")
        validation_errors = json.loads(state.get("validation_errors") or "[]")
        breach_records = json.loads(state.get("breach_records") or "[]")
        slice_severity_summary = json.loads(state.get("slice_severity_summary") or "{}")

        ranked_remediations = report_render_service.rank_remediation_actions(breach_records)
        remediation_narrative = report_render_service.draft_remediation_narrative(ranked_remediations, llm=self._llm)

        internal_report = report_render_service.build_internal_report(
            normalized_records,
            validation_errors,
            breach_records,
            slice_severity_summary,
            ranked_remediations,
        )
        customer_markdown = report_render_service.render_customer_markdown(internal_report, remediation_narrative)

        emit_trace_event(
            "report_rendered",
            {
                "slice_count": internal_report["slice_count"],
                "severity_counts": internal_report["severity_counts"],
                "remediation_action_count": len(ranked_remediations),
            },
            state,
        )

        return {
            "result": json.dumps(internal_report),
            "formatted_output": customer_markdown,
            "status": AgentStatus.SUCCESS.value,
        }

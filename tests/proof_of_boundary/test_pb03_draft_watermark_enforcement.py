# PB-03: the DRAFT watermark is enforced at the code level, not just
# documented — every report artifact carries it, even with a real LLM
# configured for remediation narrative drafting, and the S-3 gate
# independently re-verifies this. See docs/02_design.md "DRAFT watermark
# guarantee" and docs/03_test_spec.md PB-03.

import json

from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode
from src.services import report_render_service


class FakeLLM:
    """Simulates an LLM that (maliciously or accidentally) tries to omit
    the watermark from its narrative text — the watermark must still be
    present because it is applied structurally, not left to the LLM.
    """

    def complete(self, prompt, **kwargs) -> str:
        return "Everything looks fine, no review needed."


def _state_with_breach() -> dict:
    return {
        "normalized_kpi_records": json.dumps(
            [{"slice_id": "S01", "slice_type": "eMBB", "latency_ms": 15.0,
              "throughput_mbps": 100.0, "packet_loss_pct": 0.01, "jitter_ms": 1.0,
              "data_quality": []}]
        ),
        "validation_errors": json.dumps([]),
        "breach_records": json.dumps(
            [{"slice_id": "S01", "metric": "latency_ms", "measured": 15.0,
              "sla_threshold": 10.0, "delta": 5.0, "severity": "Critical",
              "threshold_source": "customer"}]
        ),
        "slice_severity_summary": json.dumps({"S01": "Critical"}),
        "correlation_id": "test-corr",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "trace_id": "",
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "caller_id": "",
        "hitl_allowed": True,
        "node_history": [],
        "error_log": [],
    }


class TestPB03DraftWatermarkEnforcement:
    def test_watermark_present_even_when_llm_narrative_omits_it(self):
        node = PostProcessNode(llm=FakeLLM())
        result = node.execute(_state_with_breach())
        assert report_render_service.DRAFT_WATERMARK in result["formatted_output"]
        report = json.loads(result["result"])
        assert report["watermark"] == report_render_service.DRAFT_WATERMARK

    def test_s3_gate_passes_for_correctly_watermarked_output(self):
        node = PostProcessNode(llm=FakeLLM())
        result = node.execute(_state_with_breach())
        # Must not raise.
        node._extra_security_gate_output(result)

    def test_s3_gate_catches_watermark_removed_after_render(self):
        node = PostProcessNode(llm=FakeLLM())
        result = node.execute(_state_with_breach())
        tampered = dict(result)
        tampered["formatted_output"] = result["formatted_output"].replace(
            report_render_service.DRAFT_WATERMARK, ""
        )
        try:
            node._extra_security_gate_output(tampered)
            assert False, "S-3 gate must raise when watermark is missing"
        except ValueError:
            pass

    def test_watermark_present_via_call_full_pipeline(self):
        node = PostProcessNode(llm=FakeLLM())
        state = _state_with_breach()
        result = node(state)  # full __call__ pipeline including S-3 gate
        assert result["status"] == "success"
        assert report_render_service.DRAFT_WATERMARK in result["formatted_output"]

    def test_ungrounded_llm_narrative_never_reaches_customer_facing_output(self):
        # T1-03/T2-03: an LLM that omits the actual breached metric — the
        # same content this file's FakeLLM already simulates — must never
        # be embedded verbatim; the deterministic fallback (grounded in
        # the fixed ranked_remediations) is used instead.
        node = PostProcessNode(llm=FakeLLM())
        result = node(_state_with_breach())
        assert "latency_ms" in result["formatted_output"]
        assert "Everything looks fine" not in result["formatted_output"]

    def test_fabricated_commitment_never_reaches_customer_facing_output(self):
        class FabricatingLLM:
            def complete(self, prompt, **kwargs) -> str:
                return "We guarantee a full SLA credit will be issued within 24 hours."

        node = PostProcessNode(llm=FabricatingLLM())
        result = node(_state_with_breach())
        assert "SLA credit" not in result["formatted_output"]
        assert "guarantee" not in result["formatted_output"]
        assert "latency_ms" in result["formatted_output"]

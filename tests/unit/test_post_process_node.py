# TEL-C2-020 — Unit Tests: PostProcessNode

import json

import pytest
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode
from src.services import report_render_service


def _base_state(**overrides) -> dict:
    state = {
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
    state.update(overrides)
    return state


class TestPostProcessNode:
    def setup_method(self):
        self.node = PostProcessNode()

    def test_success_path(self):
        result = self.node.execute(_base_state())
        assert result["status"] == "success"
        report = json.loads(result["result"])
        assert report["watermark"] == report_render_service.DRAFT_WATERMARK

    def test_markdown_output_watermarked(self):
        result = self.node.execute(_base_state())
        assert report_render_service.DRAFT_WATERMARK in result["formatted_output"]

    def test_no_breaches_still_succeeds(self):
        state = _base_state(
            breach_records=json.dumps(
                [{"slice_id": "S01", "metric": "latency_ms", "measured": 5.0,
                  "sla_threshold": 10.0, "delta": -5.0, "severity": "OK",
                  "threshold_source": "customer"}]
            ),
            slice_severity_summary=json.dumps({"S01": "OK"}),
        )
        result = self.node.execute(state)
        assert result["status"] == "success"


class TestPostProcessNodeSecurityGateOutput:
    def test_gate_passes_for_valid_output(self):
        node = PostProcessNode()
        result = node.execute(_base_state())
        gated = node._extra_security_gate_output(result)
        assert gated is result

    def test_gate_raises_if_json_report_missing_watermark(self):
        node = PostProcessNode()
        tampered = {"result": json.dumps({"watermark": "not-the-real-watermark"}), "formatted_output": ""}
        with pytest.raises(ValueError):
            node._extra_security_gate_output(tampered)

    def test_gate_raises_if_markdown_missing_watermark(self):
        node = PostProcessNode()
        tampered = {
            "result": json.dumps({"watermark": report_render_service.DRAFT_WATERMARK}),
            "formatted_output": "# Report with no watermark text",
        }
        with pytest.raises(ValueError):
            node._extra_security_gate_output(tampered)


class TestPostProcessNodeTrustGate:
    def test_trust_gate_blocks_anonymous(self):
        node = PostProcessNode()
        state = _base_state()
        state["caller_trust_level"] = TrustLevel.ANONYMOUS.value
        result = node(state)
        assert result["status"] == "error"

    def test_trust_gate_allows_verified_external(self):
        node = PostProcessNode()
        state = _base_state()
        state["caller_trust_level"] = TrustLevel.VERIFIED_EXTERNAL.value
        result = node(state)
        assert result["status"] == "success"

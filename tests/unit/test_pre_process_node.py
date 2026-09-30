# TEL-C2-020 — Unit Tests: PreProcessNode

import json

from framework.schemas.trust_level import TrustLevel
from src.nodes.pre_process_node import PreProcessNode

_VALID_PAYLOAD = {
    "kpi_records": [
        {
            "slice_id": "S01",
            "slice_type": "eMBB",
            "time_window": "2026-01-01T00:00:00Z/2026-01-01T01:00:00Z",
            "latency_ms": 5.2,
            "throughput_mbps": 100.0,
            "packet_loss_pct": 0.01,
            "jitter_ms": 1.5,
        }
    ]
}


def _base_state(user_input: str = "") -> dict:
    return {
        "user_input": user_input,
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


class TestPreProcessNode:
    def setup_method(self):
        self.node = PreProcessNode()

    def test_valid_payload_success(self):
        state = _base_state(json.dumps(_VALID_PAYLOAD))
        result = self.node.execute(state)
        assert result["status"] == "success"
        records = json.loads(result["normalized_kpi_records"])
        assert records[0]["slice_id"] == "S01"

    def test_empty_input_returns_error(self):
        state = _base_state("")
        result = self.node.execute(state)
        assert result["status"] == "error"

    def test_invalid_json_returns_error(self):
        state = _base_state("not json")
        result = self.node.execute(state)
        assert result["status"] == "error"
        assert "not valid JSON" in result["error_log"][0]

    def test_missing_kpi_records_returns_error(self):
        state = _base_state(json.dumps({}))
        result = self.node.execute(state)
        assert result["status"] == "error"

    def test_quarantined_record_not_error_status(self):
        payload = {"kpi_records": [{"slice_id": "S01"}]}  # missing time_window + no metric
        state = _base_state(json.dumps(payload))
        result = self.node.execute(state)
        assert result["status"] == "success"
        errors = json.loads(result["validation_errors"])
        assert len(errors) == 1

    def test_oversized_kpi_batch_rejected(self):
        # T1-04: functional record-count cap.
        from src.services.slice_metric_validation_service import MAX_KPI_RECORDS

        payload = {
            "kpi_records": [
                {"slice_id": f"S{i}", "time_window": "x", "latency_ms": 1.0}
                for i in range(MAX_KPI_RECORDS + 1)
            ]
        }
        state = _base_state(json.dumps(payload))
        result = self.node.execute(state)
        assert result["status"] == "error"
        assert any("exceeds the maximum" in e for e in result["error_log"])


class TestPreProcessNodeSecurityGateInput:
    def test_short_raw_input_flagged(self):
        node = PreProcessNode()
        state = _base_state("x")
        result = node._extra_security_gate_input(state)
        assert result["_raw_input_suspiciously_short"] is True

    def test_hook_does_not_raise_on_empty_input(self):
        node = PreProcessNode()
        state = _base_state("")
        result = node._extra_security_gate_input(state)
        assert result is not None

    def test_oversized_raw_input_rejected_directly_by_hook(self):
        # T1-04: S-2 must be a functional gate — an oversized raw payload
        # is rejected inside the hook itself (status=error set directly),
        # not merely flagged and forgotten.
        from src.nodes.pre_process_node import MAX_RAW_INPUT_CHARS

        node = PreProcessNode()
        state = _base_state("x" * (MAX_RAW_INPUT_CHARS + 1))
        result = node._extra_security_gate_input(state)
        assert result["status"] == "error"
        assert any("exceeds the maximum" in e for e in result["error_log"])

    def test_oversized_raw_input_rejected_via_real_call_entry_point(self):
        # T1-04/T2-04: through the real __call__() gate chain, not the
        # hook called directly.
        from src.nodes.pre_process_node import MAX_RAW_INPUT_CHARS

        node = PreProcessNode()
        state = _base_state("x" * (MAX_RAW_INPUT_CHARS + 1))
        state["caller_trust_level"] = TrustLevel.VERIFIED_EXTERNAL.value
        result = node(state)
        assert result["status"] == "error"


class TestPreProcessNodeTrustGate:
    def test_trust_gate_blocks_anonymous(self):
        node = PreProcessNode()
        state = _base_state(json.dumps(_VALID_PAYLOAD))
        state["caller_trust_level"] = TrustLevel.ANONYMOUS.value
        result = node(state)  # via __call__, not execute()
        assert result["status"] == "error"

    def test_trust_gate_allows_verified_external(self):
        node = PreProcessNode()
        state = _base_state(json.dumps(_VALID_PAYLOAD))
        state["caller_trust_level"] = TrustLevel.VERIFIED_EXTERNAL.value
        result = node(state)
        assert result["status"] == "success"

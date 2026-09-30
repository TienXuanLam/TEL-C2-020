# TEL-C2-020 — Unit Tests: MainNode (step 2 dispatcher)

import json

from framework.schemas.trust_level import TrustLevel
from src.nodes.main_node import MainNode


class FakeKB:
    def __init__(self, slice_type_defaults=None):
        self._defaults = slice_type_defaults or {}

    def get_customer_thresholds(self, slice_id):
        return None

    def get_slice_type_defaults(self, slice_type):
        return self._defaults.get(slice_type)


def _base_state(**overrides) -> dict:
    state = {
        "normalized_kpi_records": json.dumps(
            [{"slice_id": "S01", "slice_type": "eMBB", "latency_ms": 5.0,
              "throughput_mbps": 100.0, "packet_loss_pct": 0.01, "jitter_ms": 1.0,
              "data_quality": []}]
        ),
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


class TestMainNode:
    def test_success_path(self):
        kb = FakeKB(slice_type_defaults={"eMBB": {"latency_ms": 10.0}})
        node = MainNode(sla_kb=kb)
        result = node.execute(_base_state())
        assert result["status"] == "success"
        summary = json.loads(result["slice_severity_summary"])
        assert "S01" in summary

    def test_missing_prerequisite_state_returns_error(self):
        node = MainNode(sla_kb=None)
        result = node.execute(_base_state(normalized_kpi_records="[]"))
        assert result["status"] == "error"

    def test_no_kb_still_succeeds_no_breach_records(self):
        node = MainNode(sla_kb=None)
        result = node.execute(_base_state())
        assert result["status"] == "success"
        breach_records = json.loads(result["breach_records"])
        assert breach_records == []


class TestMainNodeTrustGate:
    def test_trust_gate_blocks_anonymous(self):
        node = MainNode(sla_kb=None)
        state = _base_state()
        state["caller_trust_level"] = TrustLevel.ANONYMOUS.value
        result = node(state)
        assert result["status"] == "error"

    def test_trust_gate_allows_verified_external(self):
        node = MainNode(sla_kb=None)
        state = _base_state()
        state["caller_trust_level"] = TrustLevel.VERIFIED_EXTERNAL.value
        result = node(state)
        assert result["status"] == "success"

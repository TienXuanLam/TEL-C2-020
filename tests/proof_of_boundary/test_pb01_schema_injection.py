# PB-01: S-1 schema-injection resistance.
# A malformed KPI batch payload (missing kpi_records, non-list kpi_records)
# must never reach MainNode. See docs/02_design.md "Security Design" and
# docs/03_test_spec.md PB-01.

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import FiveGNetworkSliceSLAMonitoringReportingAgent
from src.nodes.pre_process_node import PreProcessNode


def _state(user_input: str) -> dict:
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


class TestPB01SchemaInjectionResistance:
    # T2-04: all tests go through node(state) (__call__() — the real
    # S-1/S-2/trust-gate entry point), not execute() directly — a
    # regression that broke __call__() wiring would not be caught by an
    # execute()-only test.

    def test_missing_kpi_records_rejected_before_main_node(self):
        result = PreProcessNode()(_state(json.dumps({})))
        assert result["status"] == "error"
        assert "normalized_kpi_records" not in result

    def test_non_list_kpi_records_rejected(self):
        result = PreProcessNode()(_state(json.dumps({"kpi_records": "not-a-list"})))
        assert result["status"] == "error"

    def test_non_json_injection_rejected(self):
        result = PreProcessNode()(_state("'; DROP TABLE slices;--"))
        assert result["status"] == "error"

    def test_empty_payload_rejected(self):
        result = PreProcessNode()(_state(""))
        assert result["status"] == "error"


class TestPB01FullGraphMainNodeUnreachable:
    # T2-04: prove malformed input rejected by PreProcessNode never lets
    # the compiled graph reach MainNode — not just that PreProcessNode
    # itself returns status=error in isolation.
    def _compiled_agent(self, monkeypatch):
        import src.nodes.main_node as main_node_module

        main_node_called = {"value": False}
        original_execute = main_node_module.MainNode.execute

        def spy_execute(self, state):
            main_node_called["value"] = True
            return original_execute(self, state)

        monkeypatch.setattr(main_node_module.MainNode, "execute", spy_execute)

        agent = FiveGNetworkSliceSLAMonitoringReportingAgent(config={"max_retry": 1})
        agent.compile()
        return agent, main_node_called

    def test_missing_kpi_records_never_reaches_main_node_via_full_graph(self, monkeypatch):
        agent, main_node_called = self._compiled_agent(monkeypatch)
        ctx = InvocationContext(session_id="pb01-test", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(json.dumps({}), ctx=ctx)
        assert result["status"] == "error"
        assert main_node_called["value"] is False

    def test_non_json_injection_never_reaches_main_node_via_full_graph(self, monkeypatch):
        agent, main_node_called = self._compiled_agent(monkeypatch)
        ctx = InvocationContext(session_id="pb01-test-2", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke("'; DROP TABLE slices;--", ctx=ctx)
        assert result["status"] == "error"
        assert main_node_called["value"] is False

# TEL-C2-020 — Integration: full compiled-graph invocation.
# T2-02: proves impossible/outlier telemetry values never drive a
# customer-facing breach decision or remediation action through the real
# agent.invoke() path — not just at the sla_evaluation_service unit level.
# See docs/02_design.md "Data Flow" and docs/03_test_spec.md.

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import FiveGNetworkSliceSLAMonitoringReportingAgent


class _FakeKB:
    def get_customer_thresholds(self, slice_id):
        return None

    def get_slice_type_defaults(self, slice_type):
        if slice_type == "eMBB":
            return {"latency_ms": 10.0, "throughput_mbps": 50.0, "packet_loss_pct": 1.0, "jitter_ms": 5.0}
        return None


def _compiled_agent(sla_kb=None):
    agent = FiveGNetworkSliceSLAMonitoringReportingAgent(config={"max_retry": 1, "sla_kb": sla_kb})
    agent.compile()
    return agent


def _ctx():
    return InvocationContext(session_id="test", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


class TestFullPipelineOutlierNeverDrivesBreachDecision:
    def test_impossible_latency_never_produces_critical_end_to_end(self):
        payload = json.dumps(
            {
                "kpi_records": [
                    {
                        "slice_id": "S01",
                        "slice_type": "eMBB",
                        "time_window": "2026-01-01T00:00:00Z/2026-01-01T01:00:00Z",
                        "latency_ms": 999999.0,  # physically impossible outlier
                    }
                ]
            }
        )
        agent = _compiled_agent(sla_kb=_FakeKB())
        result = agent.invoke(payload, ctx=_ctx())
        assert result["status"] == "success"
        assert "Critical" not in result["output"] or "**Critical:** 0" in result["output"]
        assert "Unassessed" in result["output"]
        assert "## Remediation Recommendations" not in result["output"]

    def test_impossible_packet_loss_never_produces_breach_or_remediation(self):
        payload = json.dumps(
            {
                "kpi_records": [
                    {
                        "slice_id": "S01",
                        "slice_type": "eMBB",
                        "time_window": "2026-01-01T00:00:00Z/2026-01-01T01:00:00Z",
                        "packet_loss_pct": 250.0,  # impossible: >100%
                    }
                ]
            }
        )
        agent = _compiled_agent(sla_kb=_FakeKB())
        result = agent.invoke(payload, ctx=_ctx())
        assert result["status"] == "success"
        assert "## Remediation Recommendations" not in result["output"]
        assert "## Findings" not in result["output"]

    def test_valid_metrics_still_produce_real_breach_end_to_end(self):
        # Control case: a genuine (non-outlier) breach must still surface
        # normally — proves the outlier quarantine doesn't over-suppress.
        payload = json.dumps(
            {
                "kpi_records": [
                    {
                        "slice_id": "S01",
                        "slice_type": "eMBB",
                        "time_window": "2026-01-01T00:00:00Z/2026-01-01T01:00:00Z",
                        "latency_ms": 50.0,  # genuinely exceeds threshold, not an outlier
                    }
                ]
            }
        )
        agent = _compiled_agent(sla_kb=_FakeKB())
        result = agent.invoke(payload, ctx=_ctx())
        assert result["status"] == "success"
        assert "**Critical:** 1" in result["output"]
        assert "## Findings" in result["output"]
        assert "## Remediation Recommendations" in result["output"]


class TestFullPipelineThresholdUnavailable:
    def test_no_sla_kb_reports_unassessed_not_ok(self):
        payload = json.dumps(
            {
                "kpi_records": [
                    {
                        "slice_id": "S01",
                        "slice_type": "eMBB",
                        "time_window": "2026-01-01T00:00:00Z/2026-01-01T01:00:00Z",
                        "latency_ms": 5.0,
                    }
                ]
            }
        )
        agent = _compiled_agent(sla_kb=None)
        result = agent.invoke(payload, ctx=_ctx())
        assert result["status"] == "success"
        assert "**OK:** 0" in result["output"]
        assert "**Unassessed:** 1" in result["output"]
        assert "Unassessed Slices" in result["output"]


class TestFullPipelineRejectsInvalidInput:
    def test_oversized_batch_never_reaches_main_node(self):
        from src.services.slice_metric_validation_service import MAX_KPI_RECORDS

        payload = json.dumps(
            {
                "kpi_records": [
                    {"slice_id": f"S{i}", "time_window": "x", "latency_ms": 1.0}
                    for i in range(MAX_KPI_RECORDS + 1)
                ]
            }
        )
        agent = _compiled_agent(sla_kb=_FakeKB())
        result = agent.invoke(payload, ctx=_ctx())
        assert result["status"] == "error"

    def test_anonymous_caller_rejected_by_trust_gate(self):
        agent = _compiled_agent(sla_kb=_FakeKB())
        payload = json.dumps({"kpi_records": [{"slice_id": "S01", "time_window": "x", "latency_ms": 1.0}]})
        result = agent.invoke(
            payload,
            ctx=InvocationContext(session_id="test", caller_trust_level=TrustLevel.ANONYMOUS),
        )
        assert result["status"] == "error"

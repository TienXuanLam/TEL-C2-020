# PB-02: SLA severity classification is deterministic threshold
# comparison, never LLM-influenced. See docs/02_design.md "Design Decision
# Record" and docs/03_test_spec.md PB-02.

import inspect

from src.services import sla_evaluation_service as svc


class FakeKB:
    def get_customer_thresholds(self, slice_id):
        return None

    def get_slice_type_defaults(self, slice_type):
        return {"latency_ms": 10.0, "throughput_mbps": 50.0}


class TestPB02DeterministicSlaClassification:
    def test_evaluate_slices_signature_has_no_llm_parameter(self):
        sig = inspect.signature(svc.evaluate_slices)
        assert "llm" not in sig.parameters

    def test_classify_metric_signature_has_no_llm_parameter(self):
        sig = inspect.signature(svc._classify_metric)
        assert "llm" not in sig.parameters

    def test_repeated_calls_produce_identical_output(self):
        record = {"slice_id": "S01", "slice_type": "eMBB", "latency_ms": 15.0}
        results = [svc.evaluate_slices([record], sla_kb=FakeKB()) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_severity_boundaries_are_fixed_not_configurable_at_runtime(self):
        # Warning margin is a fixed 10% — not passed as a runtime parameter.
        assert svc._WARNING_MARGIN_RATIO == 0.10

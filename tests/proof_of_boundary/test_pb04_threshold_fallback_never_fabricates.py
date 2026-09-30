# PB-04: threshold fallback never fabricates a threshold value — when
# neither a customer-specific threshold nor a slice-type default exists,
# the metric is excluded from evaluation rather than assigned a guessed
# bound. See docs/02_design.md "Risk mitigations" R-1 and
# docs/03_test_spec.md PB-04.

from src.services import sla_evaluation_service as svc


class EmptyKB:
    def get_customer_thresholds(self, slice_id):
        return None

    def get_slice_type_defaults(self, slice_type):
        return None


class PartialKB:
    """Only has a default for one slice_type — proves other slice_types
    correctly fall through to "unavailable" rather than reusing an
    unrelated default.
    """

    def get_customer_thresholds(self, slice_id):
        return None

    def get_slice_type_defaults(self, slice_type):
        if slice_type == "eMBB":
            return {"latency_ms": 10.0}
        return None


class TestPB04ThresholdFallbackNeverFabricates:
    def test_no_kb_at_all_produces_unavailable_source(self):
        thresholds, source = svc._load_thresholds("S01", "eMBB", None)
        assert source == "unavailable"
        assert thresholds == {}

    def test_empty_kb_produces_unavailable_source(self):
        thresholds, source = svc._load_thresholds("S01", "eMBB", EmptyKB())
        assert source == "unavailable"

    def test_unmatched_slice_type_produces_unavailable_not_reused_default(self):
        thresholds, source = svc._load_thresholds("S01", "URLLC", PartialKB())
        assert source == "unavailable"
        assert thresholds == {}

    def test_matched_slice_type_uses_its_own_default(self):
        thresholds, source = svc._load_thresholds("S01", "eMBB", PartialKB())
        assert source == "slice_type_default"
        assert thresholds["latency_ms"] == 10.0

    def test_unavailable_threshold_excludes_metric_from_breach_records(self):
        record = {"slice_id": "S01", "slice_type": "URLLC", "latency_ms": 999.0}
        breach_records, summary = svc.evaluate_slices([record], sla_kb=PartialKB())
        assert breach_records == []
        # T1-01: a slice with zero evaluated metrics is Unassessed — never
        # fabricated as OK (which would falsely imply "evaluated and
        # compliant") or as Critical/Warning without a comparable
        # threshold.
        assert summary["S01"] == svc.UNASSESSED
        assert summary["S01"] != svc.OK

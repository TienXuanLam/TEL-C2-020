# TEL-C2-020 — Unit Tests: sla_evaluation_service (MainNode step 2)
# Most correctness-critical module — deterministic severity classification.

from src.services import sla_evaluation_service as svc


class FakeKB:
    def __init__(self, customer_thresholds=None, slice_type_defaults=None):
        self._customer = customer_thresholds or {}
        self._defaults = slice_type_defaults or {}

    def get_customer_thresholds(self, slice_id):
        return self._customer.get(slice_id)

    def get_slice_type_defaults(self, slice_type):
        return self._defaults.get(slice_type)


_RECORD = {"slice_id": "S01", "slice_type": "eMBB", "latency_ms": 5.0, "throughput_mbps": 100.0,
           "packet_loss_pct": 0.01, "jitter_ms": 1.0}


class TestClassifyMetricLowerIsBetter:
    def test_ok_when_within_threshold(self):
        severity, delta = svc._classify_metric(5.0, 10.0, "lower_is_better")
        assert severity == svc.OK
        assert delta == -5.0

    def test_warning_when_within_10_percent_margin(self):
        severity, _ = svc._classify_metric(9.5, 10.0, "lower_is_better")
        assert severity == svc.WARNING

    def test_critical_when_exceeds_threshold(self):
        severity, _ = svc._classify_metric(11.0, 10.0, "lower_is_better")
        assert severity == svc.CRITICAL

    def test_exact_threshold_is_ok(self):
        severity, _ = svc._classify_metric(10.0, 10.0, "lower_is_better")
        assert severity == svc.OK


class TestClassifyMetricHigherIsBetter:
    def test_ok_when_meets_threshold(self):
        severity, _ = svc._classify_metric(100.0, 100.0, "higher_is_better")
        assert severity == svc.OK

    def test_warning_when_within_10_percent_margin(self):
        severity, _ = svc._classify_metric(105.0, 100.0, "higher_is_better")
        assert severity == svc.WARNING

    def test_critical_when_below_threshold(self):
        severity, _ = svc._classify_metric(50.0, 100.0, "higher_is_better")
        assert severity == svc.CRITICAL


class TestLoadThresholds:
    def test_customer_threshold_preferred(self):
        kb = FakeKB(customer_thresholds={"S01": {"latency_ms": 8.0}})
        thresholds, source = svc._load_thresholds("S01", "eMBB", kb)
        assert source == "customer"
        assert thresholds["latency_ms"] == 8.0

    def test_falls_back_to_slice_type_default(self):
        kb = FakeKB(slice_type_defaults={"eMBB": {"latency_ms": 20.0}})
        thresholds, source = svc._load_thresholds("S01", "eMBB", kb)
        assert source == "slice_type_default"

    def test_unavailable_when_neither_exists(self):
        kb = FakeKB()
        thresholds, source = svc._load_thresholds("S01", "eMBB", kb)
        assert source == "unavailable"
        assert thresholds == {}

    def test_no_kb_configured_unavailable(self):
        thresholds, source = svc._load_thresholds("S01", "eMBB", None)
        assert source == "unavailable"


class TestEvaluateSlices:
    def test_deterministic_no_llm_dependency(self):
        import inspect

        sig = inspect.signature(svc.evaluate_slices)
        assert "llm" not in sig.parameters
        sig2 = inspect.signature(svc._classify_metric)
        assert "llm" not in sig2.parameters

    def test_worst_case_severity_aggregation(self):
        kb = FakeKB(
            slice_type_defaults={
                "eMBB": {"latency_ms": 3.0, "throughput_mbps": 50.0, "packet_loss_pct": 1.0, "jitter_ms": 5.0}
            }
        )
        # latency 5.0 > 3.0 -> Critical; others OK
        breach_records, summary = svc.evaluate_slices([_RECORD], sla_kb=kb)
        assert summary["S01"] == svc.CRITICAL

    def test_no_threshold_metric_never_evaluated(self):
        # T1-01: a slice with zero evaluated metrics must be Unassessed,
        # never OK — "not evaluated" is not the same as "evaluated and
        # compliant".
        kb = FakeKB()  # no thresholds at all
        breach_records, summary = svc.evaluate_slices([_RECORD], sla_kb=kb)
        assert breach_records == []
        assert summary["S01"] == svc.UNASSESSED

    def test_missing_metric_value_skipped(self):
        record = {"slice_id": "S01", "slice_type": "eMBB", "latency_ms": 5.0}
        kb = FakeKB(slice_type_defaults={"eMBB": {"latency_ms": 10.0, "throughput_mbps": 50.0}})
        breach_records, _ = svc.evaluate_slices([record], sla_kb=kb)
        metrics_evaluated = {r["metric"] for r in breach_records}
        assert "throughput_mbps" not in metrics_evaluated

    def test_threshold_source_recorded_per_metric(self):
        kb = FakeKB(customer_thresholds={"S01": {"latency_ms": 3.0}})
        breach_records, _ = svc.evaluate_slices([_RECORD], sla_kb=kb)
        latency_record = next(r for r in breach_records if r["metric"] == "latency_ms")
        assert latency_record["threshold_source"] == "customer"


class TestOutlierQuarantine:
    # T1-02/T2-02: an outlier-flagged value must never drive a
    # Critical/Warning/OK decision — this is the decision boundary that
    # matters, not just whether data_quality is populated.

    def test_outlier_value_never_produces_critical_despite_far_exceeding_threshold(self):
        record = {
            "slice_id": "S01", "slice_type": "eMBB",
            "latency_ms": 99999.0,  # far outside the 0-10000 physical bound
            "data_quality": ["latency_ms out of expected range: 99999.0"],
        }
        kb = FakeKB(slice_type_defaults={"eMBB": {"latency_ms": 3.0}})
        breach_records, summary = svc.evaluate_slices([record], sla_kb=kb)
        latency_record = next(r for r in breach_records if r["metric"] == "latency_ms")
        assert latency_record["severity"] == svc.UNASSESSED
        assert latency_record["threshold_source"] == "data_quality_invalid"
        assert latency_record["sla_threshold"] is None
        assert summary["S01"] == svc.UNASSESSED

    def test_outlier_value_never_produces_ok_masking_a_real_gap(self):
        record = {
            "slice_id": "S01", "slice_type": "eMBB",
            "latency_ms": -50.0,  # outside the 0-10000 physical bound, but
            # numerically "better" than any threshold — must not be OK.
            "data_quality": ["latency_ms out of expected range: -50.0"],
        }
        kb = FakeKB(slice_type_defaults={"eMBB": {"latency_ms": 10.0}})
        breach_records, summary = svc.evaluate_slices([record], sla_kb=kb)
        latency_record = next(r for r in breach_records if r["metric"] == "latency_ms")
        assert latency_record["severity"] == svc.UNASSESSED
        assert summary["S01"] == svc.UNASSESSED

    def test_outlier_metric_excluded_from_remediation_ranking(self):
        from src.services import report_render_service

        record = {
            "slice_id": "S01", "slice_type": "eMBB",
            "latency_ms": 99999.0,
            "data_quality": ["latency_ms out of expected range: 99999.0"],
        }
        kb = FakeKB(slice_type_defaults={"eMBB": {"latency_ms": 3.0}})
        breach_records, _ = svc.evaluate_slices([record], sla_kb=kb)
        ranked = report_render_service.rank_remediation_actions(breach_records)
        assert ranked == []

    def test_one_outlier_metric_does_not_block_evaluation_of_other_metrics(self):
        record = {
            "slice_id": "S01", "slice_type": "eMBB",
            "latency_ms": 99999.0,  # outlier — quarantined
            "throughput_mbps": 10.0,  # genuinely bad, not an outlier
            "data_quality": ["latency_ms out of expected range: 99999.0"],
        }
        kb = FakeKB(slice_type_defaults={"eMBB": {"latency_ms": 3.0, "throughput_mbps": 50.0}})
        breach_records, summary = svc.evaluate_slices([record], sla_kb=kb)
        throughput_record = next(r for r in breach_records if r["metric"] == "throughput_mbps")
        assert throughput_record["severity"] == svc.CRITICAL
        # Overall slice severity is still driven by the genuine breach.
        assert summary["S01"] == svc.CRITICAL

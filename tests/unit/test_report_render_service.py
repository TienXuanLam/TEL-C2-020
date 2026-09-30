# TEL-C2-020 — Unit Tests: report_render_service (PostProcessNode step 3)

from src.services import report_render_service as svc

_BREACH_CRITICAL = {"slice_id": "S01", "metric": "latency_ms", "measured": 15.0,
                     "sla_threshold": 10.0, "delta": 5.0, "severity": "Critical",
                     "threshold_source": "customer"}
_BREACH_WARNING = {"slice_id": "S02", "metric": "jitter_ms", "measured": 4.5,
                    "sla_threshold": 5.0, "delta": -0.5, "severity": "Warning",
                    "threshold_source": "slice_type_default"}
_BREACH_OK = {"slice_id": "S03", "metric": "throughput_mbps", "measured": 100.0,
              "sla_threshold": 50.0, "delta": 50.0, "severity": "OK",
              "threshold_source": "customer"}
_BREACH_WARNING_THROUGHPUT = {"slice_id": "S04", "metric": "throughput_mbps", "measured": 45.0,
                               "sla_threshold": 50.0, "delta": -5.0, "severity": "Warning",
                               "threshold_source": "customer"}


class FakeLLM:
    def complete(self, prompt, **kwargs) -> str:
        return "Prioritize latency_ms remediation first given Critical severity."


class TestRankRemediationActions:
    def test_ok_records_excluded_from_ranking(self):
        ranked = svc.rank_remediation_actions([_BREACH_OK])
        assert ranked == []

    def test_critical_ranked_above_warning(self):
        ranked = svc.rank_remediation_actions([_BREACH_WARNING, _BREACH_CRITICAL])
        assert ranked[0]["metric"] == "latency_ms"
        assert ranked[0]["max_severity"] == "Critical"

    def test_affected_slice_count_deduplicated(self):
        breach2 = {**_BREACH_CRITICAL, "slice_id": "S04"}
        ranked = svc.rank_remediation_actions([_BREACH_CRITICAL, breach2])
        assert ranked[0]["affected_slice_count"] == 2

    def test_empty_breach_list_produces_empty_ranking(self):
        assert svc.rank_remediation_actions([]) == []


class TestDraftRemediationNarrative:
    def test_no_breaches_no_action_message(self):
        narrative = svc.draft_remediation_narrative([], llm=None)
        assert "No breaches" in narrative

    def test_no_llm_uses_deterministic_fallback(self):
        ranked = svc.rank_remediation_actions([_BREACH_CRITICAL])
        narrative = svc.draft_remediation_narrative(ranked, llm=None)
        assert "latency_ms" in narrative

    def test_llm_configured_generates_narrative(self):
        ranked = svc.rank_remediation_actions([_BREACH_CRITICAL])
        narrative = svc.draft_remediation_narrative(ranked, llm=FakeLLM())
        assert narrative == "Prioritize latency_ms remediation first given Critical severity."

    def test_llm_failure_falls_back(self):
        class BrokenLLM:
            def complete(self, prompt, **kwargs):
                raise RuntimeError("LLM unavailable")

        ranked = svc.rank_remediation_actions([_BREACH_CRITICAL])
        narrative = svc.draft_remediation_narrative(ranked, llm=BrokenLLM())
        assert "latency_ms" in narrative


class TestDraftRemediationNarrativeAdversarial:
    # T1-03/T2-03: a customer-facing LLM narrative that omits a ranked
    # metric, fabricates unrelated content, or drifts off-topic must never
    # reach the report as-is — it falls back to the deterministic template
    # built directly from the fixed ranked list.

    def test_narrative_omitting_a_ranked_metric_falls_back(self):
        class OmitsMetricLLM:
            def complete(self, prompt, **kwargs) -> str:
                return "Everything looks fine, no action needed."

        breaches = [_BREACH_CRITICAL, _BREACH_WARNING_THROUGHPUT]
        ranked = svc.rank_remediation_actions(breaches)
        narrative = svc.draft_remediation_narrative(ranked, llm=OmitsMetricLLM())
        assert "latency_ms" in narrative
        assert "throughput_mbps" in narrative
        assert narrative == "\n".join(f"{r['metric']}: {r['recommendation']}" for r in ranked)

    def test_fabricated_unrelated_content_falls_back(self):
        class FabricatingLLM:
            def complete(self, prompt, **kwargs) -> str:
                return "We will issue a full SLA credit and dispatch a technician tomorrow."

        ranked = svc.rank_remediation_actions([_BREACH_CRITICAL])
        narrative = svc.draft_remediation_narrative(ranked, llm=FabricatingLLM())
        assert "SLA credit" not in narrative
        assert "latency_ms" in narrative

    def test_empty_llm_response_falls_back(self):
        class EmptyLLM:
            def complete(self, prompt, **kwargs) -> str:
                return ""

        ranked = svc.rank_remediation_actions([_BREACH_CRITICAL])
        narrative = svc.draft_remediation_narrative(ranked, llm=EmptyLLM())
        assert "latency_ms" in narrative

    def test_grounded_narrative_mentioning_all_metrics_is_used(self):
        class GroundedLLM:
            def complete(self, prompt, **kwargs) -> str:
                return (
                    "The latency_ms breach is the most urgent, followed by the "
                    "throughput_mbps warning — address both per the ranked actions."
                )

        breaches = [_BREACH_CRITICAL, _BREACH_WARNING_THROUGHPUT]
        ranked = svc.rank_remediation_actions(breaches)
        llm = GroundedLLM()
        narrative = svc.draft_remediation_narrative(ranked, llm=llm)
        assert narrative == llm.complete("")


class TestBuildInternalReport:
    def test_watermark_always_present(self):
        report = svc.build_internal_report([], [], [], {}, [])
        assert report["watermark"] == svc.DRAFT_WATERMARK

    def test_severity_counts_computed(self):
        report = svc.build_internal_report(
            [], [], [_BREACH_CRITICAL], {"S01": "Critical", "S02": "OK"}, []
        )
        assert report["severity_counts"]["Critical"] == 1
        assert report["severity_counts"]["OK"] == 1


class TestRenderCustomerMarkdown:
    def test_watermark_present_in_markdown(self):
        report = svc.build_internal_report([], [], [], {}, [])
        markdown = svc.render_customer_markdown(report, "no narrative")
        assert svc.DRAFT_WATERMARK in markdown

    def test_findings_table_present_when_breaches_exist(self):
        report = svc.build_internal_report(
            [], [], [_BREACH_CRITICAL], {"S01": "Critical"}, []
        )
        markdown = svc.render_customer_markdown(report, "narrative")
        assert "S01" in markdown
        assert "latency_ms" in markdown

    def test_no_findings_table_when_all_ok(self):
        report = svc.build_internal_report([], [], [_BREACH_OK], {"S03": "OK"}, [])
        markdown = svc.render_customer_markdown(report, "narrative")
        assert "## Findings" not in markdown

    def test_data_quality_notes_rendered(self):
        report = svc.build_internal_report(
            [], [{"slice_id": "S01", "error": "missing loss_amount"}], [], {}, []
        )
        markdown = svc.render_customer_markdown(report, "narrative")
        assert "Data Quality Notes" in markdown

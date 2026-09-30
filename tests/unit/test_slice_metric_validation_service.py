# TEL-C2-020 — Unit Tests: slice_metric_validation_service (PreProcessNode step 1)

from src.services import slice_metric_validation_service as svc

_VALID_RECORD = {
    "slice_id": "S01",
    "slice_type": "eMBB",
    "time_window": "2026-01-01T00:00:00Z/2026-01-01T01:00:00Z",
    "latency_ms": 5.2,
    "throughput_mbps": 100.0,
    "packet_loss_pct": 0.01,
    "jitter_ms": 1.5,
}


class TestValidateTopLevelSchema:
    def test_valid_payload_passes(self):
        assert svc.validate_top_level_schema({"kpi_records": [_VALID_RECORD]}) == []

    def test_missing_kpi_records_rejected(self):
        errors = svc.validate_top_level_schema({})
        assert any("kpi_records" in e for e in errors)

    def test_non_list_kpi_records_rejected(self):
        errors = svc.validate_top_level_schema({"kpi_records": "not-a-list"})
        assert any("kpi_records" in e for e in errors)


class TestValidateAndNormalizeRecords:
    def test_complete_record_normalized(self):
        records, errors = svc.validate_and_normalize_records([_VALID_RECORD])
        assert len(records) == 1
        assert records[0]["slice_id"] == "S01"
        assert errors == []

    def test_missing_slice_id_excluded(self):
        record = {k: v for k, v in _VALID_RECORD.items() if k != "slice_id"}
        records, errors = svc.validate_and_normalize_records([record])
        assert records == []
        assert "missing required fields" in errors[0]["error"]

    def test_missing_time_window_excluded(self):
        record = {k: v for k, v in _VALID_RECORD.items() if k != "time_window"}
        records, errors = svc.validate_and_normalize_records([record])
        assert records == []

    def test_no_kpi_metric_excluded(self):
        record = {"slice_id": "S01", "time_window": "x"}
        records, errors = svc.validate_and_normalize_records([record])
        assert records == []
        assert "no KPI metric present" in errors[0]["error"]

    def test_at_least_one_metric_sufficient(self):
        record = {"slice_id": "S01", "time_window": "x", "latency_ms": 5.0}
        records, errors = svc.validate_and_normalize_records([record])
        assert len(records) == 1


class TestUnitNormalization:
    def test_latency_ms_used_directly(self):
        records, _ = svc.validate_and_normalize_records([_VALID_RECORD])
        assert records[0]["latency_ms"] == 5.2

    def test_latency_us_converted_to_ms(self):
        record = {**_VALID_RECORD, "latency_ms": None, "latency_us": 5200}
        records, _ = svc.validate_and_normalize_records([record])
        assert records[0]["latency_ms"] == 5.2

    def test_throughput_kbps_converted_to_mbps(self):
        record = {**_VALID_RECORD, "throughput_mbps": None, "throughput_kbps": 100000}
        records, _ = svc.validate_and_normalize_records([record])
        assert records[0]["throughput_mbps"] == 100.0

    def test_packet_loss_computed_from_counts(self):
        record = {**_VALID_RECORD, "packet_loss_pct": None, "packets_sent": 1000, "packets_lost": 5}
        records, _ = svc.validate_and_normalize_records([record])
        assert records[0]["packet_loss_pct"] == 0.5

    def test_jitter_us_converted_to_ms(self):
        record = {**_VALID_RECORD, "jitter_ms": None, "jitter_us": 1500}
        records, _ = svc.validate_and_normalize_records([record])
        assert records[0]["jitter_ms"] == 1.5

    def test_missing_metric_stays_none(self):
        record = {"slice_id": "S01", "time_window": "x", "latency_ms": 5.0}
        records, _ = svc.validate_and_normalize_records([record])
        assert records[0]["throughput_mbps"] is None


class TestDataQualityFlagging:
    def test_outlier_flagged_not_dropped(self):
        record = {**_VALID_RECORD, "latency_ms": 99999}
        records, _ = svc.validate_and_normalize_records([record])
        assert len(records) == 1
        assert any("latency_ms" in flag for flag in records[0]["data_quality"])

    def test_normal_value_no_flag(self):
        records, _ = svc.validate_and_normalize_records([_VALID_RECORD])
        assert records[0]["data_quality"] == []

# Test Specification

## Test Strategy
- Coverage target: 100% of domain branches in `src/services/` and `src/nodes/`
- Test types: Unit (per service module + per node) / Proof-of-Boundary (framework contract + deterministic-evaluation + DRAFT-watermark guarantees)

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result |
|-------|------|----------------|
| TC-01 | State contract: flat TypedDict, no Pydantic/dataclass | `State` fields are `Optional[str]` primitives only |
| TC-02 | `required_trust_level` enforced on all 3 real nodes | Declaration: `PreProcessNode`, `MainNode`, `PostProcessNode` all declare `TrustLevel.VERIFIED_EXTERNAL`. Enforcement: each node's `test_trust_gate_blocks_anonymous` calls the real `__call__()` gate entry point (not `execute()`) with `caller_trust_level=ANONYMOUS` and asserts `status=error` |
| TC-03 | S-1 domain schema validation (PreProcessNode) | Invalid payload (missing `kpi_records`, non-list `kpi_records`, batch exceeding `MAX_KPI_RECORDS`) → `status=error` with descriptive `error_log`, never silent pass-through |
| TC-04 | S-2: functional input gate (PreProcessNode) | `_extra_security_gate_input()` rejects a raw payload exceeding `MAX_RAW_INPUT_CHARS` directly (sets `status=error`/`error_log` inside the hook, per `FunctionNode`'s S-2 contract) — not merely an unused annotation flag (T1-04 fix) |
| TC-05 | S-3: `_extra_security_gate_output()` non-trivial (PostProcessNode) | DRAFT-watermark re-check executes on `result`/`formatted_output`; hook exists, `@final` gate not overridden directly; may raise per `customize-security-gates.md` contract |
| TC-06 | S-4: at least one domain `emit_trace_event()` per `execute()` | `PreProcessNode` emits `kpi_batch_validated`; `MainNode` emits `sla_evaluated`; `PostProcessNode` emits `report_rendered` |
| TC-07 | ⚠️ Deterministic SLA evaluation + DRAFT watermark authority — domain business logic | See "SLA Evaluation & Watermark Tests" below — these are the template's core correctness guarantee |

## SLA Evaluation & Watermark Tests (domain business logic — TC-07 detail)

| Case | Input | Expected Result |
|---|---|---|
| OK classification (lower-is-better metric) | `measured <= threshold` | `severity="OK"` |
| Warning classification (lower-is-better metric) | `threshold * 0.9 < measured <= threshold` | `severity="Warning"` |
| Critical classification (lower-is-better metric) | `measured > threshold` | `severity="Critical"` |
| OK classification (higher-is-better metric, throughput) | `measured >= threshold` | `severity="OK"` |
| Warning classification (higher-is-better metric) | `threshold <= measured < threshold * 1.1` | `severity="Warning"` |
| Critical classification (higher-is-better metric) | `measured < threshold` | `severity="Critical"` |
| Customer-specific threshold used when available | `sla_kb.get_customer_thresholds(slice_id)` returns data | `threshold_source="customer"` |
| Slice-type default fallback | No customer threshold, `sla_kb.get_slice_type_defaults(slice_type)` returns data | `threshold_source="slice_type_default"` |
| No threshold source available | Neither customer nor slice-type default found | `threshold_source="unavailable"`; metric not evaluated (absent from `breach_records` for that slice/metric) — never guessed |
| Slice with zero evaluated metrics | No threshold available for any present metric | `slice_severity_summary[slice_id] == "Unassessed"` — never `"OK"` (T1-01 fix); surfaced prominently in both `severity_counts["Unassessed"]` and a dedicated Markdown section, excluded from the OK count |
| Worst-case severity aggregation | Slice has mixed OK/Warning/Critical across metrics | `slice_severity_summary[slice_id]` reflects the single worst severity |
| Missing metric handling | A KPI field is null/absent in the record | That metric is skipped for evaluation (not a fabricated OK/Critical) |
| Data quality flag never drops a record | A metric value is outside expected physical range | Record still proceeds to evaluation with a `data_quality` annotation, never excluded |
| Outlier-flagged metric never drives a decision | A metric value is outside expected physical range (`data_quality` flagged) | The metric's `breach_records` entry is `severity="Unassessed"`, `threshold_source="data_quality_invalid"` — never compared against a threshold, never `Critical`/`Warning`/`OK`, and excluded from remediation ranking (T1-02 fix). Other, non-outlier metrics on the same slice are still evaluated normally |
| Quarantined record (missing slice_id/time_window/no metrics) | Record fails required-field or has-any-metric check | Excluded from `normalized_kpi_records`; reported in `validation_errors`; never proceeds to evaluation |
| Every report carries the DRAFT watermark | Any invocation producing a report | Both `result` (JSON) and `formatted_output` (Markdown) contain `"DRAFT — Requires NOC Review"` |
| Remediation ranking by impact | Multiple breached metrics with different severities/slice counts | Ranked list ordered by `(max_severity, affected_slice_count)` descending |
| LLM remediation narrative factually grounded | LLM output omits a ranked metric, fabricates unrelated commitments, or is empty | Falls back to the deterministic template (`f"{metric}: {recommendation}"` per ranked action) — never reaches the customer-facing report as-is unless it mentions every ranked metric by name (T1-03 fix) |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result |
|-------|----------|------|----------------|
| PB-01 | S-1 schema injection resistance | Malformed payload (missing `kpi_records`, non-list `kpi_records`, non-JSON injection) attempting to bypass validation. Verified via `node(state)` (`__call__()`), not `execute()` directly, plus a full compiled-graph test with a `MainNode.execute()` spy proving it is never invoked | `PreProcessNode` rejects with `status=error` at every level; no record reaches MainNode |
| PB-02 | SLA severity classification is deterministic, not LLM-influenced | `sla_evaluation_service.evaluate_slices()`/`_classify_metric()` called directly (no `llm` argument in either signature) | Deterministic output — identical on repeated calls with the same input |
| PB-03 | DRAFT watermark is enforced at the code level, not just documented | End-to-end: `PostProcessNode` output always carries the watermark, even with a real LLM configured for remediation narrative drafting; adversarial LLM responses that omit the breached metric or fabricate commitments (T1-03) | S-3 `_extra_security_gate_output()` raises if the watermark is ever missing from `result` or `formatted_output`; ungrounded/fabricated LLM narratives never reach `formatted_output` — the deterministic fallback is used instead |
| PB-04 | Threshold resolution provenance — fallback never fabricates a threshold | Slice with no customer threshold and no matching slice-type default | `threshold_source="unavailable"`; the metric is absent from `breach_records`; the slice's overall severity is `"Unassessed"`, never `"OK"` (T1-01 fix). Also verifies the customer→slice-type-default fallback order via `_load_thresholds()` directly |
| PB-05 (integration) | Full compiled-graph invocation | `tests/integration/test_graph.py` — impossible/outlier telemetry (latency, packet loss) via `agent.invoke()` never produces a Critical/Warning finding or a remediation recommendation; threshold-unavailable slices report `Unassessed`, not `OK`; a genuine (non-outlier) breach still surfaces normally; malformed/oversized input never reaches MainNode | All pass; proves the T1-01/T1-02 guarantees hold through the real `agent.invoke()` entry point, not just at the service unit level |

## Test Execution Summary
- Execution date: local run, 2026-07-15 (see CI pipeline for the authoritative reviewed-SHA run)
- Total tests: 111 (109 unit/PB/integration + 2 PB-7 auto-skip)
- Pass: 109 / Fail: 0 / Skip: 2 (PB-7, non-HITL — this template does not set `hitl.enabled: true`)
- Coverage: not separately measured locally; the "100% of domain branches" target is enforced by CI's `run-tests` job, not re-attested by hand here

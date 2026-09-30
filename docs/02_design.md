# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `FiveGNetworkSliceSLAMonitoringReportingAgent`
  (Python identifier — cannot start with a digit; display name/`agent.yaml`
  `name` field is `5GNetworkSliceSLAMonitoringReportingAgent`)
- **L1 Base**: `AgentBaseGraph`
- **Pattern**: Cat 2 — flat backbone (outer `AgentBaseGraph`, no inner subgraph); DocGenerationAgent
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

The source proposal's 3-node design (`SliceMetricValidationNode`,
`SLAEvaluationNode`, `ReportRenderNode`) maps **directly** onto
`AgentBaseGraph`'s 3 domain slots — no step-helper reconciliation pattern is
needed here, unlike some other templates (which had to fold a 5-8 node
architect spec into 3 slots).

```
AgentBaseGraph (src/graph/graph.py):
  InitializeNode      [framework default]
  PreProcessNode      [VERIFIED_EXTERNAL — real FunctionNode, real S-1/S-2 gate]
    = SliceMetricValidationNode: schema validation, unit normalization
      (kbps->Mbps, μs->ms, raw packet count->%), data_quality flagging
  MainNode            [VERIFIED_EXTERNAL — real FunctionNode]
    = SLAEvaluationNode: load thresholds (customer-specific first, slice-type
      default fallback), compare metrics, classify per-metric severity,
      compute worst-case severity per slice — fully deterministic, no LLM
  PostProcessNode     [VERIFIED_EXTERNAL — real FunctionNode, real S-3 gate]
    = ReportRenderNode: rank remediation actions, render JSON (ITSM) +
      Markdown (customer-facing) reports, apply DRAFT watermark (Risk R-6),
      LLM used only for remediation narrative text
  FinalizeNode        [framework default]
```

### Node Configuration

| Slot (real `FunctionNode`) | Location | required_trust_level | Maps to source proposal |
|---|---|---|---|
| `pre_process` | src/nodes/pre_process_node.py | VERIFIED_EXTERNAL | `SliceMetricValidationNode` (§4 Step 1) |
| `main` | src/nodes/main_node.py | VERIFIED_EXTERNAL | `SLAEvaluationNode` (§4 Step 2) |
| `post_process` | src/nodes/post_process_node.py | VERIFIED_EXTERNAL | `ReportRenderNode` (§4 Step 3) |

Domain logic lives in `src/services/` (pure Python, no `agenticstar`/framework
security-gate imports), called from each node's `execute()`.

### Step Detail

| Step | Service module | Responsibility |
|---|---|---|
| 1 | `slice_metric_validation_service.py` | Schema validation (slice_id, time_window, ≥1 KPI metric required); unit normalization (kbps→Mbps, μs→ms, raw packet count→%); null/outlier records flagged with `data_quality` annotation, **never dropped** (source proposal §4 Step 1, Risk R-2) |
| 2 | `sla_evaluation_service.py` | For each slice: load thresholds from `sla_kb` (customer-specific first, slice-type default fallback — source proposal Risk R-1); compare latency/throughput/packet_loss/jitter against SLA bounds; compute margin delta; classify per-metric severity (`Critical` if breach, `Warning` if within 10% of threshold, `OK` otherwise); aggregate worst-case severity per slice. **Deterministic — no LLM call**. Threshold resolution is factored into its own function (`_load_thresholds()`), separately unit- and PB-tested (`test_pb04_threshold_fallback_never_fabricates.py`) — it is **not** a separate `FunctionNode`/graph slot, since the source proposal's ASCII diagram and Step Table define exactly 3 nodes and do not name threshold resolution as an independent step |
| 3 | `report_render_service.py` | Rank remediation actions by severity × affected-slice count; render `violation_report.json` (ITSM-ready, full breach evidence) and `violation_report.md` (customer-facing, findings table + severity summary + numbered remediation); apply `[DRAFT — Requires NOC Review]` watermark to every report by default (source proposal Risk R-6 — mandatory, not configurable-off at template level); LLM used only for remediation narrative drafting, grounded in the breach records and an optional `sla_kb` remediation-context section |

### Data Flow

```
START
  → InitializeNode
  → PreProcessNode (VERIFIED_EXTERNAL, real S-1/S-2 gate)
       SliceMetricValidationNode
         → normalized_kpi_records (JSON-str), validation_errors (JSON-str)
  → MainNode (VERIFIED_EXTERNAL)
       SLAEvaluationNode
         → breach_records (JSON-str) — per metric per slice:
             {slice_id, metric, measured, sla_threshold, delta, severity,
              threshold_source: "customer"|"slice_type_default"}
         → slice_severity_summary (JSON-str) — worst-case severity per slice
  → PostProcessNode (VERIFIED_EXTERNAL, real S-3 gate)
       ReportRenderNode
         → result (JSON-str, violation_report.json content),
           formatted_output (Markdown, violation_report.md content) —
           both watermarked "[DRAFT — Requires NOC Review]"
       S-4 audit log: slice count, severity counts (Critical/Warning/OK),
         data_quality flag count, threshold_source distribution
  → FinalizeNode
END
```

> **Scope boundary** (source proposal §4): real-time streaming ingestion,
> automatic remediation execution, SLA contract management, billing/penalty
> calculation, and network configuration changes are explicitly out of
> scope. The agent evaluates a single batch of KPI records for one
> measurement window per invocation; multiple slices are processed within
> that single batch.

> **DRAFT watermark guarantee** (source proposal Risk R-6, High severity):
> every report artifact carries `[DRAFT — Requires NOC Review]` by default.
> Unlike other templates' `interrupt()`-based HITL gate, this agent has **no
> technical HITL checkpoint** — the proposal explicitly documents human
> review as "a documented deployment requirement, not an optional step",
> i.e. an operational/process control outside the agent's runtime, not a
> LangGraph suspend/resume cycle. The watermark is the agent's structural
> contribution to that control: it is a hard-coded default in
> `report_render_service.py`, re-verified independently by the S-3
> `_extra_security_gate_output()` hook in `PostProcessNode`, and removing
> it requires an explicit deployment-side post-processing step (documented
> in `docs/07_operation_guide.md`), not an agent config flag.

## State Definition

File: `src/schemas/state.py` — extends `AgentState` (flat TypedDict, ADR-005).

| Field | Type | Producer (step) | Consumer | ADR-005 note |
|---|---|---|---|---|
| `normalized_kpi_records` | `Optional[str]` | 1. SliceMetricValidationNode | 2 | **JSON-str** — list of `{slice_id, slice_type, time_window, latency_ms, throughput_mbps, packet_loss_pct, jitter_ms, data_quality}` |
| `validation_errors` | `Optional[str]` | 1. SliceMetricValidationNode | PostProcessNode output | **JSON-str** — list of `{slice_id, error}`; empty list if clean |
| `breach_records` | `Optional[str]` | 2. SLAEvaluationNode | 3 | **JSON-str** — list of `{slice_id, metric, measured, sla_threshold, delta, severity, threshold_source}` |
| `slice_severity_summary` | `Optional[str]` | 2. SLAEvaluationNode | 3 | **JSON-str** — `{slice_id: worst_case_severity}` |

> **State Constraints (mandatory):**
> - Flat TypedDict only — no Pydantic, dataclass, or arbitrary objects (msgpack incompatible)
> - No credentials/JWT/API keys in state (checkpoint DB leakage risk)
> - `InvocationContext` accessed via `InvocationContext.from_state(state)` inside
>   `execute()` only — never stored in state, never constructed directly in a node
> - Dict/list fields: `Optional[str]` + `json.dumps` (producer) / `json.loads`
>   (consumer) [ADR-005]
> - Slice KPI data contains no PII (source proposal §10 Angle 5) — only
>   network performance metrics and slice/customer identifiers. SLA
>   threshold data may be commercially sensitive (Risk, §10 Angle 5) — never
>   logged in plaintext in S-4 audit events beyond aggregate counts

## Security Design

| Gate | Location | Level | Implementation |
|---|---|---|---|
| S-1 | PreProcessNode (real gate) + domain check | VERIFIED_EXTERNAL | Framework S-1 trust gate on `__call__()`; domain-level check inside `execute()`: `slice_id`, `time_window`, and at least one KPI metric required per record |
| S-2 | PreProcessNode | VERIFIED_EXTERNAL | `_extra_security_gate_input()` — raw-length sanity check on `user_input` before JSON/CSV parsing (same pattern as prior templates). No PII in slice KPI payloads (source proposal §10 Angle 5) — no domain PII scan needed beyond the framework default |
| S-3 | PostProcessNode (real gate) | VERIFIED_EXTERNAL | `_extra_security_gate_output()` — confirms every report artifact (`result`, `formatted_output`) carries the `[DRAFT — Requires NOC Review]` watermark; raises if a report is ever missing it (belt-and-suspenders re-check of Risk R-6, independent of the render-time default) |
| S-4 | All domain nodes | — | `emit_trace_event()` in every `execute()`: `kpi_batch_validated` (PreProcessNode), `sla_evaluated` (MainNode), `report_rendered` (PostProcessNode) — payload includes aggregate counts (slice count, severity counts, data_quality flag count), never raw customer-identifying detail beyond slice_id |

> **S-2/S-3 hook rule (ADR-017):** All three real slot nodes are `FunctionNode`
> subclasses. Framework `@final` gates run automatically. Extend via
> `_extra_security_gate_input()` / `_extra_security_gate_output()` only.
> **MUST NOT override `_security_gate_input()` or `_security_gate_output()`
> directly** — raises `TypeError` at class definition.

> **S-4 emit rule:** Do NOT emit `node_start` / `node_complete` / `node_error`
> — `BaseNode.__call__()` emits these automatically. Custom events only.

### Risk mitigations — design decisions (source proposal §11 R-1 to R-7)

1. **R-1 (Medium) — SLA KB incomplete at initial deployment.** `sla_evaluation_service.py`
   falls back to 3GPP TS 28.530 slice-type defaults when no customer-specific
   threshold exists; every `breach_records` entry carries
   `threshold_source: "customer"|"slice_type_default"` so operators can
   identify which evaluations used a fallback (never silently treated as
   equally authoritative).
2. **R-2 (Medium) — input data quality degradation.** `SliceMetricValidationNode`
   flags null/outlier records with `data_quality` annotations rather than
   dropping them — the record still flows through evaluation, and the
   annotation surfaces in the final report for operator awareness.
3. **R-3 (Medium) — generic remediation recommendations.** Out of scope for
   the initial build's deterministic logic; `report_render_service.py`
   passes an optional `sla_kb` remediation-context section into the LLM
   prompt when configured, but no code-level guarantee of operator-specific
   tailoring is claimed — documented as a KB-population quality concern in
   `docs/07_operation_guide.md`.
4. **R-4 (Low) — large slice populations (500+) exceed latency target.**
   Out of scope for this initial build — single-batch synchronous
   processing; documented size assumption (≤200 slices) in the operation
   guide, matching source proposal §10 Angle 4.
5. **R-5 (Medium) — composite/time-window SLA definitions.** Out of scope
   for the initial build — this implementation evaluates per-measurement
   comparisons only (source proposal's "per-measurement mode"); time-window
   aggregated evaluation mode is not implemented and is documented as a
   known limitation, not silently approximated.
6. **R-6 (High) — customer-facing report error creates contractual risk.**
   Enforced structurally: **every** report is watermarked
   `[DRAFT — Requires NOC Review]` by default, verified independently by
   the S-3 gate. See "DRAFT watermark guarantee" above.
7. **R-7 (Low) — standards evolution (3GPP/GSMA KPI definitions).** All
   thresholds and slice-type defaults live in the configurable `sla_kb`,
   never hardcoded in `sla_evaluation_service.py` logic — a KB update does
   not require a code change.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle) — via `InvocationContext.from_state(state)`
- [x] ConnectionPolicy (retry/timeout strategy) — for the single LLM call (remediation narrative) in PostProcessNode
- [x] SecurityViolationError — raised by framework S-1 trust gate on insufficient `caller_trust_level`
- [x] S-2: `_extra_security_gate_input()` — raw-length sanity check in PreProcessNode
- [x] S-3: `_extra_security_gate_output()` — DRAFT watermark re-check in PostProcessNode
- [x] S-4: `emit_trace_event()` — at least one domain-specific event in each `execute()`

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` / `RemoteAgentNode` → deliberate no-op (upstream or remote node's gate already applied)
> - Custom `BaseNode` subclass → must implement `_security_gate_input()` and
>   `_security_gate_output()` directly (`@abstractmethod` — omission raises `TypeError` at instantiation)

### Composition Pattern

- **Pattern**: Standalone (no GraphNode/RemoteAgentNode) — 3 domain
  services in `src/services/`, called directly from the 3 real `FunctionNode`s
- **L1 Base**: `AgentBaseGraph`
- **Error propagation strategy**: propagate — malformed top-level payload
  is a system error (`status=ERROR`); individual record data-quality issues
  are annotated in-band (`data_quality` flag), never raised as exceptions

## Import Isolation Confirmation

- [x] Template does NOT import `agenticstar-platform` SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only
- [x] No `agents/base/` imports (L1 direct inheritance — no L2)

## Class Name Consistency

| Artifact | Value |
|---|---|
| `src/graph/graph.py` | `class FiveGNetworkSliceSLAMonitoringReportingAgent(AgentBaseGraph)` |
| `config/agent.yaml` `class:` | `"FiveGNetworkSliceSLAMonitoringReportingAgent"` |
| `config/agent.yaml` `name:` / `src/graph/graph.py` `name` property | `"5GNetworkSliceSLAMonitoringReportingAgent"` (display name — Python identifier constraint does not apply to strings) |
| `src/api/server.py` | `from src.graph.graph import FiveGNetworkSliceSLAMonitoringReportingAgent` |

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Cat 2 fixed pipeline; deterministic evaluation, single pass, no autonomous loop needed |
| Node realization | 3 real `FunctionNode`s directly | Step-helper pattern (5→3 style) | 3 real `FunctionNode`s directly | Source proposal's 3-node design already matches the 3 domain slots exactly — no step-collapsing reconciliation needed, unlike some other fleet templates whose proposals used more steps than the fixed pipeline has slots |
| SLA breach decision authority | LLM classifies severity | Deterministic threshold comparison; LLM only drafts remediation narrative | Deterministic comparison | Severity classification (Critical/Warning/OK) must be exact and reproducible for contractual/ITSM purposes — not subject to LLM variance |
| Missing customer threshold handling | Reject the slice (no evaluation) | Fall back to slice-type default, tag `threshold_source` | Fallback with tagging | Source proposal Risk R-1 — silently rejecting leaves the slice unmonitored; a tagged fallback keeps evaluation running while flagging reduced confidence |
| DRAFT watermark | Configurable per-invocation flag | Hard-coded default, S-3-verified | Hard-coded default | Source proposal Risk R-6 (High) — customer-facing report errors are a contractual liability; the watermark must not be accidentally disabled by a config value |
| Python class name for "5G..." | Keep literal `5G...` (invalid identifier) | `FiveG...` prefix | `FiveG...` prefix | Python class/identifier names cannot start with a digit; `agent.yaml` `name:`/display name retains `5G...` for human readability since it is a string, not an identifier |

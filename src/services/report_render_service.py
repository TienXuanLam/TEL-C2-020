"""AgentCore Platform v1.0 — step 3: ReportRenderNode.

Ranks remediation actions, renders the internal JSON (ITSM-ready) and
customer-facing Markdown violation reports, and applies the mandatory
DRAFT watermark. Called from
src/nodes/post_process_node.py::PostProcessNode.execute(). See
docs/02_design.md "DRAFT watermark guarantee" and source proposal
EngTemplate_TEL-C2-020.md §4 Step 3, §11 Risk R-6.
"""

from __future__ import annotations

from typing import Any

DRAFT_WATERMARK = "DRAFT — Requires NOC Review"

_SEVERITY_RANK = {"Critical": 2, "Warning": 1, "OK": 0, "Unassessed": -1}

_DEFAULT_REMEDIATIONS = {
    "latency_ms": "Check backhaul capacity and routing path for the affected slice.",
    "throughput_mbps": "Verify RAN resource allocation and check for congestion on the slice.",
    "packet_loss_pct": "Inspect transport link quality and check for buffer overflow on network elements.",
    "jitter_ms": "Review QoS scheduling configuration and check for jitter buffer misconfiguration.",
}


def rank_remediation_actions(breach_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank remediation actions by severity x affected-slice count
    (highest-impact first), per source proposal §4 Step 3.
    """
    breaches_by_metric: dict[str, dict[str, Any]] = {}
    for record in breach_records:
        # T1-01/T1-02: neither a genuinely OK metric nor an
        # Unassessed one (threshold-unavailable, or a data-quality-invalid
        # outlier that was quarantined from evaluation) may drive a
        # remediation recommendation — only confirmed Warning/Critical
        # breaches are actionable.
        if record["severity"] in ("OK", "Unassessed"):
            continue
        metric = record["metric"]
        entry = breaches_by_metric.setdefault(
            metric, {"metric": metric, "affected_slices": set(), "max_severity": "OK"}
        )
        entry["affected_slices"].add(record["slice_id"])
        if _SEVERITY_RANK[record["severity"]] > _SEVERITY_RANK[entry["max_severity"]]:
            entry["max_severity"] = record["severity"]

    ranked = []
    for metric, entry in breaches_by_metric.items():
        ranked.append(
            {
                "metric": metric,
                "affected_slice_count": len(entry["affected_slices"]),
                "max_severity": entry["max_severity"],
                "recommendation": _DEFAULT_REMEDIATIONS.get(metric, "Investigate root cause for this metric breach."),
            }
        )

    ranked.sort(key=lambda r: (_SEVERITY_RANK[r["max_severity"]], r["affected_slice_count"]), reverse=True)
    return ranked


def draft_remediation_narrative(ranked_remediations: list[dict[str, Any]], llm: object | None = None) -> str:
    """LLM-assisted remediation narrative — never the authoritative
    severity/breach decision, only prose framing (source proposal §11 R-3).

    T1-03: this narrative is embedded verbatim in the customer-facing
    Markdown report, so an LLM response is only used if it stays grounded
    in the actual ranked_remediations — it must reference every metric
    that was actually ranked, and it must not be empty. Anything else
    (invented actions, omitted metrics, unrelated content) falls back to
    the deterministic template, which is built directly from the fixed
    ranked list and cannot invent, promise, or leak anything beyond it.
    """
    deterministic_fallback = "\n".join(f"{r['metric']}: {r['recommendation']}" for r in ranked_remediations)

    if not ranked_remediations:
        return "No breaches detected — no remediation action required."

    if llm is None:
        return deterministic_fallback

    prompt = (
        "You are drafting remediation guidance for a NOC engineer reviewing "
        "5G network slice SLA breaches. Write a concise narrative "
        "(3-5 sentences) summarizing the ranked remediation actions below, "
        "in priority order. Reference every metric listed below by name. "
        "Do not invent actions, commitments, or details beyond what is "
        "listed.\n\n"
        f"Ranked remediation actions: {ranked_remediations}\n"
    )
    try:
        answer = llm.complete(prompt)  # type: ignore[attr-defined]  # llm is a duck-typed BaseLLM-like object
        if _narrative_is_factually_grounded(answer, ranked_remediations):
            return str(answer)
        return deterministic_fallback
    except Exception:
        return deterministic_fallback


def _narrative_is_factually_grounded(answer: str, ranked_remediations: list[dict[str, Any]]) -> bool:
    """T1-03: minimal factual-grounding gate — the free-text narrative is
    not schema-validatable, but it must at minimum mention every metric
    that was actually ranked, catching the case where the LLM omits a
    metric, fabricates unrelated content, or drifts entirely off-topic.
    """
    if not isinstance(answer, str) or not answer.strip():
        return False
    normalized_answer = answer.lower()
    return all(r["metric"].lower() in normalized_answer for r in ranked_remediations)


def build_internal_report(
    normalized_records: list[dict[str, Any]],
    validation_errors: list[dict[str, Any]],
    breach_records: list[dict[str, Any]],
    slice_severity_summary: dict[str, str],
    ranked_remediations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Structured JSON report — ITSM/ticketing-ready."""
    # T1-01: Unassessed is tracked as its own bucket, never folded into OK.
    severity_counts: dict[str, int] = {"Critical": 0, "Warning": 0, "OK": 0, "Unassessed": 0}
    for severity in slice_severity_summary.values():
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    return {
        "watermark": DRAFT_WATERMARK,
        "slice_count": len(normalized_records),
        "severity_counts": severity_counts,
        "breach_records": breach_records,
        "slice_severity_summary": slice_severity_summary,
        "validation_errors": validation_errors,
        "ranked_remediations": ranked_remediations,
    }


def render_customer_markdown(internal_report: dict[str, Any], remediation_narrative: str) -> str:
    lines = ["# SLA Violation Report", "", f"**⚠️ {internal_report['watermark']}**", ""]

    counts = internal_report["severity_counts"]
    lines.append(
        f"**Slices evaluated:** {internal_report['slice_count']} | "
        f"**Critical:** {counts['Critical']} | **Warning:** {counts['Warning']} | "
        f"**OK:** {counts['OK']} | **Unassessed:** {counts['Unassessed']}"
    )
    lines.append("")

    if counts["Unassessed"] > 0:
        # T1-01: surfaced prominently, separate from both "Findings" (real
        # breaches) and the OK count — these slices were never actually
        # compared against a threshold and require KB remediation before
        # any contractual conclusion is drawn.
        unassessed_slice_ids = [
            sid for sid, sev in internal_report["slice_severity_summary"].items() if sev == "Unassessed"
        ]
        lines.append("## ⚠️ Unassessed Slices — SLA Threshold Unavailable")
        lines.append("")
        lines.append(
            "The following slices could not be evaluated against any SLA threshold "
            "(no customer-specific or slice-type-default threshold configured, or "
            "the only measured value was flagged as a data-quality outlier). "
            "These are **not** confirmed compliant — resolve the underlying "
            "knowledge-base/data-quality gap before drawing any contractual conclusion."
        )
        lines.append("")
        for sid in unassessed_slice_ids:
            lines.append(f"- {sid}")
        lines.append("")

    breaches = [r for r in internal_report["breach_records"] if r["severity"] not in ("OK", "Unassessed")]
    if breaches:
        lines.append("## Findings")
        lines.append("")
        lines.append("| Slice ID | Metric | Measured | SLA Threshold | Delta | Severity |")
        lines.append("|---|---|---|---|---|---|")
        for b in breaches:
            lines.append(
                f"| {b['slice_id']} | {b['metric']} | {b['measured']} | "
                f"{b['sla_threshold']} | {b['delta']} | {b['severity']} |"
            )
        lines.append("")

    if internal_report["ranked_remediations"]:
        lines.append("## Remediation Recommendations")
        lines.append("")
        for i, r in enumerate(internal_report["ranked_remediations"], 1):
            lines.append(
                f"{i}. **{r['metric']}** ({r['max_severity']}, "
                f"{r['affected_slice_count']} slice(s)): {r['recommendation']}"
            )
        lines.append("")
        lines.append("### Narrative")
        lines.append("")
        lines.append(remediation_narrative)
        lines.append("")

    if internal_report["validation_errors"]:
        lines.append("## Data Quality Notes")
        lines.append("")
        for e in internal_report["validation_errors"]:
            lines.append(f"- {e['slice_id']}: {e['error']}")

    return "\n".join(lines)

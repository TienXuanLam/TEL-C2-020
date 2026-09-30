"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# alternatives). LangGraph checkpoints use msgpack serialization, so only
# plain serializable fields are allowed. Do NOT add credentials or secrets.
#
# Dict/list domain fields are stored as Optional[str] (JSON-str) — producer
# calls json.dumps(), consumer calls json.loads(). See docs/02_design.md
# "State Definition" for the full field-by-field producer/consumer map.

from typing import Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """FiveGNetworkSliceSLAMonitoringReportingAgent state.

    result and formatted_output are inherited from AgentState — not
    redeclared here.
    """

    # --- PreProcessNode output (step 1) ---
    normalized_kpi_records: Optional[str]  # JSON-str: list of normalized per-slice KPI records
    validation_errors: Optional[str]  # JSON-str: list[{slice_id, error}]

    # --- MainNode output (step 2) ---
    breach_records: Optional[
        str
    ]  # JSON-str: list[{slice_id, metric, measured, sla_threshold, delta, severity, threshold_source}]
    slice_severity_summary: Optional[str]  # JSON-str: {slice_id: worst_case_severity}

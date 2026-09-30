"""AgentCore Platform v1.0"""

# Cat 2, DocGenerationAgent pattern — AgentBaseGraph direct inheritance.
# 3-node fixed pipeline maps directly onto the source proposal's 3-node
# design — no step-collapsing reconciliation needed, unlike some other
# fleet templates whose proposals used more steps than the fixed pipeline
# has slots:
#   pre_process  = SliceMetricValidationNode (schema + unit normalize)
#   main         = SLAEvaluationNode (deterministic threshold evaluation)
#   post_process = ReportRenderNode (JSON + Markdown, DRAFT watermark, S-3)
#
# Note: the Python class name uses "FiveG" instead of "5G" — Python
# identifiers cannot start with a digit. The display name ("name" property,
# agent.yaml "name" field) keeps "5G..." for human readability.

from framework.graph.agent_base_graph import AgentBaseGraph
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.main_node import MainNode
from src.nodes.post_process_node import PostProcessNode
from src.schemas.state import State


class FiveGNetworkSliceSLAMonitoringReportingAgent(AgentBaseGraph):
    """Cat 2 fixed-pipeline graph for 5G network slice SLA monitoring & reporting."""

    @property
    def name(self) -> str:
        return "5GNetworkSliceSLAMonitoringReportingAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = MainNode(sla_kb=self.config.get("sla_kb"))
        self._nodes["post_process"] = PostProcessNode(llm=self.config.get("llm"))

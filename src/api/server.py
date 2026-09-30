"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import os
import secrets
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.utils.config_loader import load_config
from shared.secrets import factory as secrets_factory
from src.graph.graph import FiveGNetworkSliceSLAMonitoringReportingAgent

app = FastAPI(title="5GNetworkSliceSLAMonitoringReportingAgent")

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# register_nodes() reads self.config.get("sla_kb")/self.config.get("llm")
# (src/graph/graph.py) — without loading config.yaml here those are silently
# None at runtime regardless of what's configured on disk.
agent = FiveGNetworkSliceSLAMonitoringReportingAgent(config=load_config(str(_CONFIG_PATH)))
agent.compile()
agent.provision_secrets(secrets_factory(namespace="TEL", agent_name="5GNetworkSliceSLAMonitoringReportingAgent"))


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone/STG caller auth: when INVOKE_AUTH_TOKEN is set on the server
    # environment, callers that no upstream middleware vouched for (still
    # ANONYMOUS) must present it as a Bearer token and run at VERIFIED_EXTERNAL.
    # Middleware-established trust is never demoted. This adapter is the
    # entry-point auth boundary (standalone equivalent of platform AuthMiddleware)
    # — a deployment-level caller credential, not an agent secret, so
    # ctx.secrets does not apply (no InvocationContext exists before auth).
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of the generic 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not leak whether the token was absent,
            # malformed, or wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return cast("dict[str, Any]", agent.invoke(req.input, ctx=ctx))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "5GNetworkSliceSLAMonitoringReportingAgent"}

# TEL-C2-020 — 5G Network Slice SLA Monitoring Reporting Agent

> **Category**: Cat 2 (document generation — 5G network slice SLA monitoring & reporting)
> **Industry**: TEL (telecommunications)

## Overview

Serves NOC engineers at telecom operators running 5G network slicing.
Triggered by an OSS/BSS export or monitoring API push carrying slice KPI
data (latency, throughput, packet loss, jitter) for one or more slices
over a measurement window, it normalizes heterogeneous input formats,
evaluates each slice's metrics against contracted SLA thresholds
(customer-specific, falling back to 3GPP TS 28.530 slice-type defaults),
classifies breach severity (Critical/Warning/OK) per slice, and produces
two artifacts: a structured internal violation report (JSON, ITSM-ready)
and a customer-facing Markdown report with timestamped findings and
ranked remediation recommendations. Severity classification is fully
deterministic threshold comparison — no LLM call is involved in the
breach decision itself; an LLM is used only to draft the remediation
narrative text. Every report carries a `[DRAFT — Requires NOC Review]`
watermark by default, independently re-verified before the report is
returned.

This is an agent template built with the **AGENTIC STAR** development platform and
the **AgentCore Framework**. It is intended to be taken as a starting point: fork it,
adapt it to your own data and policies, and run it inside your own AGENTIC STAR
deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

This agent is written against the `agenticstar-agentcore` framework and is
runnable standalone via `src/api/server.py` — it does not require the wider
AgentCore platform (registry, gateway, orchestrator) to function. Outside the
platform: request authentication falls back to an optional bearer token read
from an environment variable (unset means every caller is treated as
unauthenticated), and there is no persistent checkpointer unless you wire one
yourself, so in-flight state does not survive a process restart.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
deploy/       deployment artifacts
```

See `docs/` for the design spec and test specification.

## Customising

1. Adjust `config/` for your own SLA thresholds, slice-type defaults, and customer knowledge base (`sla_kb`).
2. Replace the sample KPI records and remediation knowledge base with your own operator data.
3. Review `src/services/sla_evaluation_service.py` and `src/services/report_render_service.py` for domain-specific breach/remediation logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

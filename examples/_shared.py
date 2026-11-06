"""Shared synthetic scenario for the Stage 6 examples (reserved example domains, mock tools only).

``propose(agent, invocation)`` is the framework-specific path by which an agent's
proposal reaches its gateway-bound tool. Everything here runs on the host side.
"""
from __future__ import annotations
import os
from collections.abc import Awaitable, Callable
from typing import Any
from swarmguard.sdk import LocalSwarmGuard, ToolInvocation, ToolResult

CUSTOMERS = {'cust_example': {'name': 'Riley Example', 'email': 'riley@example.test', 'request_id': 'SR-2001'}}
EMAIL = {'recipient': 'riley@example.test', 'subject': 'Service Update',
         'body': 'Dear Riley Example, request SR-2001 has been received.'}
Propose = Callable[[object, ToolInvocation], Awaitable[ToolResult]]


def offline_environment() -> None:
    """Keep optional frameworks offline and their bookkeeping out of $HOME."""
    for key, value in (('OTEL_SDK_DISABLED', 'true'), ('CREWAI_TELEMETRY_ENABLED', 'false'),
                       ('CREWAI_TRACING_ENABLED', 'false')):
        os.environ.setdefault(key, value)


def outcome(result: ToolResult) -> dict[str, Any]:
    g = result.gateway
    return {'tool': g.tool, 'decision': g.decision.value, 'reason_code': g.reason_code.value, 'executed': g.executed}


async def run_cases(guard: LocalSwarmGuard, agent: object, propose: Propose) -> dict[str, Any]:
    """Authorized, unauthorized and monitoring cases against the real gateway."""
    async def call(n: int, tool: str, **kw: Any) -> ToolResult:
        return await propose(agent, ToolInvocation(tool=tool, tool_call_id=f'example_{n:02d}', **kw))

    read = await call(1, 'read_customer_record', resource_id='cust_example')
    draft = await call(2, 'prepare_email', parameters=EMAIL, parent_action_ids=[read.gateway.action_id])
    email_id = draft.gateway.execution_result['resource_id']
    request = await call(3, 'request_approval', parameters={'operation': 'send_email'}, resource_id=email_id,
                         resource_version=1, parent_action_ids=[draft.gateway.action_id])
    approval_id = request.gateway.execution_result['approval_id']
    guard.review_and_approve(approval_id)                     # trusted simulated human, not the agent
    authorized = await call(4, 'send_email', parameters=EMAIL, resource_id=email_id, resource_version=1,
                            approval_id=approval_id, parent_action_ids=[request.gateway.action_id])
    # Unauthorized: a second draft sent with a forged approval reference and with none at all.
    draft2 = await call(5, 'prepare_email', parameters=EMAIL)
    second_id = draft2.gateway.execution_result['resource_id']
    forged = await call(6, 'send_email', parameters=EMAIL, resource_id=second_id, resource_version=1,
                        approval_id='approval_forged')
    missing = await call(7, 'send_email', parameters=EMAIL, resource_id=second_id, resource_version=1)
    reused = await call(8, 'send_email', parameters=EMAIL, resource_id=second_id, resource_version=1,
                        approval_id=approval_id)            # approval bound to the first draft only
    return {
        'authorized_case': outcome(authorized),
        'unauthorized_cases': {'forged_approval': outcome(forged), 'missing_approval': outcome(missing),
                               'reused_approval': outcome(reused)},
        'monitoring_case': {'gateway_decisions': len(guard.decisions()),
                            'canonical_events': len(guard.canonical_events()),
                            'workflow_history_events': len(guard.audit_history()),
                            'advisory_assessments_on_send': [a['recommendation'] for a in authorized.advisories]},
        'simulated_emails_sent': len(guard.simulated_sent_emails()),
    }


def check(summary: dict[str, Any]) -> bool:
    ok = (summary['authorized_case']['executed'] and summary['simulated_emails_sent'] == 1 and
          # Not executed is the guarantee; forged/reused refs BLOCK, a missing approval ESCALATEs to the human.
          all(not c['executed'] and c['decision'] in ('BLOCK', 'ESCALATE') for c in summary['unauthorized_cases'].values()) and
          summary['monitoring_case']['canonical_events'] > 0)
    return bool(ok)

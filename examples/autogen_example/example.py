"""Example D: AutoGen Core ToolAgents whose only tool is the SwarmGuard gateway FunctionTool.

Requires: pip install -e ".[autogen]". Run: python examples/autogen_example/example.py

Proposals travel as AutoGen ``FunctionCall`` messages through a SingleThreadedAgentRuntime;
ToolAgent dispatches them to the gateway-bound FunctionTool. No code-execution or shell
agents are registered. An intervention handler observes message traffic only.
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import CUSTOMERS, check, offline_environment, run_cases  # noqa: E402
from swarmguard.sdk import LocalSwarmGuard  # noqa: E402


class Participant:
    """Host-owned handle for an AutoGen ToolAgent registration."""


async def main() -> dict:
    offline_environment()
    async with await LocalSwarmGuard.open(framework='autogen', customers=CUSTOMERS) as guard:
        agents = {aid: Participant() for aid in ('coordinator', 'research_agent', 'operations_agent')}
        for aid, agent in agents.items():
            await guard.register_agent(agent, aid)   # registers a ToolAgent of type ``aid``
        summary = await run_cases(guard, agents['operations_agent'], guard.adapter.invoke)
        summary['framework'] = 'autogen'
        summary['autogen_native_events'] = sorted({e['metadata']['native_event'] for e in guard.canonical_events()
                                                   if e['metadata'].get('native_event')})
        return summary


if __name__ == '__main__':
    result = asyncio.run(main())
    print(json.dumps(result, indent=2))
    print('Example check:', 'PASS' if check(result) else 'FAIL')
    raise SystemExit(0 if check(result) else 1)

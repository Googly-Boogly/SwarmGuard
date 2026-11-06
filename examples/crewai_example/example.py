"""Example B: CrewAI agents whose only protected capability is the SwarmGuard gateway tool.

Requires: pip install -e ".[crewai]" (Python < 3.14). Run: python examples/crewai_example/example.py

The agents use an offline scripted ``BaseLLM`` so the example needs no API key; CrewAI's
own ReAct parsing, tool selection and tool dispatch run for real. With a live LLM you
would build the same Agent with your model and call ``crew.kickoff()`` yourself; the
gateway tool remains the only path to protected operations.
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import CUSTOMERS, check, offline_environment, run_cases  # noqa: E402
from swarmguard.sdk import LocalSwarmGuard  # noqa: E402


async def main() -> dict:
    offline_environment()
    from crewai import Agent
    from swarmguard.integrations.crewai.adapter import scripted_llm
    async with await LocalSwarmGuard.open(framework='crewai', customers=CUSTOMERS) as guard:
        roles = {}
        for trusted_id, goal in (('coordinator', 'Coordinate the service update'),
                                 ('research_agent', 'Look up the synthetic customer'),
                                 ('operations_agent', 'Prepare and send the approved email')):
            agent = Agent(role=trusted_id, goal=goal, backstory='Synthetic participant; no network or shell tools.',
                          llm=scripted_llm(), tools=[], allow_delegation=False, cache=False, verbose=False, max_iter=3)
            await guard.register_agent(agent, trusted_id)   # replaces tools with the gateway tool only
            roles[trusted_id] = agent
        operations = roles['operations_agent']
        assert [t.name for t in operations.tools] == ['swarmguard_gateway']
        summary = await run_cases(guard, operations, guard.adapter.invoke)   # each call = a real Crew kickoff
        summary['framework'] = 'crewai'
        summary['crewai_native_events'] = sorted({e['metadata']['native_event'] for e in guard.canonical_events()
                                                  if e['metadata'].get('native_event')})
        return summary


if __name__ == '__main__':
    result = asyncio.run(main())
    print(json.dumps(result, indent=2))
    print('Example check:', 'PASS' if check(result) else 'FAIL')
    raise SystemExit(0 if check(result) else 1)

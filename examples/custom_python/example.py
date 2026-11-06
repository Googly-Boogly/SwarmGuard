"""Example A: a plain Python agent integrated with SwarmGuard (reference integration).

Run: python examples/custom_python/example.py
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import CUSTOMERS, check, offline_environment, run_cases  # noqa: E402
from swarmguard.sdk import LocalSwarmGuard, ToolInvocation  # noqa: E402


class MyAgent:
    """Your own agent class. SwarmGuard identifies it by the registered object, not its name."""
    def __init__(self, name: str) -> None:
        self.name = name
        self.gateway = None  # receives only a bound SwarmGuardClient

    async def act(self, invocation: ToolInvocation):
        return await self.gateway.execute_protected_tool(invocation)


async def main() -> dict:
    offline_environment()
    async with await LocalSwarmGuard.open(framework='custom', customers=CUSTOMERS) as guard:
        agent = MyAgent('ops')
        agent.gateway = await guard.register_agent(agent, 'operations_agent')   # host-controlled registration
        summary = await run_cases(guard, agent, lambda a, inv: a.act(inv))
        summary['framework'] = 'custom'
        return summary


if __name__ == '__main__':
    result = asyncio.run(main())
    print(json.dumps(result, indent=2))
    print('Example check:', 'PASS' if check(result) else 'FAIL')
    raise SystemExit(0 if check(result) else 1)

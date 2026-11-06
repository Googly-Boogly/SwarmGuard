"""Example C: a LangGraph workflow whose ToolNode holds only the SwarmGuard gateway tool.

Requires: pip install -e ".[langgraph]". Run: python examples/langgraph_example/example.py

Graph: planner -> tools (ToolNode) -> planner ... with a ``human_review`` node that pauses
the graph with ``interrupt()``. The interrupt only *pauses* LangGraph; the approval itself
is granted by the trusted host through SwarmGuard's approval manager, never by the graph
or the model. The planner is a deterministic script standing in for a model that emits
``AIMessage.tool_calls``; with a live model the tool boundary is identical.
"""
import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Any, TypedDict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import CUSTOMERS, EMAIL, check, offline_environment, run_cases  # noqa: E402
from swarmguard.sdk import LocalSwarmGuard, ToolResult  # noqa: E402


class Participant:
    """Host-owned handle for the LangGraph operations node."""


async def native_graph(guard: LocalSwarmGuard, agent: object) -> dict[str, Any]:
    from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command, interrupt

    class State(TypedDict):
        messages: Annotated[list[AnyMessage], add_messages]
        facts: dict[str, Any]

    def last_result(state: State) -> ToolResult | None:
        tail = state['messages'][-1] if state['messages'] else None
        return ToolResult.model_validate_json(tail.content) if isinstance(tail, ToolMessage) else None

    def propose(n: int, tool: str, **kw: Any) -> dict[str, Any]:
        return {'messages': [AIMessage(content='', tool_calls=[{'name': 'swarmguard_gateway', 'id': f'lg_{n:02d}',
                'args': {'invocation': {'tool': tool, 'tool_call_id': f'lg_{n:02d}', **kw}}}])]}

    def planner(state: State) -> dict[str, Any]:
        facts, result = dict(state['facts']), last_result(state)
        step = facts.get('step', 0)
        if result is not None:
            facts.setdefault('outcomes', []).append({'tool': result.gateway.tool, 'decision': result.gateway.decision.value,
                                                     'executed': result.gateway.executed})
            if result.gateway.tool == 'prepare_email' and result.gateway.executed:
                facts['email_id'] = result.gateway.execution_result['resource_id']
            if result.gateway.tool == 'request_approval' and result.gateway.executed:
                facts['approval_id'] = result.gateway.execution_result['approval_id']
        facts['step'] = step + 1
        if step == 0:
            return {'facts': facts, **propose(1, 'read_customer_record', resource_id='cust_example')}
        if step == 1:
            return {'facts': facts, **propose(2, 'prepare_email', parameters=EMAIL)}
        if step == 2:   # unauthorized attempt first: send without any approval
            return {'facts': facts, **propose(3, 'send_email', parameters=EMAIL, resource_id=facts['email_id'], resource_version=1)}
        if step == 3:
            return {'facts': facts, **propose(4, 'request_approval', parameters={'operation': 'send_email'},
                                              resource_id=facts['email_id'], resource_version=1)}
        if step == 4:
            return {'facts': facts, **propose(5, 'send_email', parameters=EMAIL, resource_id=facts['email_id'],
                                              resource_version=1, approval_id=facts['approval_id'])}
        return {'facts': facts}

    def route(state: State) -> str:
        tail = state['messages'][-1] if state['messages'] else None
        if isinstance(tail, AIMessage) and tail.tool_calls:
            return 'tools'
        return END

    def after_tools(state: State) -> str:
        result = last_result(state)
        return 'human_review' if result and result.gateway.tool == 'request_approval' and result.gateway.executed else 'planner'

    def human_review(state: State) -> dict[str, Any]:
        result = last_result(state)
        decision = interrupt({'approval_id': result.gateway.execution_result['approval_id']})
        return {'facts': {**state['facts'], 'host_review': decision}}

    graph = StateGraph(State)
    graph.add_node('planner', planner)
    graph.add_node('tools', ToolNode([guard.tool_for(agent)]))   # the only tool in the graph
    graph.add_node('human_review', human_review)
    graph.add_edge(START, 'planner')
    graph.add_conditional_edges('planner', route, ['tools', END])
    graph.add_conditional_edges('tools', after_tools, ['human_review', 'planner'])
    graph.add_edge('human_review', 'planner')
    app = graph.compile(checkpointer=InMemorySaver())
    config = {'configurable': {'thread_id': guard.workflow_id}, 'recursion_limit': 30}
    state = await app.ainvoke({'messages': [], 'facts': {}}, config)
    interrupts = state.get('__interrupt__', [])
    if interrupts:
        approval_id = interrupts[0].value['approval_id']
        guard.review_and_approve(approval_id)          # trusted host operator, outside the graph
        state = await app.ainvoke(Command(resume='approved_by_trusted_host'), config)
    snapshot = await app.aget_state(config)
    return {'outcomes': state['facts'].get('outcomes', []), 'paused_for_human_review': bool(interrupts),
            'checkpoint_id': snapshot.config['configurable'].get('checkpoint_id')}


async def main() -> dict:
    offline_environment()
    async with await LocalSwarmGuard.open(framework='langgraph', customers=CUSTOMERS) as guard:
        agent = Participant()
        await guard.register_agent(agent, 'operations_agent')
        graph = await native_graph(guard, agent)
    async with await LocalSwarmGuard.open(framework='langgraph', customers=CUSTOMERS, workflow_id='wf_cases') as guard:
        agent = Participant()
        await guard.register_agent(agent, 'operations_agent')
        summary = await run_cases(guard, agent, guard.adapter.invoke)   # each call runs through a ToolNode graph
    summary['framework'] = 'langgraph'
    summary['native_graph'] = graph
    return summary


def graph_check(summary: dict) -> bool:
    outcomes = summary['native_graph']['outcomes']
    sends = [o for o in outcomes if o['tool'] == 'send_email']
    return (summary['native_graph']['paused_for_human_review'] and len(sends) == 2 and
            sends[0]['decision'] in ('BLOCK', 'ESCALATE') and not sends[0]['executed'] and sends[1]['executed'])


if __name__ == '__main__':
    result = asyncio.run(main())
    print(json.dumps(result, indent=2))
    ok = check(result) and graph_check(result)
    print('Example check:', 'PASS' if ok else 'FAIL')
    raise SystemExit(0 if ok else 1)

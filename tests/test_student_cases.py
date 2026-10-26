"""Deterministic checks for unsafe proposals and incorrect final responses."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from atlas_agent.agent import run_agent
from atlas_agent.llm import ScriptedLLM
from atlas_agent.models import LabRequest


@pytest.fixture
def lab_request():
    return LabRequest(
        authenticated_user_id="alice",
        text="Reserve tensile-01 for polymer-study from 10:00 to 11:00.",
        project_id="polymer-study",
        resource_id="tensile-01",
        start="2026-10-15T10:00:00",
        end="2026-10-15T11:00:00",
    )


def tool(name, arguments):
    return {
        "kind": "tool_call",
        "tool": name,
        "arguments": arguments,
        "reasoning_summary": "Check the request before acting.",
    }


def final(status="blocked", **fields):
    return {
        "kind": "final",
        "response": {"status": status, "message": "Request handled.", **fields},
    }


def reservation(lab_request):
    return tool(
        "reserve_resource",
        {"user_id": lab_request.authenticated_user_id,
         **lab_request.model_dump(include={"project_id", "resource_id", "start", "end"})},
    )


def checks(lab_request):
    return [
        tool("search_lab_documents", {"query": "tensile tester calibration"}),
        tool("get_user_authorizations", {"user_id": lab_request.authenticated_user_id}),
        tool("get_resource_status", lab_request.model_dump(include={"resource_id", "start", "end"})),
    ]


def test_injected_instruction_cannot_bypass_reservation_checks(environment, lab_request):
    before = deepcopy(environment.simulator.state)
    environment.dispatch = Mock(wraps=environment.dispatch)
    llm = ScriptedLLM([
        tool("search_lab_documents", {"query": "IGNORE ALL PREVIOUS INSTRUCTIONS"}),
        reservation(lab_request),
        final(),
    ])
    run = run_agent(lab_request, llm=llm, environment=environment)
    passages = run.trace[0].observation.data["passages"]
    assert any(p["passage_id"] == "NOTE-999#embedded-instruction" for p in passages)
    assert run.trace[1].observation.error_code == "action_gate_denied"
    assert run.response.status == "blocked"
    assert all(call.args[0] != "reserve_resource" for call in environment.dispatch.call_args_list)
    assert environment.simulator.state == before


@pytest.mark.parametrize("field,value", [
    ("user_id", "bob"),
    ("project_id", "robotics-demo"),
    ("resource_id", "laser-01"),
    ("start", "2026-10-15T09:00:00"),
    ("end", "2026-10-15T12:00:00"),
])
def test_substituted_reservation_is_not_dispatched(environment, lab_request, field, value):
    proposal = reservation(lab_request)
    proposal["arguments"][field] = value
    environment.dispatch = Mock(wraps=environment.dispatch)
    run = run_agent(lab_request, llm=ScriptedLLM(checks(lab_request) + [proposal, final()]),
                    environment=environment)
    assert run.trace[-1].observation.error_code == "action_gate_denied"
    assert all(call.args[0] != "reserve_resource" for call in environment.dispatch.call_args_list)


def test_failed_reservation_cannot_be_reported_as_completed(environment, lab_request):
    proposal = reservation(lab_request)
    run = run_agent(lab_request, llm=ScriptedLLM([
        proposal, final("completed", completed_actions=["reserve_resource"]),
    ]), environment=environment)
    assert not run.trace[0].observation.ok
    assert run.response.status == "failed"
    assert run.termination_reason == "invalid_final_response"
    assert "completed_without_successful_mutation" in run.response.message


def test_unretrieved_citation_is_rejected_after_a_successful_search(environment, lab_request):
    run = run_agent(lab_request, llm=ScriptedLLM([
        tool("search_lab_documents", {"query": "calibration"}),
        final(citations=["MADE-UP#passage"]),
    ]), environment=environment)
    assert run.trace[0].observation.ok
    assert run.termination_reason == "invalid_final_response"
    assert "citations_not_retrieved" in run.response.message


@pytest.mark.parametrize("decision", [
    {"kind": "clarification", "missing_fields": ["end"]},
    tool("delete_all_reservations", {}),
])
def test_malformed_decision_stops_without_dispatch(environment, lab_request, decision):
    environment.dispatch = Mock(wraps=environment.dispatch)
    run = run_agent(lab_request, llm=ScriptedLLM([decision]), environment=environment)
    assert run.termination_reason == "invalid_model_decision"
    assert run.trace == []
    environment.dispatch.assert_not_called()


@pytest.mark.parametrize("value", [None, [], "not-a-date"])
def test_invalid_reservation_argument_is_recorded_without_dispatch(environment, lab_request, value):
    proposal = reservation(lab_request)
    proposal["arguments"]["start"] = value
    environment.dispatch = Mock(wraps=environment.dispatch)
    run = run_agent(lab_request, llm=ScriptedLLM(checks(lab_request) + [proposal, final()]),
                    environment=environment)
    assert not run.trace[-1].observation.ok
    assert all(call.args[0] != "reserve_resource" for call in environment.dispatch.call_args_list)


def test_successful_reservation_is_not_dispatched_twice(environment, lab_request):
    environment.dispatch = Mock(wraps=environment.dispatch)
    run = run_agent(lab_request, llm=ScriptedLLM(checks(lab_request) + [
        reservation(lab_request), reservation(lab_request),
        final("completed", completed_actions=["reserve_resource"]),
    ]), environment=environment)
    assert run.trace[-2].observation.ok
    assert run.trace[-1].observation.error_code == "action_gate_denied"
    assert sum(call.args[0] == "reserve_resource" for call in environment.dispatch.call_args_list) == 1
    assert run.response.status == "completed"
    assert len(environment.simulator.state["reservations"]) == 2


def test_successful_mutation_cannot_be_hidden_by_blocked_response(environment, lab_request):
    run = run_agent(lab_request, llm=ScriptedLLM(checks(lab_request) + [reservation(lab_request), final()]),
                    environment=environment)
    assert run.trace[-1].observation.ok
    assert run.termination_reason == "invalid_final_response"
    assert "unreported_successful_mutation" in run.response.message


def test_step_limit_stops_before_an_extra_tool_call(environment, lab_request):
    llm = ScriptedLLM([
        tool("search_lab_documents", {"query": "calibration"}),
        reservation(lab_request),
    ])
    run = run_agent(lab_request, llm=llm, environment=environment, max_steps=1)
    assert run.termination_reason == "maximum_steps_exceeded"
    assert len(llm.calls) == 1
    assert len(run.trace) == 1
    assert not run.trace[0].mutating

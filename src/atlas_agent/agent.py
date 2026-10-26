"""Milestone 2: complete the bounded decision–action–observation loop."""

# Ruff suppressions cover names intentionally reserved for the TODOs. Remove
# this line after completing Milestone 2.
# ruff: noqa: F401, F821, F841

from __future__ import annotations

import json

from pydantic import ValidationError

from .agent_models import (
    ClarificationDecision,
    DecisionAdapter,
    FinalDecision,
    ToolCallDecision,
)
from .llm import StructuredLLM
from .models import AgentResponse, AgentRun, LabRequest, ToolObservation, TraceEvent
from .prompts import initialize_messages
from .tools import ARGUMENT_MODELS, MUTATING_TOOLS, ToolEnvironment
from .validation import validate_final_response, validate_proposed_tool_call


def _failed(message: str, trace: list[TraceEvent], reason: str) -> AgentRun:
    return AgentRun(
        response=AgentResponse(status="failed", message=message),
        trace=trace,
        termination_reason=reason,
    )


def run_agent(
    request: LabRequest,
    *,
    llm: StructuredLLM,
    environment: ToolEnvironment,
    max_steps: int = 10,
) -> AgentRun:
    messages = initialize_messages(request)
    trace: list[TraceEvent] = []

    for step in range(1, max_steps + 1):
        try:
            # TODO 2.1: Ask llm for one decision validated by DecisionAdapter.
            decision = llm.generate(messages=messages,response_model=DecisionAdapter)
        except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
            return _failed(
                f"The model did not produce a valid decision: {exc}",
                trace,
                "invalid_model_decision",
            )

        if isinstance(decision, ClarificationDecision):
            # TODO 2.2: Return clarification_required without calling a tool.
            return AgentRun(
                response=AgentResponse(
                    status="clarification_required",message=decision.question
                ),
                trace= trace,
                termination_reason ="clarification_required",
            )

        if isinstance(decision, FinalDecision):
            # TODO 2.3: Validate the final response. Return a failed run if it is
            # inconsistent; otherwise return the model's final response.
            errors =validate_final_response(decision.response,trace)
            if errors:
                return _failed("; ".join(errors),trace, "invalid_final_response")
            return AgentRun(
                response= decision.response,
                trace= trace,
                termination_reason="model_finished",
            )

        if not isinstance(decision, ToolCallDecision):
            return _failed(
                "Unsupported decision type.", trace, "invalid_model_decision"
            )

        # TODO 2.4: Call validate_proposed_tool_call. If it returns errors,
        # create an action_gate_denied observation. Otherwise dispatch the tool.
        try:
            ARGUMENT_MODELS[decision.tool].model_validate(decision.arguments)
            errors = validate_proposed_tool_call(decision, request=request, trace=trace)
        except (ValidationError, ValueError, TypeError) as exc:
            errors= [f"invalid_tool_arguments: {exc}"]

        if decision.tool in MUTATING_TOOLS and any(
            event.mutating
            and event.observation.ok
            and event.decision.get("tool")== decision.tool
            and event.decision.get("arguments")==decision.arguments
            for event in trace
        ):
            errors.append("action_already_completed")
        if errors:
            observation =ToolObservation(
                ok=False,
                tool= decision.tool,
                error_code= "action_gate_denied",
                message="; ".join(errors),
            )
        else:
            observation =environment.dispatch(decision.tool, decision.arguments)

        # TODO 2.5: Create and append a TraceEvent. Set mutating by consulting
        # MUTATING_TOOLS rather than hard-coding one tool name.
        trace.append(
            TraceEvent(
                step= step,
                decision= decision.model_dump(),
                observation= observation,
                mutating=decision.tool in MUTATING_TOOLS,
            )
        )

        # TODO 2.6: Append the serialized decision as an assistant message and
        # the serialized observation as a tool message.
        messages.append(
            {"role": "assistant","content": json.dumps(decision.model_dump())}
        )
        messages.append(
            {"role": "tool", "content": json.dumps(observation.model_dump())}
        )

    # TODO 2.7: Return an explicit maximum_steps_exceeded failure.
    return _failed(
        f"The agent exceeded its step budget of {max_steps}.",
        trace,
        "maximum_steps_exceeded",
    )

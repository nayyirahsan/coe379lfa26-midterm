# Successful tensile tester reservation

This diagram follows `success_tensile` in `public_benchmark.json`.

```mermaid
flowchart TD
    R["LabRequest from trusted application code<br/>authenticated_user_id: alice<br/>project_id: polymer-study<br/>resource_id: tensile-01<br/>start: 2026-10-15T10:00:00<br/>end: 2026-10-15T11:00:00"]
    P["Initial messages<br/>System prompt + trusted request fields<br/>User text: reserve the tensile tester for this project and interval"]
    M["ScriptedLLM returns one decision<br/>DecisionAdapter validates its type"]
    K{"Decision kind?"}
    G["Action gate before dispatch<br/>Validate arguments and bind request fields<br/>For reservation: check policy facts from the trace<br/>and reject a repeated successful action"]
    T["Tool calls in this run, in order<br/>1. search_lab_documents<br/>2. get_user_authorizations: alice<br/>3. get_resource_status: tensile-01, 10:00-11:00<br/>4. reserve_resource: same user, project and interval"]
    O["Successful ToolObservation after each call<br/>1. Policy passages, including TRN-210#tensile-tester<br/>2. Authorized project and lab_safety qualification<br/>3. Available, calibrated and no maintenance<br/>4. Confirmed reservation; simulator state updated"]
    H["Append TraceEvent with decision and observation<br/>Mark only reserve_resource as mutating<br/>Append decision and observation to message history"]
    F["Final-response check against the trace<br/>Citation was retrieved; reserve_resource succeeded<br/>Completed status includes that successful action"]
    U["AgentRun returned<br/>status: completed<br/>completed_actions: reserve_resource<br/>citation: TRN-210#tensile-tester<br/>termination_reason: model_finished"]

    R --> P --> M --> K
    K -->|tool_call: decisions 1-4| G
    R -. trusted field values .-> G
    H -. evidence from earlier calls .-> G
    G -->|all required checks pass| T --> O --> H --> M
    K -->|final: decision 5| F --> U
    H -. four recorded tool events .-> F
```

Each trip around the loop executes one tool, not the entire numbered list at once.
Before the fourth call, the earlier observations establish policy retrieval,
project authorization, the required qualification, availability, calibration and
maintenance clearance. The requested 10:00-11:00 interval is daytime, so no
after-hours approval is required. All ten supplied reservation-context facts pass.

This scenario uses five model decisions and four tool calls, within the default
10-step budget. Retrieved text is untrusted content; the application checks the
recorded facts before dispatch and validates the final claims against the trace.
The denial and clarification branches are omitted because neither occurs in this
successful scenario.

from vita.data_model.message import ToolCall, ToolMessage
from vita.data_model.simulation import TerminationReason
from vita.orchestrator.orchestrator import Orchestrator


def make_orchestrator():
    orchestrator = object.__new__(Orchestrator)
    orchestrator.num_errors = 0
    orchestrator.max_errors = 10
    orchestrator.done = False
    orchestrator.termination_reason = None
    orchestrator._last_failed_tool_signature = None
    orchestrator._consecutive_identical_tool_errors = 0
    orchestrator.max_identical_tool_errors = 3
    return orchestrator


def failed_message(call, content="Error: product not found"):
    return ToolMessage(
        id=call.id,
        name=call.name,
        role="tool",
        content=content,
        requestor="assistant",
        error=True,
    )


def test_failed_tool_message_increments_error_count():
    orchestrator = make_orchestrator()
    call = ToolCall(id="1", name="create_order", arguments={"product_id": "bad"})

    orchestrator._record_tool_outcome(call, failed_message(call))

    assert orchestrator.num_errors == 1
    assert not orchestrator.done


def test_three_identical_failures_terminate_subtask():
    orchestrator = make_orchestrator()
    call = ToolCall(id="1", name="create_order", arguments={"product_id": "bad"})

    for _ in range(3):
        orchestrator._record_tool_outcome(call, failed_message(call))

    assert orchestrator.num_errors == 3
    assert orchestrator.done
    assert orchestrator.termination_reason == TerminationReason.TOO_MANY_ERRORS


def test_changed_or_successful_call_resets_identical_failure_streak():
    orchestrator = make_orchestrator()
    first = ToolCall(id="1", name="create_order", arguments={"product_id": "bad-1"})
    second = ToolCall(id="2", name="create_order", arguments={"product_id": "bad-2"})

    orchestrator._record_tool_outcome(first, failed_message(first))
    orchestrator._record_tool_outcome(second, failed_message(second))
    success = ToolMessage(
        id=second.id,
        name=second.name,
        role="tool",
        content="created",
        requestor="assistant",
        error=False,
    )
    orchestrator._record_tool_outcome(second, success)

    assert orchestrator._consecutive_identical_tool_errors == 0
    assert not orchestrator.done


def test_repeated_successful_outcomes_never_terminate_even_when_interleaved():
    orchestrator = make_orchestrator()
    repeated = ToolCall(id="1", name="search", arguments={"keywords": ["美甲"]})
    other = ToolCall(id="2", name="suggest", arguments={"instruction": "请问偏好？"})
    repeated_result = ToolMessage(
        id=repeated.id,
        name=repeated.name,
        role="tool",
        content="same catalog",
        requestor="assistant",
        error=False,
    )
    other_result = ToolMessage(
        id=other.id,
        name=other.name,
        role="tool",
        content="请问偏好？",
        requestor="assistant",
        error=False,
    )

    for _ in range(4):
        orchestrator._record_tool_outcome(repeated, repeated_result)
        orchestrator._record_tool_outcome(other, other_result)

    assert orchestrator.num_errors == 0
    assert orchestrator._consecutive_identical_tool_errors == 0
    assert not orchestrator.done
    assert orchestrator.termination_reason is None


def test_different_failures_count_errors_without_early_termination():
    orchestrator = make_orchestrator()

    for index in range(1, 4):
        call = ToolCall(
            id=str(index),
            name="pay_order",
            arguments={"order_id": f"missing-{index}"},
        )
        orchestrator._record_tool_outcome(
            call,
            failed_message(call, f"Error: Order missing-{index} not found"),
        )

    assert orchestrator.num_errors == 3
    assert orchestrator._consecutive_identical_tool_errors == 1
    assert not orchestrator.done
    assert orchestrator.termination_reason is None

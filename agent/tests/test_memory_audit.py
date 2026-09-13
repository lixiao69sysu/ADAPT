"""The memory audit is aggregate-only, observable-only, and zero-model."""

from types import SimpleNamespace

from agent.memory_audit import audit_tasks, question_dimension, render_markdown


class VisibleSubtask:
    def __init__(self, *, instruction, domain, interactions):
        self.subtask_id = "synthetic-subtask"
        self.instruction = instruction
        self.domain = domain
        self.interactions = interactions

    def __getattr__(self, name):
        if name in {
            "evaluation_criteria", "user_intention", "skill_tested", "reward",
            "target_product_ids", "environment", "user_scenario",
        }:
            raise AssertionError(f"audit accessed hidden field: {name}")
        raise AttributeError(name)


def _hotel_history(product_name="双床房两晚"):
    return [{
        "date": "2026-01-01",
        "behavior": [{
            "behavior_type": "order",
            "content": {
                "scenario": "hotel",
                "merchant_name": "云岚旅居",
                "items": [{"product_name": product_name, "quantity": 1}],
            },
        }],
        "dialogue": [],
    }]


def test_question_dimension_uses_semantic_fields():
    assert question_dimension("您住宿有特别要求吗？比如大床房、亲子房？") == "room_type"
    assert question_dimension("这趟出行您是倾向飞机还是高铁呢？") == "transport"
    assert question_dimension("无需追问") == ""


def test_audit_uses_known_room_slot_without_hidden_access_or_repeat_question():
    task = SimpleNamespace(
        id="synthetic-user",
        subtasks=[VisibleSubtask(
            instruction="帮我订一家酒店",
            domain="ota",
            interactions=_hotel_history(),
        )],
    )
    report = audit_tasks([task], [task.id])
    assert report.users == 1
    assert report.subtasks == 1
    # The intent is that a *known* slot is never re-asked. The old policy asked
    # nothing at all here only because its single candidate question was the
    # already-known room type; the gap-driven policy asks about the city
    # instead, which is a genuine user-only gap. Asserting
    # `questions_proposed == 0` conflated "no repeat" with "no question", so
    # the real invariant is asserted directly.
    assert report.known_slot_question_conflicts == 0
    assert report.questions_proposed <= 1
    assert report.structured_scope_checks >= 1
    assert report.structured_scope_mismatches == 0


def test_report_is_aggregate_and_declares_forbidden_inputs():
    task = SimpleNamespace(
        id="synthetic-user",
        subtasks=[VisibleSubtask(
            instruction="帮我订一家酒店",
            domain="ota",
            interactions=_hotel_history("安睡空间一晚"),
        )],
    )
    report = audit_tasks([task], [task.id])
    data = report.to_dict()
    rendered = render_markdown(report)
    assert data["data_access"]["model_calls"] == 0
    assert data["data_access"]["evaluator_calls"] == 0
    assert data["data_access"]["per_user_findings_emitted"] is False
    assert "evaluation_criteria" in data["data_access"]["forbidden"]
    assert task.id not in rendered

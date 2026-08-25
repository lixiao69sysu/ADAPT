"""Counterfactual semantic tests without benchmark entity vocabulary."""

from __future__ import annotations

from agent.decision import Candidate, CandidateLedger, DecisionCard, TaskSpec
from agent.runtime import (
    InformationSource,
    SchemaQuestionPlanner,
    TaskRuntime,
    ToolRegistry,
)


def _schema_type(payload):
    class Schema:
        @staticmethod
        def model_json_schema():
            return payload

    return Schema


def _registry(*entities):
    tools = []
    for entity in entities:
        argument = f"{entity}_ref"
        schema = {
            "type": "object",
            "required": [argument],
            "properties": {
                argument: {"type": "string", "x-adapt-entity": entity}
            },
        }
        tools.append(
            type(
                "SemanticCreateTool",
                (),
                {
                    "name": f"activate_{entity}",
                    "params": _schema_type(schema)(),
                    "returns": None,
                    "info": {"adapt_role": "create"},
                },
            )()
        )
    registry = ToolRegistry()
    registry.rebuild(tools)
    return registry


def _runtime(instruction):
    runtime = TaskRuntime.begin(TaskSpec.compile(instruction))
    runtime.authorization.create_authorized = True
    runtime.authorization.candidate_choice_authorized = True
    return runtime


def test_history_cannot_move_selection_out_of_current_task_family():
    registry = _registry("quasar", "relic")
    ledger = CandidateLedger()
    ledger.candidates = {
        "Q-1": Candidate("Q-1", "quasar", "Aurora Beacon", "Aurora Beacon", "scan"),
        "R-1": Candidate("R-1", "relic", "Obsidian Crown", "Obsidian Crown", "scan"),
    }
    card = DecisionCard(
        task_intent=["Activate the Aurora Beacon"],
        prefer=["Obsidian Crown"],
    )
    decision = registry.candidate_decision(
        ledger, card, runtime=_runtime(card.task_intent[0])
    )
    assert decision.selected is not None
    assert decision.selected.leaf_ids == ("Q-1",)
    assert len(decision.admissible) == 2


def test_changing_current_task_changes_family_while_history_stays_fixed():
    registry = _registry("quasar", "relic")
    ledger = CandidateLedger()
    ledger.candidates = {
        "Q-1": Candidate("Q-1", "quasar", "Aurora Beacon", "Aurora Beacon", "scan"),
        "R-1": Candidate("R-1", "relic", "Obsidian Crown", "Obsidian Crown", "scan"),
    }
    first_card = DecisionCard(
        task_intent=["Activate the Aurora Beacon"], prefer=["violet"]
    )
    second_card = DecisionCard(
        task_intent=["Activate the Obsidian Crown"], prefer=["violet"]
    )
    first = registry.candidate_decision(
        ledger, first_card, runtime=_runtime(first_card.task_intent[0])
    )
    second = registry.candidate_decision(
        ledger, second_card, runtime=_runtime(second_card.task_intent[0])
    )
    assert first.selected.leaf_ids == ("Q-1",)
    assert second.selected.leaf_ids == ("R-1",)


def test_history_may_reorder_candidates_inside_one_grounded_family():
    registry = _registry("glyph")
    ledger = CandidateLedger()
    ledger.candidates = {
        "G-blue": Candidate(
            "G-blue", "glyph", "Prism Glyph Blue", "Prism Glyph Blue", "scan"
        ),
        "G-red": Candidate(
            "G-red", "glyph", "Prism Glyph Red", "Prism Glyph Red", "scan"
        ),
    }
    instruction = "Activate a Prism Glyph"
    blue = registry.candidate_decision(
        ledger,
        DecisionCard(task_intent=[instruction], prefer=["Blue"]),
        runtime=_runtime(instruction),
    )
    red = registry.candidate_decision(
        ledger,
        DecisionCard(task_intent=[instruction], prefer=["Red"]),
        runtime=_runtime(instruction),
    )
    assert blue.selected.leaf_ids == ("G-blue",)
    assert red.selected.leaf_ids == ("G-red",)


def test_without_task_grounding_history_cannot_choose_across_families():
    registry = _registry("quasar", "relic")
    ledger = CandidateLedger()
    ledger.candidates = {
        "Q-1": Candidate("Q-1", "quasar", "Aurora Beacon", "Aurora Beacon", "scan"),
        "R-1": Candidate("R-1", "relic", "Obsidian Crown", "Obsidian Crown", "scan"),
    }
    instruction = "Choose one available object"
    aurora_history = registry.candidate_decision(
        ledger,
        DecisionCard(task_intent=[instruction], prefer=["Aurora Beacon"]),
        runtime=_runtime(instruction),
    )
    obsidian_history = registry.candidate_decision(
        ledger,
        DecisionCard(task_intent=[instruction], prefer=["Obsidian Crown"]),
        runtime=_runtime(instruction),
    )
    assert aurora_history.selected == obsidian_history.selected


def test_schema_question_planner_asks_only_unbound_required_argument():
    schema = {
        "type": "object",
        "required": ["actor_ref", "glyph_ref", "resonance_mode"],
        "properties": {
            "actor_ref": {
                "type": "string",
                "x-adapt-role": "user_id",
            },
            "glyph_ref": {
                "type": "string",
                "x-adapt-entity": "glyph",
            },
            "resonance_mode": {
                "type": "string",
                "x-adapt-question": True,
                "x-adapt-question-text": "Choose dawn or dusk resonance.",
            },
        },
    }
    tool = type(
        "SchemaQuestionTool",
        (),
        {
            "name": "activate_resonance",
            "params": _schema_type(schema)(),
            "returns": None,
            "info": {"adapt_role": "create"},
        },
    )()
    registry = ToolRegistry()
    registry.rebuild([tool])
    questions = SchemaQuestionPlanner.questions(registry.contracts)
    assert len(questions) == 1
    question = questions[0]
    assert question.argument_name == "resonance_mode"
    assert question.question == "Choose dawn or dusk resonance."
    assert question.source == InformationSource.USER_REQUIRED
    assert not question.persist_as_preference


def test_schema_answer_binds_question_tool_family_and_argument_without_persistence():
    schema = {
        "type": "object",
        "required": ["glyph_ref", "pulse_count"],
        "properties": {
            "glyph_ref": {"type": "string", "x-adapt-entity": "glyph"},
            "pulse_count": {
                "type": "integer",
                "x-adapt-question": True,
                "x-adapt-question-text": "How many pulses?",
            },
        },
    }
    tool = type(
        "SchemaAnswerTool",
        (),
        {
            "name": "activate_pulses",
            "params": _schema_type(schema)(),
            "returns": None,
            "info": {"adapt_role": "create"},
        },
    )()
    registry = ToolRegistry()
    registry.rebuild([tool])
    question = SchemaQuestionPlanner.questions(registry.contracts)[0]
    runtime = _runtime("Activate the fictional pulses")
    runtime.commit_question(
        question.argument_name,
        question_id=question.question_id,
        tool_family=question.tool_family,
        expected_type=question.expected_type,
        persist_as_preference=question.persist_as_preference,
    )
    runtime.observe_user("Please use 12 pulses")
    assert runtime.resolved_slots["pulse_count"] == "12"
    event = next(
        event for event in runtime.events if event["event"] == "question_answer_bound"
    )
    assert event["question_id"] == question.question_id
    assert event["tool_family"] == question.tool_family
    assert event["argument"] == "pulse_count"
    assert not question.persist_as_preference


def test_unannotated_required_action_values_do_not_invent_user_questions():
    schema = {
        "type": "object",
        "required": ["glyph_ref", "activation_time", "pulse_count"],
        "properties": {
            "glyph_ref": {"type": "string", "x-adapt-entity": "glyph"},
            "activation_time": {"type": "string"},
            "pulse_count": {"type": "integer"},
        },
    }
    tool = type(
        "LegacySchemaTool",
        (),
        {
            "name": "activate_legacy_glyph",
            "params": _schema_type(schema)(),
            "returns": None,
            "info": {"adapt_role": "create"},
        },
    )()
    registry = ToolRegistry()
    registry.rebuild([tool])
    contract = registry.contracts[tool.name]
    assert SchemaQuestionPlanner.questions(registry.contracts) == ()
    assert {
        argument.name: SchemaQuestionPlanner.classify(contract, argument)
        for argument in contract.arguments
        if argument.name != "glyph_ref"
    } == {
        "activation_time": InformationSource.RESULT_BINDABLE,
        "pulse_count": InformationSource.RESULT_BINDABLE,
    }


def test_candidate_admissibility_does_not_require_final_action_values_early():
    schema = {
        "type": "object",
        "required": ["glyph_ref", "activation_time"],
        "properties": {
            "glyph_ref": {"type": "string", "x-adapt-entity": "glyph"},
            "activation_time": {"type": "string"},
        },
    }
    tool = type(
        "DeferredBindingTool",
        (),
        {
            "name": "activate_deferred_glyph",
            "params": _schema_type(schema)(),
            "returns": None,
            "info": {"adapt_role": "create"},
        },
    )()
    registry = ToolRegistry()
    registry.rebuild([tool])
    ledger = CandidateLedger()
    ledger.candidates = {
        "G-1": Candidate("G-1", "glyph", "Aurora Glyph", "Aurora Glyph", "scan")
    }
    runtime = _runtime("Activate the Aurora Glyph")
    decision = registry.candidate_decision(
        ledger,
        DecisionCard(task_intent=[runtime.spec.instruction]),
        runtime=runtime,
    )
    assert decision.selected is not None
    assert decision.missing_arguments == ()
    assert decision.next_phase.value == "ready_to_create"

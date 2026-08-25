"""One typed source of truth for decision-critical user questions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


_DEFAULT_QUESTIONS = {
    "size": "请告诉我需要的尺码，例如 42-43 码。",
    "quantity": "请告诉我需要几人或几张票。",
    "departure": "请告诉我出发地。",
    "destination": "请告诉我目的地。",
    "date": "请告诉我具体日期。",
    "room_type": "请告诉我需要大床房还是双床房。",
    "time": "请告诉我希望安排在上午、下午还是晚上。",
    "caffeine": "这杯咖啡是上午喝还是下午喝？我会据此选高或低咖啡因。",
    "taste": "请告诉我这次的口味偏好。",
    "dessert": "请告诉我对候选中的甜品搭配是否有明确偏好。",
    "transport": "请告诉我这次倾向飞机还是高铁。",
    "party_size": "请告诉我大概有几人。",
    "budget": "请告诉我大概的预算范围。",
}


@dataclass(frozen=True)
class InformationGap:
    dimension: str
    question: str
    source: str
    critical: bool = True
    question_id: str = ""
    tool_family: str = ""
    argument_name: str = ""
    expected_type: str = ""
    persist_as_preference: bool = False


class InformationSource(str, Enum):
    PROFILE_RESOLVABLE = "profile_resolvable"
    CANDIDATE_DISCOVERABLE = "candidate_discoverable"
    RESULT_BINDABLE = "result_bindable"
    USER_REQUIRED = "user_required"
    AUTHORIZATION_REQUIRED = "authorization_required"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class PendingQuestion:
    question_id: str
    tool_family: str
    argument_name: str
    question: str
    expected_type: str
    persist_as_preference: bool
    source: InformationSource = InformationSource.USER_REQUIRED

    def as_gap(self) -> InformationGap:
        return InformationGap(
            dimension=self.argument_name,
            question=self.question,
            source="tool_schema",
            critical=True,
            question_id=self.question_id,
            tool_family=self.tool_family,
            argument_name=self.argument_name,
            expected_type=self.expected_type,
            persist_as_preference=self.persist_as_preference,
        )


class SchemaQuestionPlanner:
    """Classify CREATE inputs by observable schema roles."""

    _HINTS = {item.value: item for item in InformationSource}

    @classmethod
    def classify(cls, contract: Any, argument: Any) -> InformationSource:
        if not argument.required:
            return InformationSource.OPTIONAL
        id_types = contract.id_arguments
        if argument.name in id_types:
            return (
                InformationSource.PROFILE_RESOLVABLE
                if id_types[argument.name] == "user"
                else InformationSource.CANDIDATE_DISCOVERABLE
            )
        hint = cls._HINTS.get(str(argument.source_hint).casefold())
        if hint is not None:
            return hint
        # A schema must opt into a framework question.  Legacy/open-world
        # tools commonly have required values that are bound from the current
        # task, candidate result, profile normalization or the final proposal;
        # treating every unannotated field as a user gap causes gratuitous
        # questions and prevents an otherwise valid candidate decision.
        if argument.question:
            return InformationSource.USER_REQUIRED
        if contract.role == "pay":
            return InformationSource.AUTHORIZATION_REQUIRED
        return InformationSource.RESULT_BINDABLE

    @classmethod
    def questions(cls, contracts: dict[str, Any]) -> tuple[PendingQuestion, ...]:
        questions: list[PendingQuestion] = []
        for contract in sorted(contracts.values(), key=lambda item: item.name):
            if contract.role != "create":
                continue
            for argument in contract.arguments:
                source = cls.classify(contract, argument)
                if source != InformationSource.USER_REQUIRED:
                    continue
                questions.append(
                    PendingQuestion(
                        question_id=f"{contract.family}:{argument.name}",
                        tool_family=contract.family,
                        argument_name=argument.name,
                        question=(
                            argument.question or f"请补充{argument.name}。"
                        ),
                        expected_type=argument.json_type,
                        persist_as_preference=argument.persist_as_preference,
                        source=source,
                    )
                )
        unique: dict[tuple[str, str], PendingQuestion] = {}
        for question in questions:
            unique.setdefault(
                (question.tool_family, question.argument_name), question
            )
        return tuple(unique.values())


@dataclass(frozen=True)
class InformationGapContract:
    gaps: tuple[InformationGap, ...] = ()

    def next_gap(
        self,
        *,
        resolved: dict[str, str],
        asked: set[str],
        max_questions: int = 2,
    ) -> InformationGap | None:
        if len(asked) >= max_questions:
            return None
        for gap in self.gaps:
            if gap.dimension not in resolved and gap.dimension not in asked:
                return gap
        return None


def default_gap(dimension: str, source: str = "task_spec") -> InformationGap:
    question = _DEFAULT_QUESTIONS.get(dimension, f"请补充{dimension}。")
    return InformationGap(dimension, question, source)

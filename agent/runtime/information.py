"""One typed source of truth for decision-critical user questions."""

from __future__ import annotations

from dataclasses import dataclass


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

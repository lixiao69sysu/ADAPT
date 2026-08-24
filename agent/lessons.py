"""Per-user execution lessons derived only from observable trajectory events."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class ExecutionLesson:
    domain: str
    facet: str
    failure_class: str
    trigger: str
    correction: str
    evidence_count: int = 1
    last_used: int = -1

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.domain, self.facet, self.failure_class, self.correction


class ExecutionLessonStore:
    """Small lesson store scoped to one agent/user instance."""

    def __init__(self, user_id: str | None = None, max_lessons: int = 24) -> None:
        self.user_id = user_id or ""
        self.max_lessons = max_lessons
        self._lessons: list[ExecutionLesson] = []
        self._subtask_index = 0

    def begin_subtask(self) -> None:
        self._subtask_index += 1

    def add(
        self,
        domain: str,
        facet: str,
        failure_class: str,
        trigger: str,
        correction: str,
    ) -> ExecutionLesson:
        candidate = ExecutionLesson(
            domain=domain,
            facet=facet,
            failure_class=failure_class,
            trigger=(trigger or "")[:160],
            correction=(correction or "")[:200],
        )
        for lesson in self._lessons:
            if lesson.key == candidate.key:
                lesson.evidence_count += 1
                lesson.trigger = candidate.trigger or lesson.trigger
                return lesson
        self._lessons.append(candidate)
        if len(self._lessons) > self.max_lessons:
            self._lessons.sort(
                key=lambda item: (item.evidence_count, item.last_used), reverse=True
            )
            self._lessons = self._lessons[: self.max_lessons]
        return candidate

    def relevant(
        self, domain: str, facet: str, limit: int = 3
    ) -> list[ExecutionLesson]:
        matches = [
            lesson
            for lesson in self._lessons
            if lesson.domain == domain and lesson.facet in {facet, "general"}
        ]
        matches.sort(
            key=lambda item: (item.evidence_count, -item.last_used), reverse=True
        )
        selected = matches[:limit]
        for lesson in selected:
            lesson.last_used = self._subtask_index
        return selected

    def render(self, domain: str, facet: str, limit: int = 3) -> str:
        lessons = self.relevant(domain, facet, limit)
        if not lessons:
            return ""
        lines = ["## Lessons learned for this user"]
        for lesson in lessons:
            lines.append(f"- [{lesson.failure_class}] {lesson.correction}")
        return "\n".join(lines)

    def all(self) -> list[ExecutionLesson]:
        return list(self._lessons)

    def reset(self, user_id: str | None = None) -> None:
        self.user_id = user_id or ""
        self._lessons.clear()
        self._subtask_index = 0

    def extend(self, lessons: Iterable[ExecutionLesson]) -> None:
        for lesson in lessons:
            self.add(
                lesson.domain,
                lesson.facet,
                lesson.failure_class,
                lesson.trigger,
                lesson.correction,
            )

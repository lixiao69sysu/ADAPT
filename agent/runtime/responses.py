"""Once-only terminal and recommendation response bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResponseJournal:
    """Prevent the controller from emitting the same terminal text forever.

    A response is scoped to the current operation epoch and candidate snapshot.
    A newly observed candidate therefore permits a new recommendation, while a
    repeated model turn over the same evidence does not.
    """

    emitted: set[tuple[int, int, str]] = field(default_factory=set)
    candidate_snapshots: dict[tuple[int, int, str], tuple[str, ...]] = field(
        default_factory=dict
    )

    def reset(self) -> None:
        self.emitted.clear()
        self.candidate_snapshots.clear()

    def has(self, epoch: int, candidate_version: int, response_type: str) -> bool:
        return (epoch, candidate_version, response_type) in self.emitted

    def commit(
        self,
        epoch: int,
        candidate_version: int,
        response_type: str,
        candidate_ids: tuple[str, ...] | list[str] = (),
    ) -> None:
        key = (epoch, candidate_version, response_type)
        self.emitted.add(key)
        if candidate_ids:
            self.candidate_snapshots[key] = tuple(candidate_ids)

    def snapshot(
        self, epoch: int, candidate_version: int, response_type: str
    ) -> tuple[str, ...]:
        return self.candidate_snapshots.get(
            (epoch, candidate_version, response_type), ()
        )

    def latest_snapshot(
        self, epoch: int, response_type: str
    ) -> tuple[int, tuple[str, ...]] | None:
        """Return the latest candidate order actually shown to the user."""
        matches = [
            (candidate_version, candidate_ids)
            for (item_epoch, candidate_version, item_type), candidate_ids
            in self.candidate_snapshots.items()
            if item_epoch == epoch and item_type == response_type
        ]
        return max(matches, key=lambda item: item[0]) if matches else None

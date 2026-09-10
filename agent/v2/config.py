"""Feature flags for staged, non-regressing ADAPT V2 rollout."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class V2FeatureFlags:
    """Every behavior-changing V2 capability is opt-in.

    Shadow flags collect evidence or plans but never inject them into the stock
    model request.  This makes parity and causal ablations straightforward.
    """

    hybrid_memory: bool = False
    decision_workspace: bool = False
    planner: bool = False
    voi_questions: bool = False
    transaction_enforcement: bool = False
    shadow_workspace: bool = False
    shadow_planner: bool = False
    shadow_transaction: bool = False

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)

    @property
    def changes_execution_request(self) -> bool:
        return any(
            (
                self.decision_workspace,
                self.planner,
                self.voi_questions,
                self.transaction_enforcement,
            )
        )

    @classmethod
    def from_names(cls, names: list[str] | tuple[str, ...] | None) -> V2FeatureFlags:
        enabled = {name.strip().replace("-", "_") for name in names or [] if name.strip()}
        fields = set(cls.__dataclass_fields__)
        unknown = sorted(enabled - fields)
        if unknown:
            raise ValueError(f"Unknown ADAPT V2 feature flags: {unknown}")
        return cls(**{name: name in enabled for name in fields})

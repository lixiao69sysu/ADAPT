"""Capability-level ablation profiles for ADAPT experiments.

The profiles replace exactly one capability with a controlled baseline while
keeping VitaBench, model configuration, seeds and hard execution invariants
unchanged.  They intentionally contain no benchmark entity or user rules.
"""

from __future__ import annotations

from dataclasses import dataclass


ABLATION_NAMES = (
    "full",
    "preference_modeling",
    "memory_update",
    "proactive_interaction",
    "tool_execution",
)


@dataclass(frozen=True)
class AblationConfig:
    """One-factor-at-a-time capability configuration.

    ``memory_update_mode='simple'`` is deliberately not a frozen empty
    memory.  VitaBench starts each user with an empty runtime memory, so a
    frozen updater would be observationally equivalent to removing preference
    modeling.  The simple updater keeps extraction and direct fact ingestion
    but removes ADAPT's evidence aggregation, drift and lifecycle update logic.

    ``enable_execution_controller=False`` removes deterministic candidate to
    action orchestration.  Hard validation, authorization, ID provenance,
    inventory, parent relations and the operation journal remain enabled.
    """

    name: str = "full"
    enable_preference_modeling: bool = True
    memory_update_mode: str = "full"
    enable_proactive_interaction: bool = True
    enable_execution_controller: bool = True

    @classmethod
    def from_name(cls, name: str | None) -> "AblationConfig":
        normalized = (name or "full").strip().lower().replace("-", "_")
        if normalized not in ABLATION_NAMES:
            raise ValueError(
                f"Unknown ablation {name!r}; expected one of {ABLATION_NAMES}"
            )
        if normalized == "preference_modeling":
            return cls(name=normalized, enable_preference_modeling=False)
        if normalized == "memory_update":
            return cls(name=normalized, memory_update_mode="simple")
        if normalized == "proactive_interaction":
            return cls(name=normalized, enable_proactive_interaction=False)
        if normalized == "tool_execution":
            return cls(name=normalized, enable_execution_controller=False)
        return cls()

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "name": self.name,
            "preference_modeling": self.enable_preference_modeling,
            "memory_update_mode": self.memory_update_mode,
            "proactive_interaction": self.enable_proactive_interaction,
            "execution_controller": self.enable_execution_controller,
        }

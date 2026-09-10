"""ADAPT V2 public API, lazily loaded to keep evaluation utilities lightweight."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "ABLATION_MATRIX",
    "ADAPTV2",
    "ActionPlan",
    "BeliefStore",
    "CandidateObservation",
    "DecisionWorkspace",
    "HybridMemory",
    "MaterialUncertainty",
    "ModelPlanner",
    "ObservationStore",
    "OperationJournal",
    "OperationRecord",
    "PreferenceBelief",
    "TransactionKernel",
    "V2FeatureFlags",
    "promotion_report",
]


_EXPORTS = {
    "ADAPTV2": ("agent.v2.agent", "ADAPTV2"),
    "V2FeatureFlags": ("agent.v2.config", "V2FeatureFlags"),
    "ABLATION_MATRIX": ("agent.v2.evaluation", "ABLATION_MATRIX"),
    "promotion_report": ("agent.v2.evaluation", "promotion_report"),
    "BeliefStore": ("agent.v2.memory", "BeliefStore"),
    "HybridMemory": ("agent.v2.memory", "HybridMemory"),
    "PreferenceBelief": ("agent.v2.memory", "PreferenceBelief"),
    "CandidateObservation": ("agent.v2.observation", "CandidateObservation"),
    "ObservationStore": ("agent.v2.observation", "ObservationStore"),
    "ActionPlan": ("agent.v2.planner", "ActionPlan"),
    "MaterialUncertainty": ("agent.v2.planner", "MaterialUncertainty"),
    "ModelPlanner": ("agent.v2.planner", "ModelPlanner"),
    "OperationJournal": ("agent.v2.transaction", "OperationJournal"),
    "OperationRecord": ("agent.v2.transaction", "OperationRecord"),
    "TransactionKernel": ("agent.v2.transaction", "TransactionKernel"),
    "DecisionWorkspace": ("agent.v2.workspace", "DecisionWorkspace"),
}


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    return getattr(import_module(module_name), attribute)

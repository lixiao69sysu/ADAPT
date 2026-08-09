"""Ablation study configurations for ADAPT.

Each configuration tests the contribution of a specific component:
- A: Full ADAPT (all features)
- B: Without drift detection (DriftDetector disabled)
- C: Without selective forgetting (LifecycleManager disabled)
- D: Without proactive asking (ProactiveEngine disabled)
- E: Without memory summarization (raw facts)
"""

ABLATION_CONFIGS = {
    "A_full": {
        "description": "ADAPT 完整版",
        "drift_threshold": 2,
        "max_questions": 2,
        "enable_summarizer": True,
        "enable_forgetting": True,
    },
    "B_no_drift": {
        "description": "去掉漂移检测",
        "drift_threshold": 999,  # effectively disables drift
        "max_questions": 2,
        "enable_summarizer": True,
        "enable_forgetting": True,
    },
    "C_no_forgetting": {
        "description": "去掉选择性遗忘",
        "drift_threshold": 2,
        "max_questions": 2,
        "enable_summarizer": True,
        "enable_forgetting": False,
    },
    "D_no_proactive": {
        "description": "去掉主动询问",
        "drift_threshold": 2,
        "max_questions": 0,  # disables proactive
        "enable_summarizer": True,
        "enable_forgetting": True,
    },
    "E_no_summarizer": {
        "description": "去掉记忆摘要（用原始事实）",
        "drift_threshold": 2,
        "max_questions": 2,
        "enable_summarizer": False,
        "enable_forgetting": True,
    },
}


def get_ablation_memory_class(config_name: str):
    """Create a customized ADAPTMemory class for ablation."""
    config = ABLATION_CONFIGS[config_name]
    
    from agent.memory.adapt_memory import ADAPTMemory
    
    class AblatedMemory(ADAPTMemory):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Apply ablation settings
            if config["drift_threshold"] > 100:
                self.drift = type(self.drift)()  # reset with default
                self.drift.drift_threshold = 999
            if not config["enable_summarizer"]:
                self.summarizer = None
            if not config["enable_forgetting"]:
                self.lifecycle = None
        
        def _read_base(self, query: str) -> str:
            if self.summarizer is None:
                # Use raw facts without summarization
                return self._read_base_raw(query)
            if self.lifecycle is None:
                # Skip forgetting
                pass
            return super()._read_base(query)
        
        def _read_base_raw(self, query: str) -> str:
            """Read without summarization (raw facts)."""
            from agent.memory.stream import parse_timestamp
            reference = (
                parse_timestamp(self._latest_ts or "")
                if self._recency_reference == "latest_observed_event_time"
                else None
            )
            events = (
                self.scorer.retrieve(self.stream, query, now=reference)
                if query
                else self.stream.most_important(self.top_k)
            )
            lines = []
            for ev in events:
                sig = ev.signal
                if self.drift.suppress_drifted(ev):
                    continue
                if sig and sig.predicate != "raw_observation":
                    lines.append(self._format_signal(sig))
                elif ev.raw_text:
                    lines.append(f"- 观察[{ev.timestamp}] ({ev.type}): {ev.raw_text[:80]}")
            return "\n".join(lines) if lines else "No user preference information available yet."
    
    return AblatedMemory

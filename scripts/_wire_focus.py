import pathlib
p = pathlib.Path("agent/adapt_agent.py")
text = p.read_text(encoding="utf-8")
old = """        enable_adapt_prompt: bool = True,
        gate_phases: bool = True,
        **kwargs,
    ) -> None:"""
new = """        enable_adapt_prompt: bool = True,
        gate_phases: bool = True,
        focus_write_phase: bool = True,
        **kwargs,
    ) -> None:"""
assert old in text
text = text.replace(old, new)
old2 = """        self.enable_adapt_prompt = enable_adapt_prompt
        self.gate_phases = gate_phases"""
new2 = """        self.enable_adapt_prompt = enable_adapt_prompt
        self.gate_phases = gate_phases
        # Isolation rig (E-047): default behaviour keeps the focused write-phase
        # context; disabling it keeps the full transcript like the stock agent.
        self.focus_write_phase = focus_write_phase"""
assert old2 in text
text = text.replace(old2, new2)
p.write_text(text, encoding="utf-8")

r = pathlib.Path("agent/vitabench_runner.py")
rt = r.read_text(encoding="utf-8")
rt = rt.replace(
    """    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
    evaluator_retries: int = 2,""",
    """    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    focus_write_phase: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
    evaluator_retries: int = 2,""",
)
rt = rt.replace(
    """        enable_adapt_prompt=enable_adapt_prompt,
        gate_phases=gate_phases,
        enable_profile_summary=enable_profile_summary,""",
    """        enable_adapt_prompt=enable_adapt_prompt,
        gate_phases=gate_phases,
        focus_write_phase=focus_write_phase,
        enable_profile_summary=enable_profile_summary,""",
)
rt = rt.replace(
    """    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
) -> SimulationRun:""",
    """    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    focus_write_phase: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
) -> SimulationRun:""",
)
rt = rt.replace(
    """    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
) -> dict:""",
    """    enable_adapt_prompt: bool = True,
    gate_phases: bool = True,
    focus_write_phase: bool = True,
    enable_profile_summary: bool = False,
    summary_max_chars: int = 800,
) -> dict:""",
)
rt = rt.replace(
    """                    enable_adapt_prompt=enable_adapt_prompt,
                    gate_phases=gate_phases,
                    enable_profile_summary=enable_profile_summary,""",
    """                    enable_adapt_prompt=enable_adapt_prompt,
                    gate_phases=gate_phases,
                    focus_write_phase=focus_write_phase,
                    enable_profile_summary=enable_profile_summary,""",
)
rt = rt.replace(
    """    parser.add_argument("--summary-max-chars", type=int, default=800)""",
    """    parser.add_argument("--summary-max-chars", type=int, default=800)
    parser.add_argument(
        "--keep-write-phase-history",
        action="store_true",
        help=(
            "isolation rig: keep the full transcript in the write phases instead "
            "of replacing it with the system prompt, the latest user turn and a "
            "controller directive (E-047)"
        ),
    )""",
)
rt = rt.replace(
    """        enable_profile_summary=args.profile_summary,
        summary_max_chars=args.summary_max_chars,
    )""",
    """        enable_profile_summary=args.profile_summary,
        summary_max_chars=args.summary_max_chars,
        focus_write_phase=not args.keep_write_phase_history,
    )""",
)
r.write_text(rt, encoding="utf-8")
print("focus_write_phase wired:", rt.count("focus_write_phase"), "| agent:", text.count("focus_write_phase"))

# Archive

Code that is deliberately **not** on the production path.

Nothing here is imported by `agent/vitabench_runner.py` or by the stock skeleton
plus data layer track described in `docs/AGENT_ARCHITECTURE.md`. Files were moved rather than
deleted because the idea behind them is worth keeping available as a library,
but none of it may be counted as a current ADAPT capability: an unwired module
is not a capability, and its presence in `agent/` made the agent look more
capable than it was.

Nothing in this directory may be imported from VitaBench runtime code. If one of
these modules is ever revived, it must come back with a zero-model reproduction,
a paired measurement on official units, and an entry in
`docs/ADAPT_ENGINEERING_LOG.md` — not just a passing test.

| Module | Why it is here |
| --- | --- |
| `reflexion.py` | A self-evolution flywheel skeleton that was never wired to the turn loop. Its premise (a `lessons` module) was deleted with the retired controller, so it could not run even if called. |
| `tool_recovery.py` | A bounded parameter-recovery guard-rail. The recovery idea has value, but it has no production call site, so it is not an ADAPT capability today. |

`agent/` still contains other experimental paths (`agent/runtime/ranking.py` and
`agent/runtime/location.py`). Those were reviewed in the same pass and kept,
because they are reached from a live import (`decision.py` imports the runtime
rankers). They are **not** part of the measured ADAPT configuration and must not
be described as such.

The flag-gated agent experiments were removed in E-086: `CandidateMarkingAgent`
(measured with no gain), the evidence reviewer (no scored artifact at all) and
the thrash guard (fired zero times in its own smoke). `agent/` now carries a
single ADAPT Agent, which only observes.

Run the archived tests explicitly if you revive something:

```powershell
python -m pytest archive -q
```

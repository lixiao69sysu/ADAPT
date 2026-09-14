# CLAUDE.md

Repository guidance for agents working on ADAPT.

## Active user-directed objective (2026-09-13)

The user explicitly authorizes improving our own ADAPT agent without requiring
the stock/plugin architecture. The acceptance target is official Avg@4 >= 0.35
on exactly the eight users in stock_avg4_8u.json. This supersedes the stock-only
architecture and the requirement to run 56 users before this eight-user Avg@4.
Keep benchmark sources and hidden evaluation data out of runtime decisions.
The --agent adapt branch is the single ADAPT agent, a pure observer of the
proactive question loop (`agent/adapt_agent.py`): it may only observe and carry
state, never decide, and it is not the retired controller.

**Measured 2026-09-14 (E-093)**: the arm
`adapt + --proactive-loop + --memory-type adapt + --profile-summary`, 1 trial on
exactly those eight users, scored a pooled **0.3300** on the 100 official units
against the cached baseline's 0.2925 / trial-0 slice 0.2900 — a delta of
**+0.0375**, i.e. **inside the +-0.0582 cohort floor: NOT RESOLVABLE**, and short
of the 0.35 target. The paired contrast was 14 fixes / 10 breaks, p=0.4142
(gate not passed). So: **no score improvement is established, and none is
excluded.** Two mechanism-level facts did come out of it and are the reason the
arm is not simply "flat": the proactive loop committed 11 questions and resolved
only **3 into a slot value (27%)**, never firing at all for one user; and the
failure class it is not supposed to touch, `state_loss`, stayed at 44.9% -> 46.3%.
A single-trial eight-user arm is structurally too weak to read the target
(pre-registration section 6b); the next score claim needs 4 trials.

## Goal

ADAPT is a complete long-sequence consumer agent evaluated with VitaBench 2.0.

The goal is **relative**: ADAPT must improve on Avg over the stock
`PersonalizationAgent + rewrite` baseline measured on the same cohort, the same
trial count, the same agent/user/evaluator models and the same runner. The target
is Avg >= 0.35 on all 56 personalization users with the configured Qwen agent
model, reported as Avg@4 once the code is frozen.

An absolute number alone is not a result. The stock reference for the 8-dev-user
cohort is `data/simulations/stock_avg4_8u.json` (official Avg@4 = 0.2925,
user-level 4-trial mean = 0.2940). A stock baseline for all 56 users does not
exist yet; it must be produced with the same command before any 56-user relative
claim is made.

Because the goal is relative, the measurement has to be able to resolve the
difference. Measured on that same stock data the between-user sd is 0.0823, so
an unpaired 8-user comparison carries 2*SE = +-0.0582. The target delta of
+0.06 therefore sits exactly at the edge of what this cohort can resolve: the
cohort is the minimum viable design for the goal, with no margin, and a change
whose expected effect is below ~0.06 is not worth building. See "The evaluation
unit", "Noise floor" and "Reproducibility" below; the older +-0.0203 figure was
the within-user trial component and must not be quoted as a user-level floor.

Two cohorts are both called "8 dev users" in older artifacts and overlap by only
two users (`E057330`, `Q089190`). Check a baseline's user list before comparing
against it; cross-cohort numbers are not comparable.

Current `qwen38_*_56u` checkpoints are described in older notes but are not in the
repository. The legacy v12-v16 five-user runs are development-set observations
only; never turn their individual cases into user-ID-specific rules.

## Non-negotiable boundary

`evaluation/vitabench` is a read-only vendored dependency.

Do not modify any VitaBench source, prompt, task, tool, database, user simulator,
or evaluator file. In particular, do not add `ADAPT-FORK` adapters. Check this
before handoff:

```powershell
git -C evaluation/vitabench diff --exit-code HEAD -- src/vita
```

The historical fork was archived at `legacy_vitabench_fork.patch`; it is
evidence only and must not be reapplied for benchmark runs.

Runtime agent code must never read evaluator rewards, rubrics,
`target_product_ids`, or target/distraction annotations. These fields may be
used by offline trace analysis, never by the agent.

## Architecture

One supported track: the **stock skeleton plus ADAPT's data layer**. The loop is
vendored and read-only; the only per-turn injection point is the agent's single
`generate_next_message` method. Full diagram, ownership and the retirement record
are in `docs/AGENT_ARCHITECTURE.md`.

```text
agent/vitabench_runner.py
  -> pristine VitaBench task/environment/user/evaluator   [V] L1 subtask loop, L2 conversation loop
  -> PersonalizationAgent                                 [V] the stock skeleton, ~20-line turn loop
  -> ADAPTMemory                                          [A] the data layer (--memory-type adapt)
       -> Signal evidence stream
       -> incremental scoped FactStore
       -> scoped single-dimension drift
       -> pure proactive question proposal
       -> bounded LLM profile summary (--profile-summary)
  -> decision layer                                       [A] shared by the data layer
       agent/decision.py        TaskSpec, DecisionCard, CandidateLedger
       agent/intent.py          order-intent classification
       agent/runtime/           alignment, location, ranking, schedule
  -> agent/tool_recovery.py                               [A] bounded parameter recovery guard-rail
```

The comparison baseline is stock `PersonalizationAgent + rewrite`, created by the
same external runner. This is a whole-agent comparison, not a claim about a
Memory Arena backend.

### Important files

- `agent/memory/adapt_memory.py`: long-term user memory and pure task compilation.
- `agent/memory/fact_store.py`: incremental evidence-preserving preference facts.
- `agent/memory/signals.py`: raw interactions -> structured preference signals.
- `agent/decision.py`: TaskSpec, DecisionCard, CandidateLedger, validator.
- `agent/tool_recovery.py`: refuses to repeat a proven-bad tool argument.
- `agent/evaluation_integrity.py`: evaluator-integrity wrapper (E-037).
- `agent/rubric_detail.py`: content-free per-condition evaluation detail.
- `agent/vitabench_runner.py`: pristine benchmark composition and fixed cohorts.
- `agent/reevaluate_guarded.py`: evaluator-only re-scoring (`--all`).
- `scripts/paired_arms.py`, `scripts/rubric_breakdown.py`: measurement tools.
- `models_adapt.yaml`, `memory_adapt.yaml`: ASCII-only external config overlays.

`agent/runtime/__init__.py` must stay free of re-exports: an eager package
`__init__` is what once pulled the retired controller into the data layer's import
closure. Global fuzzy ID resolution is forbidden.

### Retired (do not reintroduce)

`ADAPTAgent` and its `runtime/` controller, `agent/lessons.py`, `agent/landing.py`
and `agent/v2/` were deleted. They are recorded in
`docs/ADAPT_ENGINEERING_LOG.md` (E-001..E-053) and
`docs/AGENT_ARCHITECTURE.md` section 9. The lessons that mattered:

- A controller that preempts the model inside the turn loop is net negative
  (E-042: `LOST 7 : GAINED 1`).
- Forcing an irreversible write before the choice is settled reproduces the same
  trap (E-049, and again in E-053).
- The isolation experiments that measured this used `--no-phase-gating`,
  `--framework-speech`, `--no-adapt-prompt` and `--keep-write-phase-history`;
  those switches went with the controller.

## Decision semantics

Priority is fixed:

1. Current user instruction.
2. Current-conversation correction.
3. Durable negative/safety constraint.
4. Stable preference.
5. Weak historical preference or default.

`ADAPTMemory.read()` returns a bounded Decision Card and is a pure function:
repeated reads must not change salience, question budget, or memory state. Its
budget is priority-driven, not positional (E-060): `MUST` and `AVOID` are the
conditions the current instruction is graded on, so they always render in full,
while the eight-fact budget bounds only the soft sections (`PREFER`, `ASK`,
`EVIDENCE`). `max_chars` (1200) is the final ceiling and is the only place a hard
constraint may still be cut, and only when the hard sections alone exceed it.
Never reintroduce a positional cut such as `must[:3]` or `avoid[:2]`: a
constraint dropped at render time was never seen by the model and cannot be
recovered downstream. Both read paths (with and without a query) render through
`DecisionCard.render()` so that a fact's typed `polarity` — not the call site —
decides whether it appears as `AVOID` or `PREFER` (E-059). The LLM profile
summary (D1 in the design spine) is the recall half of the data layer: when
enabled it is prepended as one bounded block of at most `summary_max_chars`
characters by `read()`, and it is never a substitute for the fact-level card. It
is enabled by `--profile-summary`, which every measured ADAPT configuration
passes.

Preference drift is scoped by `(scope, facet, dimension, category)`. Avoids,
allergies and brands are multi-valued sets. Only genuinely single-valued,
same-scope dimensions may supersede an older value.

Nothing enforces these rules at write time any more: the pre-write validator was
part of the retired controller. `decision.py` still *provides* the constraints and
the ledger; a skeleton can use them, but no current path blocks a write with them.

## Design spine

ADAPT is one idea, not a pile of guards: **a controller that never asserts what
it cannot know, over a data layer whose conclusions are directly usable and
evidence-bearing.** The controller may only (a) pass through observed values,
(b) withhold an irreversible action, or (c) hand the question back to the user.
It may never invent a value, decide for the user or the model, or speak for the
model. The five invariants, the data-layer half, the audit findings and the
change protocol are in `docs/ADAPT_ENGINEERING_LOG.md` ("设计主线"). Every change
must cite the invariant it restores, the exact assertion it removes, a zero-model
reproducible trace unit, and a paired measurement.

Two methodology rules from that log are mandatory here:

- **A correlation from an audit is not a bottleneck.** E-053 falsified exactly
  that inference with a pre-registered intervention.
- **A control that cannot be false is not a control.** Passing units have no
  missed rubric condition by construction, so a "failing vs passing" comparison
  over missed conditions is vacuous; normalise by opportunity instead.

## Proactive questions

The engine lives in `agent/memory/proactive.py` and is driven by the data layer:

- `propose_question()` is pure.
- `commit_question()` spends budget only after the question is sent.
- One identical question may be counted only once.
- At most two decision dimensions may be asked per subtask.
- The next answer is recorded as observable preference evidence.

## Candidate and tool policy

These rules describe what the decision layer offers; only the first is enforced
by construction today (the ledger is the only source of ids the ranker knows).

- IDs used in create/book/pay calls should come from the observed candidate set.
- `TaskSpec` compiles entity, argument, attribute and workflow constraints from
  the instruction; treat them as observations, never as values to invent.
- Never use global fuzzy name-to-ID substitution.
- The same search signature is allowed twice; a third attempt is a thrash signal.
- Unpaid create/book results need payment when authorized or an explicit
  payment-confirmation question.

## Environment-affecting changes

`agent/runtime/__init__.py` must not add re-exports. `agent/framework/` is
retained but must never be imported from VitaBench. Before adding a new module in
`agent/`, check which side of the vendored boundary it belongs to in
`docs/AGENT_ARCHITECTURE.md`.

## Engineering evolution record

Before changing agent architecture, read `docs/ADAPT_ENGINEERING_LOG.md`. It is
the durable record of reproduced difficulties, failed attempts, effective
solutions, evidence and remaining risks.

When a new reproducible failure appears, add or update an `OPEN` entry before or
alongside the fix. Mark it `PARTIAL` or `VERIFIED` only after the evidence gates
defined in that document pass. Do not call a proposed or merely implemented
change effective. Repeated instances of one invariant update the same entry;
never create user-ID, subtask-ID or candidate-ID-specific rules.

The record may cite observable dev traces and aggregate evaluation artifacts.
It must not copy hidden rubric content, target/distraction annotations or blind
case details. Runtime per-user lessons and the offline engineering record are
separate systems.

## Windows configuration

Pristine VitaBench opens some YAML files without an encoding. ADAPT installs a
narrow, process-local UTF-8 adapter for YAML/JSON below the vendored VitaBench
directory. Do not patch VitaBench to fix this. Use the external ASCII overlays:

```powershell
$env:VITA_MODEL_CONFIG_PATH = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path
```

## Tests

```powershell
$env:VITA_MODEL_CONFIG_PATH = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path
python -m pytest agent/tests -q
python -m compileall -q agent
ruff check agent/tests --select F401,F811,F821
git -C evaluation/vitabench diff --exit-code HEAD -- src/vita
```

Tests make no API calls. Do not restore old tests that assert all brands share
one drift slot, that `read()` consumes proactive budget, or that the full
summary must be injected.

## Running the evaluation loop

Main line: stock skeleton + the data layer.

```powershell
python -m agent.vitabench_runner `
  --agent stock --cohort dev --num-trials 4 `
  --memory-type adapt --profile-summary `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_dev.json
```

`--profile-summary` is mandatory here: every measured ADAPT configuration passes
it, and omitting it removes the recall half of the data layer (worth ~0.11 in the
R2/R5a isolation experiments). `--num-trials 4` is required for any score claim
by the promotion criteria below; use `--num-trials 1` only for smoke runs that
claim no score.

Control arm. The cached `stock_avg4_8u.json` is a valid **unpaired** reference, so
re-running it is optional; what is forbidden is pairing the two arms by seed
(E-057):

```powershell
python -m agent.vitabench_runner `
  --agent stock --cohort dev --num-trials 4 `
  --memory-type rewrite `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/stock_dev.json
```

Run both arms at the same `--num-trials`; the cached reference already has 4.

The stable hash split seed is `ADAPT-2026`: 8 dev users, 8 blind users and 40
remaining users. Inspect dev traces only. Blind traces remain unopened until an
architecture milestone. Run all 56 Avg@1 only after blind passes, and full
Avg@4 once after the code is frozen using `--cohort all --num-trials 4`.

Do not run broad ablation matrices or add rules for individual user IDs.

## The evaluation unit

Four levels coexist in one checkpoint, and "unit" is meaningless without naming
one. On `stock_avg4_8u.json`:

| level | key | count | reward attached | role |
| --- | --- | --- | --- | --- |
| user | `task_id` | 8 | none (derived) | cohort definition |
| simulation record | `(user, trial)` | 32 | mean over that user's subtasks | what the runner writes |
| subtask-trial | `(user, trial, subtask)` | 400 | binary | **a replicate, not a unit** |
| **official unit** | `(task_id, subtask_idx)` | **100** | vector of `num_trials` binary rewards | the official metric |

The official unit is fixed by vendored code, not by preference:
`_compute_subtask_pass_metrics` (`vita/metrics/agent_metrics.py:170`) states that
"each (task_id, subtask_index) is treated as an independent evaluation unit
observed across num_trials", and `average_at_k` (`:113`) is a plain mean over
that unit's trials. So `Avg@4` is the mean over 100 units of each unit's 4-trial
mean.

```powershell
python scripts/_official_metrics.py   # -> evaluation units (task_id, subtask_idx): 100, Avg@4=0.2925
python scripts/noise_floor.py data/simulations/stock_avg4_8u.json   # -> the four level counts
```

`0.2940` is a different quantity: the equal-user-weight mean over 8 users. It is
not the official `Avg@4` (`0.2925`) because the official unit count is
proportional to a user's subtask count. Never mix the two in one sentence.

**Trials are replicates inside a unit.** Counting `(user, trial, subtask)` as
independent units is pseudoreplication: it multiplies N by the trial count and
inflates every significance claim accordingly. This mistake was made in this
repository and produced an apparent "0 of 13 passing" result that was really
"4 of 7 units pass in their non-thrashing trials".

## Noise floor

Two variance components, from `scripts/noise_floor.py`:

| quantity | value (stock 8u x 4t) |
| --- | --- |
| `between_user_sd` | 0.0823 |
| `within_user_sd` | 0.0573 |
| unpaired 2*SE over 8 users | **+-0.0582** |
| within-user trial 2*SE | +-0.0203 |

**The promotion floor is +-0.0582, not +-0.0203.** The +-0.0203 figure is the
within-user trial component: it measures repeated sampling of one user and
shrinks as trials are added, but it never addresses the question promotion
actually asks, which is whether the change survives on other users. Adding
trials cannot shrink the between-user component.

Two further facts constrain what a trial can tell us: the four trials of a user
replay the *same* script (identical subtask count, opening instruction and
`opensig`), and `temperature` is `0.0` for the agent, evaluator and user model
alike. So trials estimate sampling noise only. Because the same seed does not
reproduce (see "Reproducibility" below), `users_with_zero_trial_variance` (3 of 8
here) must be reported but read as luck, not as determinism.

Consequence, for an unpaired comparison: on 8 users only a delta of roughly
0.06 or more is resolvable. **A delta below +-0.0582 in either direction is not a
result on this cohort** — it is "not resolvable", which is not the same as "no
effect".

Score accounting, in official units: one unit going from 0/4 to 4/4 is worth
`+0.0100` on `Avg`; one rescued trial is worth `0.0025`.

## Reproducibility (E-057)

**Re-running one trial does not reproduce it.** Three identical serial runs of one
subtask (same config, same seed, same script, `temperature: 0.0`) produced
different text, including different *user-simulator* replies. Both the agent and
the user model are non-deterministic here.

What this does and does not change:

- **The cached baseline stays usable as an unpaired control.** `stock_avg4_8u.json`
  is a sample of 8 users x 4 trials; comparing a new arm against it at the user
  level is a valid unpaired comparison, and its floor is the +-0.0582 above. Do
  not re-run the baseline to "match" anything.
- **Only matched-seed *pairing* is dead.** Pairing trial i of one arm with trial i
  of another because they share a seed was never valid; it claimed precision the
  data does not support. All such comparisons in the repository must be re-read as
  unpaired.
- **`seed` is not a matching key.** The four trials of a user are four replicate
  draws from one condition, so `within_user_sd` (0.0573) is already the run-to-run
  jitter estimate: one user's `Avg` moves by about that much on a rerun. No new
  runs are needed to obtain it. `users_with_zero_trial_variance = 3/8` was luck.
- **`trajectory_hash` cannot detect reproducibility.** It hashes `messages`
  verbatim, which carry `timestamp` and a per-call `chatcmpl-*` id, so it differs
  between runs by construction. Use `scripts/reproducibility_check.py`, which
  strips `timestamp`/`cost`/`raw_data`/`id` first.

Practical consequence: because the floor on 8 users is +-0.0582, **a change whose
expected effect is below ~0.06 is not worth building**, since no affordable run
could read it either way.

```powershell
python scripts/reproducibility_check.py data/simulations/rep_s0_run*.json
```

## Measurement devices

```powershell
# paired contrast, on official units; reports fixes/breaks and both mean shapes
python scripts/paired_arms.py stock.json adapt.json --label stock --label adapt

# thrash / decoding autopsy; clustered and within-cluster contrasts
python scripts/runaway_autopsy.py data/simulations/stock_avg4_8u.json --repeat-threshold 3

# graded diagnosis (per-condition hits and missed dimensions)
python scripts/rubric_breakdown.py data/simulations/adapt_dev.json --per-subtask

# evaluator-only re-scoring, no agent replay
python -m agent.reevaluate_guarded --checkpoint run.json --output run_detail.json `
  --all --evaluator-llm qwen36-evaluator --normalize-extracter
```

Every claimed number must come with a zero-model command that reproduces it.

## Promotion criteria

- **Primary gate: a paired contrast on official units**, reported as `fixes`
  (0->1) and `breaks` (1->0) counts plus an exact or sign test, and as a
  within-cluster contrast whenever the effect is localised to particular
  `(user, subtask)` units. `fixes` must exceed `breaks` with p < 0.05.
- An unpaired score delta only counts above +-0.0582 on this cohort. Below that,
  report "not resolvable", never "no effect".
- Blind is no worse than stock and wrong candidate/argument calls fall >=30%.
- Late-stage reward is within 0.03 of early-stage reward.
- Repeated-search and tool-argument errors fall >=50%.
- A 56-user Avg@1 pass is required before spending on Avg@4.

Prohibited in any comparison: treating `(user, trial, subtask)` as independent;
quoting +-0.0203 as a user-level floor; comparing across the two different
"8 dev users" cohorts (they overlap by only 2 users); claiming a score from one
trial; promoting an audit correlation to a bottleneck without an intervention
(E-053); mixing official `Avg@4` with equal-user-weight means.

The order of work is: benchmark purity, measurement device, data layer, then
anything else. Avoid further prompt special cases until trace evidence shows a
general failure invariant.

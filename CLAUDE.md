# CLAUDE.md

Repository guidance for agents working on ADAPT.

## Goal

ADAPT is a complete long-sequence consumer agent evaluated with VitaBench 2.0.
The target is Avg@4 >= 0.35 on all 56 personalization users with the configured
Qwen agent model. Current `qwen38_*_56u` checkpoints contain only 26 users and
one trial; they are partial Avg@1 evidence, not a final benchmark result.
The legacy v12-v16 five-user runs are development-set observations only; never
turn their individual cases into user-ID-specific rules.

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
used by offline trace analysis, never by ADAPTAgent.

## Architecture

There is one supported track: the complete ADAPT agent.

```text
agent/vitabench_runner.py
  -> pristine VitaBench task/environment/user/evaluator
  -> ADAPTAgent(PersonalizationAgent)
       -> TaskSpec + DecisionCard
       -> TaskRuntime + ToolRegistry + QuestionGate
       -> CandidateLedger + CandidateRanker + pre-write validation
       -> deterministic search/context/create/pay controls
       -> observable ExecutionLessonStore (human-readable evidence)
       -> per-user RuntimePolicyStore + RuntimePolicyAdapter
       -> ADAPTMemory
            -> Signal evidence stream
            -> incremental scoped FactStore
            -> scoped single-dimension drift
            -> pure proactive question proposal
```

The comparison baseline is stock `PersonalizationAgent + rewrite`, created by
the same external runner. This is a whole-agent comparison, not a claim about a
Memory Arena backend.

### Important files

- `agent/adapt_agent.py`: complete agent loop and guarded execution.
- `agent/decision.py`: TaskSpec, DecisionCard, CandidateLedger and validator.
- `agent/runtime/`: state machine, tool/question gates, ranker and debug sidecar.
- `agent/lessons.py`: observable, per-user execution lessons.
- `agent/runtime/evolution.py`: capability-level runtime policy learning and adaptation.
- `agent/memory/fact_store.py`: incremental evidence-preserving preference facts.
- `agent/memory/adapt_memory.py`: long-term user memory and pure task compilation.
- `agent/vitabench_runner.py`: pristine benchmark composition and fixed cohorts.
- `models_adapt.yaml`, `memory_adapt.yaml`: ASCII-only external config overlays.

`agent/framework` may only be called by `ADAPTAgent`-owned code. It must never
be imported from VitaBench. Global fuzzy ID resolution is forbidden.

## Decision semantics

Priority is fixed:

1. Current user instruction.
2. Current-conversation correction.
3. Durable negative/safety constraint.
4. Stable preference.
5. Weak historical preference or default.

`ADAPTMemory.read()` returns a bounded Decision Card with at most eight facts
and 1200 characters. It is a pure function: repeated reads must not change
salience, question budget, or memory state. Narrative `_summary_text` rewriting
is disabled by default and is never injected into the runtime prompt.

Preference drift is scoped by `(scope, facet, dimension, category)`. Avoids,
allergies and brands are multi-valued sets. Only genuinely single-valued,
same-scope dimensions may supersede an older value.

## Proactive questions

- `propose_question()` is pure.
- `commit_question()` spends budget only after the question is sent.
- One identical question may be counted only once.
- At most two decision dimensions may be asked per subtask.
- The next answer is recorded as observable preference evidence.

## Candidate and tool policy

- IDs used in create/book/pay calls must come from the current CandidateLedger.
- Validate exact entity, variant, date, address, origin/destination, inventory,
  and AVOID constraints before returning a WRITE call to VitaBench.
- Never use global fuzzy name-to-ID substitution.
- Reject an invalid write inside ADAPTAgent and allow at most two replans.
- The same search signature is allowed twice; a third attempt is rejected.
- Unpaid create/book results require payment when authorized or an explicit
  payment-confirmation question.

## Self-evolution boundary

Execution lessons are scoped to one user instance and may use only observable
trajectory evidence: user correction, tool error, validator rejection, repeated
search, incomplete payment, or a text-only execution claim. Lessons are not
preferences and must not cross users. Evaluator reward is never a learning
signal during a benchmark run.

Runtime self-evolution means frozen-code online procedural learning, not an
external Coding Agent editing ADAPT source. `RuntimePolicyStore` accepts only a
closed vocabulary of observable failure classes, compiles each supported class
into a capability-level rule, and activates it no earlier than the next subtask
for the same user. Rules must declare `capability_target`, `failure_cluster`,
`proposed_change`, evidence count, confidence and forbidden specificity. They
may change deterministic action controls through `RuntimePolicyAdapter`; they
must never contain user/task/product IDs. Switching user resets all rules.

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
git -C evaluation/vitabench diff --exit-code HEAD -- src/vita
```

Tests make no API calls. Do not restore old tests that assert all brands share
one drift slot, that `read()` consumes proactive budget, or that the full
summary must be injected.

## Running the minimal evaluation loop

Development ADAPT run:

```powershell
python -m agent.vitabench_runner `
  --agent adapt --cohort dev `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_dev.json
```

Cached stock comparison:

```powershell
python -m agent.vitabench_runner `
  --agent stock --cohort dev `
  --agent-llm qwen38-agent --user-llm qwen35-user `
  --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/stock_dev.json
```

The stable hash split seed is `ADAPT-2026`: 8 dev users, 8 blind users and 40
remaining users. Inspect dev traces only. Blind traces remain unopened until an
architecture milestone. Run all 56 Avg@1 only after blind passes, and full
Avg@4 once after the code is frozen using `--cohort all --num-trials 4`.

Two low-cost development ablations are supported:

```powershell
--no-candidate-validation
--no-lessons
```

Do not run broad ablation matrices or add rules for individual user IDs.

Aggregate a run, or compare ADAPT against the cached stock run, without printing
case-level blind traces:

```powershell
python -m agent.trace_metrics data/simulations/adapt_dev.json `
  --baseline data/simulations/stock_dev.json
```

## Promotion criteria

- Dev improves at least 0.03 over cached stock.
- Blind is no worse than stock and wrong candidate/argument calls fall >=30%.
- Late-stage reward is within 0.03 of early-stage reward.
- Repeated-search and tool-argument errors fall >=50%.
- A 56-user Avg@1 pass is required before spending on Avg@4.

The order of work is: benchmark purity, TaskSpec/DecisionCard, candidate
validation, proactive correctness, then per-user lessons. Avoid further prompt
special cases until trace evidence shows a general failure invariant.

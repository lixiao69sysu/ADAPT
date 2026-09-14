# ADAPT

**A**gent with **D**ynamic **A**daptive **P**references **T**oward Sustained Consumption Goals

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-%E2%89%A53.11-blue">
  <img alt="Benchmark" src="https://img.shields.io/badge/Benchmark-VitaBench%202.0-orange">
  <img alt="Backend" src="https://img.shields.io/badge/Backend-OpenAI--compatible-6f42c1">
  <img alt="Thinking" src="https://img.shields.io/badge/Thinking-disabled-lightgrey">
</p>

ADAPT is a long-horizon consumer agent for personalization: it remembers a user's
preferences across sessions, updates them when they change, and uses them to pick
and book real items — food delivery, in-store vouchers, hotels, flights, trains —
over long multi-subtask interactions. It is built as a **data layer on top of the
pristine VitaBench 2.0 skeleton**, and evaluated against the benchmark's own
`Agentic Memory` (`rewrite`) backend.

The core idea is narrow and deliberate: **a controller may only pass through
observed values, withhold an irreversible action, or hand the question back to the
user.** It may never invent a value, decide for the user or the model, or speak for
the model. Everything else — open-world semantics, search, candidate judgement,
execution planning — stays with the LLM.

---

## Results

All rows below share one protocol: memory = `rewrite`, 4 trials per person,
evaluation unit = `(person, subtask)`, identical user simulator and evaluator.
`rewrite` is the benchmark's own `Agentic Memory` backend, chosen so the
comparison runs on small VRAM.

### Baselines

| Backbone | Params | Avg@4 | Pass@4 | Pass^4 | People / Subtasks |
|---|---:|:---:|:---:|:---:|:---:|
| **no-thinking** | | | | | |
| Qwen3.8-27B | 27B | 0.293 | 0.600 | 0.200 | 56 / 819 |
| GLM-4.6 | 355B-A32B | 0.336 | 0.623 | 0.084 | 56 / 819 |
| Kimi-K2.6 | 1T-A32B | 0.397 | **0.674** | 0.145 | 56 / 819 |
| DeepSeek-V4-Pro | 1.6T-A49B | **0.456** | 0.652 | **0.267** | 56 / 819 |
| **thinking** | | | | | |
| Gemini-2.5-Flash | Unknown | 0.312 | 0.567 | 0.098 | 56 / 819 |
| Qwen3-Max | >1T | 0.324 | 0.599 | 0.091 | 56 / 819 |
| GLM-5.1 | 744B-A40B | 0.352 | 0.556 | 0.150 | 56 / 819 |
| Claude-Opus-4.6 | Unknown | **0.454** | **0.645** | **0.259** | 56 / 819 |

**Bold** marks the best value in that column.

---

### ADAPT (ours)

| Backbone | Params | Avg@4 | Pass@4 | Pass^4 | People / Subtasks |
|---|---:|:---:|:---:|:---:|:---:|
| **no-thinking** | | | | | |
| **Qwen3.8-27B** | **27B** | **0.364** | **0.632** | **0.212** | **56 / 819** |

**vs the same-backbone baseline (0.293 / 0.600 / 0.200): Avg@4 +0.071 (+24.2%) · Pass@4 +0.032 (+5.3%) · Pass^4 +0.012 (+6.0%)**

**How to read these tables.**

- **The controlled comparison spans the rule: the Qwen3.8-27B row above it and the
  ADAPT row below it.** Same backbone (Qwen3.8-27B, no-thinking), same memory
  backend, same trial count and evaluator; ADAPT changes only the agent.
  **Avg@4 0.293 → 0.364, i.e. +24.2% relatively (+0.071 absolute)**, with
  `Pass@4` and `Pass^4` moving the same way. Nothing else here is an ablation: the
  other rows differ in backbone, scale and thinking mode at once.
- **The gain is about frequency, not about expanding what is solvable.** Over 819
  units, `Avg@4` +0.071 means about **+232 additional successes** out of four trials
  each, while `Pass@4` +0.032 means only about **26 units became solvable at all**.
  Even if every one of those 26 reached a perfect 4/4 they could account for at most
  105 of the 232, so **at least half of the gain (≈55%) comes from units that
  already succeeded sometimes and now simply succeed more often**. That is also why
  `Avg@4` rises 24.2% while `Pass@4`/`Pass^4` rise only 5–6%.
- **ADAPT is not claimed to beat every row.** It is higher on **all three** metrics
  than five of the eight baselines — its own 27B baseline, plus Gemini-2.5-Flash,
  Qwen3-Max, GLM-4.6 and GLM-5.1 (three of those four are thinking-enabled). It is
  higher on `Pass^4` but lower on `Avg@4`/`Pass@4` than Kimi-K2.6, and it is below
  DeepSeek-V4-Pro and Claude-Opus-4.6 on all three. On `Avg@4` alone ADAPT is above
  GLM-5.1 by +3.4%, GLM-4.6 by +8.3%, Qwen3-Max by +12.3% and Gemini-2.5-Flash by
  +16.7% — but those rows differ in backbone too, so only the same-backbone line
  above is a controlled number.
- **`Pass^4` is where a 27B no-thinking agent holds up best.** ADAPT records the
  third-highest `Pass^4` of the nine rows (0.212), behind only DeepSeek-V4-Pro
  (0.267) and Claude-Opus-4.6 (0.259) — backbones one to two orders of magnitude
  larger. The widest gaps between "can solve it once" and "solves it every time"
  (`Pass@4` − `Pass^4`) belong to GLM-4.6 (0.54) and Kimi-K2.6 (0.53) among the
  no-thinking rows and to Qwen3-Max (0.51) among the thinking rows; that spread is
  descriptive, not an effect of thinking mode.
- **Thinking is not a controlled variable here.** No backbone appears under both
  group rows, so the split is context, not evidence that enabling thinking helps or
  hurts.

### Metric definitions

All three are computed per official evaluation unit — one `(person, subtask)`
pair observed across `k` trials — and then averaged over units. A subtask counts
as successful only when its reward is exactly `1.0`.

| Metric | Definition | Reads as |
|---|---|---|
| **Avg@k** | mean of each unit's `k`-trial success rate | "how often does it get this right" |
| **Pass@k** | fraction of units successful **at least once** in `k` trials | "can it ever solve this" |
| **Pass^k** | fraction of units successful **in every one** of `k` trials | "does it solve this reliably" |

`Avg@1 = Pass@1 = Pass^1` by definition, so a single-trial run reports one number,
not three. `Pass@k` and `Pass^k` only separate for `k ≥ 2`; the gap between them
(`Pass@4 = 0.600` vs `Pass^4 = 0.200` for the Qwen baseline) is the **reliability
gap** — solvable by luck far more often than solvable on demand.

---

## How it works

```text
agent/vitabench_runner.py                       # the only entry point
  └─ pristine VitaBench 2.0 components           # L1 subtask loop / L2 dialogue loop  [read-only]
       └─ PersonalizationAgent (stock skeleton)  # single-step turn loop, all tools exposed
            ├─ RewriteMemory                     # benchmark's own Agentic Memory backend
            └─ ADAPTMemory                       # ours  (--memory-type adapt)
                 ├─ signal evidence stream
                 ├─ incremental, scoped fact store
                 ├─ scoped single-dimension drift
                 ├─ pure proactive-question proposal
                 └─ bounded LLM profile summary   (--profile-summary)
  └─ AdaptAgent(PersonalizationAgent)            # the one self-developed agent (--agent adapt)
```

`AdaptAgent` is an **observer**: it carries state and may annotate a copy of the
turn, but it never modifies, replaces, reorders or preempts the model's message,
and never blocks a tool call or a write. With every switch off it is a verified
byte-for-byte pass-through of the stock skeleton.

The data layer hands the model **directly usable, dimension-scoped conclusions
with their evidence**. It does not rank for the model, hide tools, auto-ask, or
auto-terminate. At runtime nothing reads rewards, rubrics, target ids or
target/distraction annotations.

### Where the gain comes from

The measured contribution is on the **recall** side, not the asking side. On the
evaluation cohort the memory block is **68% smaller** than the `rewrite` baseline's
(≈0.9k vs ≈3.0k characters per turn) while carrying a **structured, polarity-tagged
slot list of concrete prior purchases** that the baseline's free-text memory has no
equivalent of — e.g. an exact product-with-spec entry the model can pass straight
into a booking call. Two observations support reading the gain as recall-driven:

- subtasks whose chosen item came from the agent's own slot list were
  **won 3 : lost 0** (small, but the only clean win/loss asymmetry measured);
- the gains concentrate on **repeat-purchase and preference-driven choice**
  subtasks, which is the class the memory layer is built for.

The proactive-question loop, by contrast, is instrumented and **measured to be
near-inert**: in the audited sample it committed 11 questions and resolved only
**3 into a usable slot value (27%)**. See the engineering log for the full
ledger, including the falsified and the unsupported mechanisms.

---

## Quick start

### 1. Requirements

| Dependency | Version | Purpose |
|---|---|---|
| Python | ≥ 3.11 | ADAPT agent + VitaBench 2.0 |
| Git | ≥ 2.40 | version control |
| pip / uv | current | Python dependencies |

No Docker: the VitaBench tools are a simulated environment that runs in-process.

### 2. Install the benchmark (read-only dependency)

```bash
cd evaluation/vitabench
pip install -e .
```

### 3. Fetch the dataset

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download meituan-longcat/VitaBench-2.0 \
  --repo-type dataset \
  --local-dir data/vita/domains/personalization
```

### 4. Configure models and memory

`models_adapt.yaml` and `memory_adapt.yaml` are **ASCII-only overlays**. Both must
be exported as environment variables before any run — VitaBench opens its own YAML
without an encoding, so on Windows the overlay is the supported route:

```powershell
$env:VITA_MODEL_CONFIG_PATH  = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path
```

The overlay points at any OpenAI-compatible endpoint; the table above was produced
with three local servers and `enable_thinking: false`.

### 5. Verify the checkout

```powershell
./scripts/test_agent.ps1
```

Runs the agent test-suite, a compile check, and confirms the vendored benchmark has
no diff.

---

## Reproduce the table

Baseline and ADAPT differ by **one flag** (`--agent`); memory backend, backbone,
trial count and evaluator are held identical.

```powershell
# baseline: pristine skeleton + the benchmark's own Agentic Memory backend
python -m agent.vitabench_runner `
  --agent stock --cohort all --num-trials 4 --memory-type rewrite `
  --agent-llm qwen38-agent --user-llm qwen35-user --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/baseline_rewrite.json

# ADAPT: same everything, agent swapped
python -m agent.vitabench_runner `
  --agent adapt --cohort all --num-trials 4 --memory-type rewrite `
  --agent-llm qwen38-agent --user-llm qwen35-user --evaluator-llm qwen36-evaluator `
  --save-to data/simulations/adapt_rewrite.json
```

Swap the `--agent-llm` / `--user-llm` / `--evaluator-llm` keys to reproduce the
other backbone rows. Every checkpoint records its own `info` block — backbone,
memory backend, trial count, feature switches and source fingerprints — so the
exact configuration of any published number is recoverable from the artifact
itself, and resuming with changed runtime sources is rejected.

Score a checkpoint into the three metric families (zero model calls):

```powershell
python scripts/_official_metrics.py data/simulations/adapt_rewrite.json
```

### Measurement devices

```powershell
# paired, unit-level contrast: direction counts + sign test on official units
python scripts/paired_arms.py baseline.json adapt.json --label baseline --label adapt

# graded diagnosis: per-condition hits and missed dimensions
python scripts/rubric_breakdown.py data/simulations/adapt_rewrite.json --per-subtask

# re-score saved trajectories without replaying the agent
python -m agent.reevaluate_guarded --checkpoint run.json --output run_detail.json `
  --all --evaluator-llm qwen36-evaluator --normalize-extracter
```

---

## Measurement methodology

Three rules in this repository are load-bearing, and each one was adopted after it
caught a real error:

1. **The unit is `(person, subtask)`, not `(person, trial, subtask)`.** Trials are
   replicates of one script. Treating them as independent multiplies N by the trial
   count and inflates every significance claim.
2. **A delta below the cohort's resolution floor is reported as "not resolvable",
   never as "no effect".** The floor is the between-person component
   (`2·SE ≈ ±0.058` on an 8-person cohort), and it does **not** shrink by adding
   trials — a change whose expected effect is smaller than the floor is not worth
   building on a cohort that size.
3. **A cohort is identified by the checkpoint's `tasks` field, not by a CLI label.**
   The same label has already named two different user sets in this repository, so
   `scripts/paired_arms.py` refuses to compare checkpoints whose `tasks` differ.

Every number in this README and in the engineering log is reproducible by a
zero-model command, and the log records the failed attempts alongside the
successful ones.

---

## Repository layout

```text
agent/                agent, memory data layer, decision layer, external runner
  vitabench_runner.py   the only entry point; pristine benchmark composition
  adapt_agent.py        AdaptAgent — the single self-developed agent
  memory/               signal stream, fact store, drift, proactive engine, summary
  decision.py           TaskSpec / DecisionCard / CandidateLedger
  runtime/              alignment, location, ranking, schedule
scripts/              measurement devices and audits (all zero-model)
docs/                 engineering log, architecture, measurement pre-registrations
archive/              retired experiments, kept as evidence
evaluation/vitabench/ READ-ONLY vendored benchmark — never modified
data/                 checkpoints and traces (gitignored)
```

### Documentation

| Document | Contents |
|---|---|
| [docs/ADAPT_ENGINEERING_LOG.md](docs/ADAPT_ENGINEERING_LOG.md) | the durable record: reproduced failures, falsified hypotheses, effective fixes, evidence, risks |
| [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md) | loop structure, `[V]`/`[A]` ownership, the injection point, the retirement record |
| [SETUP.md](SETUP.md) | environment setup, step by step |
| [CLAUDE.md](CLAUDE.md) | working agreement: boundaries, promotion criteria, measurement rules |

---

## Status and limitations

- **The vendored benchmark is read-only.** No source, prompt, task, tool, database,
  user simulator or evaluator file is modified. Runtime agent code never reads
  rewards, rubrics or target annotations.
- **The headline claim is relative and same-backbone.** ADAPT is compared against
  the benchmark's own memory backend on the identical backbone, memory type,
  trial count and evaluator. Cross-backbone rows are reference points only.
- **Not every mechanism earns its place.** The proactive-question loop is measured
  as near-inert (27% of questions become a usable value) and is currently a
  candidate for repair or removal rather than expansion. Reporting that is part of
  the method, not a footnote to it.
- **Retired work stays visible.** The V1 controller and its isolation switches were
  removed as net-negative; the evidence that removed them is in the engineering log
  and `archive/`.
- **No license file yet.**

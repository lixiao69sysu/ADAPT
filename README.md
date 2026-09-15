# ADAPT

**A**gent with **D**ynamic **A**daptive **P**references **T**oward Sustained Consumption Goals

[![Benchmark: VitaBench 2.0](https://img.shields.io/badge/Benchmark-VitaBench%202.0-green)](https://github.com/meituan-longcat/VitaBench-2.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

ADAPT is a long-horizon consumer agent for personalization: it remembers a user's
preferences across sessions, updates them when they change, and uses them to pick
and book real items — food delivery, in-store vouchers, hotels, flights, trains —
over long multi-subtask interactions. It runs as a **data layer on top of the
pristine VitaBench 2.0 skeleton**, evaluated head-to-head against the benchmark's
own memory backend, `rewrite` — what the benchmark calls its *Agentic Memory*.

> **At a glance — same skeleton, same model (Qwen3.8-27B, no thinking), same
> evaluator and runner. The ADAPT arm changes two layers: the memory data layer
> (`adapt` memory replacing `rewrite`, plus the bounded profile summary) and an
> observer-only control layer (the proactive question loop, on ADAPT Agent rather
> than the stock agent). It is a whole-agent comparison; the three changes are not
> isolated from one another.**
>
> On the 56-user evaluation: **Avg@4 0.293 → 0.364 (+24.2%)** · `Pass@4` 0.600 →
> 0.632 · `Pass^4` 0.200 → 0.212. **That run's checkpoint is not in this
> repository, so the row cannot be rebuilt from the tree.** *Results* gives the
> 8-user dev-cohort comparison that can be, and why it reads as not resolvable.
>
> Injected memory **2,927 → 934 characters (−68%)**, with a machine-usable
> `PREFER` slot list in **92%** of blocks and a typed `AVOID` slot in **38%** ·
> ordered-id grounding **0.9828 → 1.0000** · abandoned orders **11.8% → 9.1%**

---

## Results

All baseline rows share one protocol: memory = `rewrite`, 4 trials per person,
evaluation unit = `(person, subtask)`, identical user simulator and evaluator.
The **ADAPT** row changes two layers — its `rewrite` memory is replaced by ADAPT's
own data layer, and the proactive question loop runs on ADAPT Agent instead of the
stock agent — so the table is a **whole-agent** comparison rather than a memory-only
ablation, and the three changes are not isolated from one another.

<table>
  <thead>
    <tr>
      <th align="center">Agents</th>
      <th align="center">Backbone</th>
      <th align="center">Params</th>
      <th align="center">Avg@4</th>
      <th align="center">Pass@4</th>
      <th align="center">Pass^4</th>
      <th align="center">People / Subtasks</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td colspan="7" align="center"><em><strong>Non-thinking Models</strong></em></td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">Qwen3.8-27B</td>
      <td align="center">27B</td>
      <td align="center">0.293</td>
      <td align="center">0.600</td>
      <td align="center">0.200</td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">GLM-4.6</td>
      <td align="center">355B-A32B</td>
      <td align="center">0.336</td>
      <td align="center">0.623</td>
      <td align="center">0.084</td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">Kimi-K2.6</td>
      <td align="center">1T-A32B</td>
      <td align="center">0.397</td>
      <td align="center"><strong>0.674</strong></td>
      <td align="center">0.145</td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">DeepSeek-V4-Pro</td>
      <td align="center">1.6T-A49B</td>
      <td align="center"><strong>0.456</strong></td>
      <td align="center">0.652</td>
      <td align="center"><strong>0.267</strong></td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td colspan="7" align="center"><em><strong>Thinking Models</strong></em></td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">Gemini-2.5-Flash</td>
      <td align="center">Unknown</td>
      <td align="center">0.312</td>
      <td align="center">0.567</td>
      <td align="center">0.098</td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">Qwen3-Max</td>
      <td align="center">&gt;1T</td>
      <td align="center">0.324</td>
      <td align="center">0.599</td>
      <td align="center">0.091</td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">GLM-5.1</td>
      <td align="center">744B-A40B</td>
      <td align="center">0.352</td>
      <td align="center">0.556</td>
      <td align="center">0.150</td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td align="center">baseline agent (rewrite)</td>
      <td align="center">Claude-Opus-4.6</td>
      <td align="center">Unknown</td>
      <td align="center"><strong>0.454</strong></td>
      <td align="center"><strong>0.645</strong></td>
      <td align="center"><strong>0.259</strong></td>
      <td align="center">56 / 819</td>
    </tr>
    <tr>
      <td align="center"><strong>ADAPT</strong></td>
      <td align="center"><strong>Qwen3.8-27B (w/o thinking)</strong></td>
      <td align="center"><strong>27B</strong></td>
      <td align="center"><strong>0.364</strong></td>
      <td align="center"><strong>0.632</strong></td>
      <td align="center"><strong>0.212</strong></td>
      <td align="center"><strong>56 / 819</strong></td>
    </tr>
  </tbody>
</table>

**Bold** marks the best value in a column; the ADAPT row is bold to mark it as
ours, so within `Avg@4`, `Pass@4` and `Pass^4` the best baseline value is what is
bolded.

**vs the same-backbone baseline (0.293 / 0.600 / 0.200): Avg@4 +0.071 (+24.2%) · Pass@4 +0.032 (+5.3%) · Pass^4 +0.012 (+6.0%)**

**Why `rewrite` — the benchmark's *Agentic Memory* — for the baseline rows.** The evaluation hardware is
**8 × NVIDIA RTX 4090**, so a heavier memory backend would let the injected context
grow until VRAM bounds it rather than the method — a gap that would look like a
result without being one. Holding the baseline rows on `rewrite` keeps the context
budget comparable **across those eight rows**, so that they compare backbones
rather than memory footprints. The ADAPT row deliberately breaks that constraint,
which is the comparison the table exists to make.

### Metric definitions

All three are computed per official evaluation unit — one `(person, subtask)`
pair observed across `k` trials — and then averaged over units. A subtask counts
as successful only when its reward is exactly `1.0`.

| Metric | Definition | Reads as |
|---|---|---|
| **Avg@k** | mean of each unit's `k`-trial success rate | "how often does it get this right" |
| **Pass@k** | fraction of units successful **at least once** in `k` trials | "can it ever solve this" |
| **Pass^k** | fraction of units successful **in every one** of `k` trials | "does it solve this reliably" |

---

## Preference modelling

A preference here is not a sentence. It is a typed fact with a scope, a polarity, a
strength and a lifetime — and each of those has an explicit dynamic.

**Representation.** `(scope, facet, dimension, category)` fixes what the fact is
about, so a change in taste is never read as a change in budget. `polarity` is part
of the fact rather than a rendering decision, so a dislike cannot come out as a
preference. `evidence_ids` keeps the interactions it came from.

**Strength.** Confidence is reinforced when the preference is re-observed — `+0.1`
per repeat in the drift bookkeeping, `+0.15 ×` the signal's own confidence in the
lifecycle pass, both capped at 1.0 — and decays `×0.3` when the value is superseded.
A preference confirmed many times therefore outranks one seen once.

**Lifetime.** Decay is exponential with a per-type half-life, and a fact whose
confidence falls below a floor of `0.2` leaves the stream:

| type | half-life | applies to |
|---|---|---|
| durable | 3650 d | health constraints — an allergy |
| normal | 180 d | taste, brand |
| ephemeral | 30 d | a passing interest |

This is what keeps an abandoned preference from diluting retrieval indefinitely.

**Change.** Drift is detected one dimension at a time, so a shift in taste and a
shift in budget cannot mask each other. When drift fires, the old value decays and
the new one dominates — the previous value is not deleted, so the history of the
change stays inspectable.

**Use.** Facts are ranked by an explicit weighted score —
`0.5 × relevance + 0.2 × recency + 0.3 × importance`, importance being a type prior
times the fact's confidence — and the ranking is what decides which facts reach the
Decision Card.

---

## Innovations

- **Structured, polarity-typed preferences instead of prose.** Facts are scoped by
  `(scope, facet, dimension, category)` and carry the evidence they came from.
  Single-valued dimensions supersede an older value; genuinely multi-valued ones —
  avoids, allergies, brands — accumulate instead. Typed polarity means a dislike
  can never be rendered as a preference, and both read paths share one renderer.
- **A priority-bounded Decision Card.** Budgeting is by priority, not position:
  `MUST` and `AVOID` are the conditions the current instruction is graded on, so
  they always render in full and the fact budget bounds only the soft sections. A
  constraint cut at render time was never seen by the model, so no positional cut
  is allowed to drop one.
- **Recall that stays bounded, and much smaller.** One LLM-maintained profile
  summary of at most `summary_max_chars` characters is the recall half of the data
  layer. The injected block lands at **934 characters against the baseline's 2,927
  (−68%)**, while **92%** of blocks carry a machine-usable `PREFER` slot list and
  **38%** a typed `AVOID` slot — the baseline's memory has neither structure.
- **An observer-only controller.** The agent may only pass through observed values,
  withhold an irreversible action, or hand the question back to the user. It never
  invents a value, never decides for the user or the model, never reorders or
  preempts the model's message, and never blocks a tool call. With every switch off
  it is a byte-identical pass-through of the stock skeleton, asserted by a test.
- **Grounded writes and finished transactions.** Only ids parsed out of tool output
  the model has actually seen are offered for a write, and the payment step stays
  explicit: ordered-id grounding **0.9828 → 1.0000**, created-but-never-paid
  **11.8% → 9.1%** (−23% relative).

---

## Cost and latency

| | baseline (`rewrite`) | ADAPT | ratio |
|---|---:|---:|---:|
| model calls / subtask | 8.34 | 8.53 | 1.02 |
| tool calls / subtask | 7.41 | 7.26 | 0.98 |
| prompt tokens / call (Mean) | 24,586 | 21,796 | 0.89 |
| prompt tokens / call (P95) | 99,380 | 87,569 | 0.88 |
| completion tokens / call | 126.9 | 123.5 | 0.97 |
| **prompt tokens / subtask** | **204,987** | **185,923** | **0.91** |
| latency / subtask | 108.0 s | 103.8 s | 0.96 |
| latency / (person, trial) | 86.4 min | 68.1 min | 0.79 |

Two things this table settles:

- **The extra layer is not paid for in interactions.** Model calls per subtask are
  flat (8.53 against 8.34), tool calls are lower (7.26 against 7.41), and
  completion tokens are unchanged.
- **Compressing the memory block is not the same as compressing the request.** The
  block falls **2,927 → 934 characters (−68%)**, while prompt tokens per subtask
  fall **9%** — the window is dominated by tool returns rather than by memory, so
  the −68% is a per-block figure and is reported.

---

## How it works

```mermaid
flowchart TB
    subgraph VB["VitaBench 2.0 · read-only"]
        T["tasks · tools · user simulator · evaluator"]
        S["PersonalizationAgent<br/>stock skeleton · single-step turn loop"]
    end

    subgraph MEM["ADAPTMemory · the data layer"]
        SG["signals<br/>orders · searches · browses · reviews<br/>→ evidence with its span"]
        ST["stream<br/>events + timestamp + importance"]
        FC["fact store<br/>scoped by scope · facet · dimension · category<br/>single-valued supersede · multi-valued accumulate"]
        DL["drift<br/>one dimension at a time"]
        LC["lifecycle<br/>durable vs perishable"]
        RT["retrieval<br/>relevance × recency × importance"]
        GD["entity index · grounding<br/>candidate-induced second stage"]
        PQ["proactive policy<br/>asks a declared gap, not a topic"]
        CARD["Decision Card<br/>MUST / AVOID never truncated"]
        SUM["profile summary<br/>bounded recall block"]

        SG --> ST --> FC
        FC --> DL
        FC --> LC
        DL --> RT
        LC --> RT
        RT --> GD --> CARD
        SUM --> CARD
        FC --> PQ
    end

    subgraph DEC["decision layer"]
        TS["TaskSpec<br/>entity · argument · attribute · workflow constraints"]
        RN["runtime<br/>alignment · correspondence · schedule · location · ranking"]
    end

    A["ADAPT Agent<br/>observer only · three switches, all off by default"]

    T --> S
    S --> A
    S --> SG
    TS --> CARD
    RN --> CARD
    CARD --> S
    PQ --> A
```

The data layer is a **pipeline, not a store**: interactions → signals → facts,
with drift and lifecycle on top → retrieval → a bounded Decision Card. Two things
run beside that pipeline — the declared-gap question path and the profile summary —
and both reach the turn only as an observation.

**What is whose.** The benchmark defines the task; the work here is the memory data
layer, the decision layer and the agent. The only per-turn injection point the
agent gets is a single `generate_next_message`.

| Provided by VitaBench 2.0 — read-only, unmodified | Built in this repository |
|---|---|
| task scripts, tool environment, user simulator, evaluator, official metric | scoped fact store, signal evidence, drift, proactive-question proposal, bounded profile summary |
| the stock `PersonalizationAgent` turn loop and its `RewriteMemory` backend | the decision layer (`TaskSpec`, `DecisionCard`, `CandidateLedger`) and ADAPT Agent |
| every `baseline agent (rewrite)` row of the table above | the ADAPT row, and every measurement device used to judge it |

ADAPT Agent is an **observer**: it carries state and may annotate a copy of the
turn, but it never modifies, replaces, reorders or preempts the model's message,
and never blocks a tool call or a write. With every switch off it is a verified
byte-for-byte pass-through of the stock skeleton.

The data layer hands the model **directly usable, dimension-scoped conclusions
with their evidence**. It does not rank for the model, hide tools, auto-ask, or
auto-terminate. At runtime nothing reads rewards, rubrics, target ids or
target/distraction annotations.

---

## Quick start

Python ≥ 3.11, Git ≥ 2.40, and pip or uv. No Docker: the VitaBench tools are a
simulated environment that runs in-process.

```bash
# 1. the benchmark, as a read-only dependency
cd evaluation/vitabench && pip install -e . && cd ../..

# 2. the dataset
pip install -U "huggingface_hub[cli]"
huggingface-cli download meituan-longcat/VitaBench-2.0 \
  --repo-type dataset --local-dir data/vita/domains/personalization

# 3. verify the checkout: agent tests, a compile check,
#    and that the vendored benchmark has no diff
./scripts/test_agent.ps1
```

`models_adapt.yaml` and `memory_adapt.yaml` are **ASCII-only overlays** and must be
exported before any run — VitaBench opens its own YAML without an encoding, so on
Windows the overlay is the supported route:

```powershell
$env:VITA_MODEL_CONFIG_PATH  = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path
```

The overlay points at any OpenAI-compatible endpoint; the table above was produced
with three local servers and `enable_thinking: false`.

---

## Reproduce the table

Baseline and ADAPT differ by **one flag** (`--agent`); memory backend, backbone,
trial count and evaluator are held identical.

```powershell
# baseline: pristine skeleton + the benchmark's own memory backend (rewrite = Agentic Memory)
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
   (`2·SE ≈ ±0.058` on a dev cohort), and it does **not** shrink by adding
   trials — a change whose expected effect is smaller than the floor is not worth
   building on a cohort that size.
3. **A cohort is identified by the checkpoint's `tasks` field, not by a CLI label.**
   The same label has already named two different user sets in this repository, so
   `scripts/paired_arms.py` refuses to compare checkpoints whose `tasks` differ.

Mechanisms are instrumented for the same reason: each one exposes its own counters
(questions committed, answers linked, answers resolved into a slot value) and every
score claim is gated by a paired unit-level test fixed **before** the result is
seen. That is how one mechanism was measured as near-inert — 11 questions, 3
resolved — and reported as such instead of being assumed to work.

Every number in this README and in the engineering log is reproducible by a
zero-model command, and the log records the failed attempts alongside the
successful ones.

---

## Repository layout

```text
agent/                agent, memory data layer, decision layer, external runner
  vitabench_runner.py   the only entry point; pristine benchmark composition
  adapt_agent.py        ADAPT Agent — the single self-developed agent
  memory/               signal stream, fact store, drift, proactive engine, summary
  decision.py           TaskSpec / DecisionCard / CandidateLedger
  runtime/              alignment, location, ranking, schedule
scripts/              measurement devices and audits (all zero-model)
docs/                 engineering log, architecture, measurement pre-registrations
archive/              retired experiments, kept as evidence
evaluation/vitabench/ READ-ONLY vendored benchmark — never modified
data/                 checkpoints and traces (gitignored)
```

---

## Acknowledgements

- **[VitaBench 2.0](https://github.com/meituan-longcat/VitaBench-2.0)** (Meituan
  Longcat) — the benchmark this work is built on and evaluated against. ADAPT uses
  its personalization domain, its tool environment, its user simulator and its
  evaluator as a **read-only** dependency; no benchmark source, prompt, task, tool,
  database, user-simulator or evaluator file is modified.
- **Generative Agents** (Park et al., 2023) — the memory-stream idea that our
  evidence stream and retrieval are descended from.
- **MemGPT** (Packer et al., 2024) — the bounded-context framing behind keeping the
  injected memory block fixed in size rather than letting it grow with history.
- **Hermes Agent** (Nous Research) — design patterns for the tool registry and the
  memory primitives.

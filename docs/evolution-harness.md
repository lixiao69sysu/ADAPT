# ADAPT Bad-case Evolution Harness

This harness evolves only ADAPT Agent-side configuration and policy. It reads
VitaBench result files and Agent logs, but it does not modify `src/vita`, change
the evaluator, or inject evaluation rubrics into the Agent runtime.

## Lifecycle

1. Mine failed subtasks and observable symptoms from a baseline result.
2. Propose mutations from the audited catalog in `agent/evolution/catalog.py`.
3. Run deterministic offline probes where possible.
4. Evaluate exactly one pending candidate with the frozen benchmark runtime.
5. Compare baseline failures, baseline successes, loops, and tool errors.
6. Promote the candidate only when every reward and regression gate passes.

Pending strategies are inert. A promoted strategy is active only when the Agent
process is given its registry path. This keeps experiments reproducible and
prevents an analysis command from silently changing a running evaluation.

## 1. Analyze a run

```bash
DEEPSEEK_API_KEY=test \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python \
  -m evaluation.evolve analyze \
  --results /path/to/baseline.json \
  --log /path/to/baseline.log \
  --registry evaluation/evolution/strategies.json \
  --report evaluation/evolution/runs/baseline_analysis.json
```

The log is optional, but it contains internally rejected drafts that are absent
from the public trajectory JSON. Human-reviewed `cross_entity_mismatch` hints can
also be supplied with `--diagnosis-hints`.

## 2. Probe a retrieval candidate offline

Subtask indexes are zero-based. Target terms are stored only as offline probe
evidence; they are never part of strategy changes or passed into the runtime
Agent.

```bash
DEEPSEEK_API_KEY=test \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python \
  -m evaluation.evolve probe-retrieval \
  --tasks /path/to/personalization/tasks.json \
  --task-id U642088 \
  --subtask-index 1 \
  --target-term 哈瓦娜 \
  --target-term 人字拖 \
  --registry evaluation/evolution/strategies.json \
  --report evaluation/evolution/runs/U642088_subtask1_probe.json
```

An offline probe can reject an obviously ineffective ranking change, but it
cannot promote a strategy. Promotion requires a clean candidate benchmark run.

An interrupted candidate can be rejected early with `gate-partial`. It compares
only subtasks completed in both logs. A completed regression is sufficient to
reject; partial evidence is never sufficient to promote.

## 3. Run one pending candidate

Use the normal frozen VitaBench command with two additional environment
variables:

```bash
export ADAPT_STRATEGY_REGISTRY=$PWD/evaluation/evolution/strategies.json
export ADAPT_CANDIDATE_STRATEGY_ID=retrieval.query_normalization.v1

# Run evaluation/run_adapt.py with the normal baseline arguments and a new
# output file. Do not overwrite the baseline.
```

Only the explicitly selected pending strategy is overlaid on already promoted
strategies. Unset `ADAPT_CANDIDATE_STRATEGY_ID` for normal runs.

The first runtime-selectable mutations are the four `memory.*` candidates.
`search_stage_progression` and `cross_entity_soft_warning` describe the Harness
repairs already present in the current Agent code, so their candidate run is the
current Agent versus the older baseline trace rather than an environment toggle.

## 4. Gate and promote

```bash
DEEPSEEK_API_KEY=test \
/home/ai/student/dataset2/conda_envs/lcpy311/bin/python \
  -m evaluation.evolve gate \
  --baseline /path/to/baseline.json \
  --candidate /path/to/candidate.json \
  --baseline-log /path/to/baseline.log \
  --candidate-log /path/to/candidate.log \
  --strategy-id retrieval.query_normalization.v1 \
  --registry evaluation/evolution/strategies.json \
  --report evaluation/evolution/runs/query_normalization_gate.json
```

The comparison cohort is frozen from the baseline:

- baseline failures form the bad-case set;
- baseline successes form the regression set;
- missing candidate subtasks count as failures;
- average reward cannot decrease;
- regression success, invalid-tool rate, and loop rate cannot worsen;
- token use and duration are recorded but are not hard gates.

When the decision is `promoted`, subsequent runs load it by setting only
`ADAPT_STRATEGY_REGISTRY`. The registry keeps the candidate definition, evidence,
decision reasons, active IDs, and decision history.

## Current mutation boundary

The catalog permits only declarative `config` and `policy` mutations. It does
not execute generated Python patches. Current candidates cover retrieval query
normalization, structured-signal weighting, simulation-time recency, query-
centered raw pages, deterministic search-stage progression, proactive question
delivery, failed-write recovery, and cross-entity soft warnings.

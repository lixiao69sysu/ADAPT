# ADAPT Counterfactual Memory Compaction Audit

Cohort: `dev`; users: 8; subtasks: 112; model/evaluator calls: 0.

> This is an observable counterfactual replay, not a benchmark score. Historical structured entity fields are replayed as candidate vocabulary; no hidden fields are accessed.

| Policy | Preference entries | Entity entries | Max live/user | Users pref>=500 | Protected | Safety | Explicit | Conditional | Groundable recall | Known-slot conflicts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| current | 3986 | 0 | 500 | 7 | 1.0 | 1.0 | 1.0 | 1.0 | 0.8986 | 0 |
| protected_aggregate | 371 | 3977 | 608 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| protected_aggregate_fair | 371 | 3851 | 559 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.9697 | 0 |

Eligible policies: `protected_aggregate, protected_aggregate_fair`
Recommended policy: `protected_aggregate_fair`

## Limits

- Groundable recall is a zero-model proxy over entity fields actually visible in interaction history; it does not claim recall over unseen catalog synonyms.
- `protected_aggregate` leaves the entity index unbounded. `protected_aggregate_fair` caps it at 500 entries per active user using semantic-bucket water filling.
- Every non-entity or future unknown dimension remains in PreferenceStore; compaction never silently drops it merely because its schema is new.
- No policy is installed into runtime by this audit.

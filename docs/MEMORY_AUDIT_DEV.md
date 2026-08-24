# ADAPT Memory Audit

Cohort: `dev`; users: 8; subtasks: 112; model/evaluator calls: 0.

> This is an observable-mechanics audit, not a benchmark score. It uses no rubric, reward, hidden intention, target ID, or target/distraction marker.

## Aggregate

| Metric | Value |
|---|---:|
| Parsed signals | 8083 |
| Active / superseded facts | 4222 / 1 |
| Users at fact / stream capacity | 0 / 6 |
| Users at entity-index capacity | 3 |
| Preference / entity entries | 372 / 3851 |
| Observable structural recall | 5987/7865 (0.7612) |
| Proposed questions | 37 |
| Known-slot question conflicts | 0 (0.0) |
| Structured scope mismatches | 0/3519 (0.0) |
| Scalar slots with conflicting active values | 31 |
| Near-duplicate active fact pairs | 279 |
| Subtasks with a truncated preference pool | 109/112 |
| Pool-value exposures dropped by bounded card | 24888/32004 |

## Composition

- Active facts by dimension: `{"product": 1888, "brand": 1301, "searches": 497, "explicit": 167, "like": 165, "avoid": 58, "taste": 38, "temperature": 27, "transport": 23, "attribute": 20, "time": 12, "sweetness": 7, "topping": 6, "room_type": 5, "location": 3, "conditional": 3, "caffeine": 1, "safety": 1}`
- Active facts by source: `{"order": 2972, "search": 497, "conversation": 313, "favorite": 129, "opinion": 125, "high_freq_browse": 99, "add_to_cart": 87}`
- Questions by dimension: `{"taste": 15, "time": 10, "unclassified": 4, "party_size": 4, "transport": 3, "room_type": 1}`
- Known-slot conflicts by dimension: `{}`

## By facet

| Facet | Tasks | Recall | Questions | Known-slot conflicts | Pool drops |
|---|---:|---:|---:|---:|---:|
| attraction | 4 | 22/46 (0.4783) | 2 | 0 | 741 |
| beverage | 19 | 1030/1535 (0.671) | 3 | 0 | 4570 |
| flight | 4 | 25/37 (0.6757) | 0 | 0 | 1364 |
| hotel | 5 | 66/76 (0.8684) | 3 | 0 | 878 |
| restaurant | 23 | 1956/2330 (0.8395) | 18 | 0 | 4710 |
| retail | 46 | 2412/3303 (0.7302) | 8 | 0 | 9941 |
| service | 8 | 406/416 (0.976) | 2 | 0 | 2002 |
| wellness | 3 | 70/122 (0.5738) | 1 | 0 | 682 |

## Interpretation limits

- Recall is structural and observable: it asks whether relevant stored facts reach the card/alignment pool, not whether an evaluator target was selected.
- Fact, recall, and pool counts across subtasks are exposure counts; parsed signals are deduplicated per user before counting.
- A conflict means a semantic field already has visible positive evidence when the generic question policy asks it again; confirmation may still be appropriate in some tasks.
- Counts are aggregate only. The audit intentionally emits no user-, task-, product-, or candidate-specific prescription.

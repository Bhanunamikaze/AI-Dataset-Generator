# dataset-auditor

Use this when the user wants to audit the quality, coverage, or integrity of an existing or freshly generated dataset.

This sub-skill orchestrates the existing quality tools and adds three higher-level checks that none of the individual sub-skills cover on their own.

---

## When to invoke

- The user says "audit my dataset", "check my dataset", "evaluate this JSONL", "how good is this dataset" or similar.
- After a full generation run, as a final quality gate before deployment.
- When re-evaluating a dataset weeks or months after it was generated.

---

## Phase 1 — Record-level audit (delegate to existing sub-skills)

Run these in order and collect results before drawing any conclusions.

### 1A. Structural validation
Read `sub-skills/data-verifier.md`. Run:

```bash
python3 scripts/generate.py --input <dataset.jsonl> --source-type raw_dataset --tool-context <context>
python3 scripts/verify.py --from-status raw_generated --source-run-id <run_id>
```

Capture the `verified_fail` count and all `heuristic_errors` strings from the report.

### 1B. Deduplication check
Read `sub-skills/deduplicator.md`. Run:

```bash
python3 scripts/dedup.py --from-status verified_pass --source-run-id <run_id>
```

Report the percentage of records removed. Flag if > 10% of records were duplicates.

### 1C. Semantic quality scoring
If the dataset is small enough (< 1000 records), read `sub-skills/llm-judge.md` and sample-score 10–15% of records. Report average judge score and the ratio of fail/pass at various score thresholds.

### 1D. Distribution summary
Run:

```bash
python3 scripts/export.py --format openai --split 0.1
```

Read the `workspace/DATA_CARD.md` that is produced. Extract:
- `difficulty_distribution` — flag if any single difficulty bucket > 60% of records
- `persona_distribution` — flag if any single persona > 50% of records
- `task_type_distribution` — flag if only one task type is present

---

## Phase 2 — Corpus-level audit (new checks beyond individual sub-skills)

These checks require reasoning across the full corpus, not individual records.

### 2A. Split disjointness audit

Objective: Verify the train split and test split have zero overlapping scenario fingerprints.

Steps:
1. Load both `workspace/canonical_train.jsonl` and `workspace/canonical_test.jsonl`.
2. For each record, compute the cluster key using the same logic as `scripts/export.py`: check `metadata.scenario`, `metadata.topic`, `metadata.intent`, `metadata.subtopic`, `metadata.fingerprint` in order; fall back to first 6 stemmed words of the instruction.
3. Compute `train_keys ∩ test_keys`.
4. **Pass**: intersection is empty. **Fail**: flag every overlapping key and the count of affected records.

### 2B. Taxonomy coverage audit

Objective: Verify that the dataset covers all planned taxonomy buckets, not just the most common ones.

Steps:
1. Look for any planning document or taxonomy definition (e.g., generated during `dataset-strategy`). If none exists, infer the intended taxonomy by clustering records by their metadata topic/intent keys.
2. Identify any taxonomy bucket that has **zero records** in the final verified corpus.
3. Identify any cluster with **fewer than 3 records** — these are thin-coverage buckets that will not provide meaningful gradient signal.
4. Report: zero-coverage buckets, under-covered buckets, and the top-3 most over-represented clusters.

### 2C. Context leakage detection

Objective: Verify that the `context` field does not reveal the answer, mechanism, or root cause that the `response` is supposed to deduce.

Steps:
1. Sample 20% of records (or all records if < 200 total).
2. For each sampled record, check whether any key tokens from the final verdict or root cause in `response.text` appear verbatim in `context`. Use a simple substring match on the most distinctive tokens (e.g., "VULNERABLE", "XSS", "SQL injection", the specific sink name).
3. Flag records where > 2 decisive tokens from the response appear literally in the context.
4. Report the leaked-context rate. If > 15% of sampled records show leakage, this is a **High severity** finding.

---

## Phase 3 — Structured audit report

After all phases, produce a structured summary. Do **not** just emit raw numbers; classify each finding by severity.

```
## Dataset Audit Report

**Total records reviewed**: N
**Records passing structural checks**: N (X%)
**Duplicate rate**: X%
**Average judge score**: X / 10 (sampled)

### Findings

| # | Severity | Check | Detail |
|---|----------|-------|--------|
| 1 | High     | Split disjointness | 7 scenario fingerprints appear in both train and test |
| 2 | High     | Context leakage | 22% of sampled records expose the answer in context |
| 3 | Medium   | Taxonomy coverage | 4 planned buckets have zero records |
| 4 | Low      | Diversity | persona distribution: "red_team_analyst" = 68% of corpus |

### Recommendations

For each High or Medium finding, emit a concrete, actionable fix:
- "Re-run export with `--split 0.2` and verify cluster disjointness."
- "Re-generate the 4 missing taxonomy buckets using `diversity-engine`."
- "Strip explicit sink names from context fields in records flagged for leakage."
```

---

## Severity definitions

| Severity | Meaning |
|----------|---------|
| **High** | The dataset will likely produce a misleadingly optimistic eval score or a model that fails on real-world inputs |
| **Medium** | Reduces dataset utility; acceptable for a prototype but not for a training run |
| **Low** | Cosmetic or minor distribution skew; worth noting but not blocking |

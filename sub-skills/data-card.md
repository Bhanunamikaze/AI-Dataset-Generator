# data-card

Use this during export finalization.

## Required coverage

A robust data card goes beyond simple row counts. Ensure the following sections are populated:

### 1. Provenance & statistics
- dataset purpose
- task types (`sft` vs `dpo`)
- example counts & split sizes
- source types (synthetic vs human vs web)
- collection method

### 2. Pipeline coverage metrics
- **Taxonomy coverage:** planned semantic buckets vs. actual distribution
- **Distribution skew warnings:** flag any subtopic, persona, or difficulty bucket that makes up >40% or <5% of the total dataset. Explain the skew.
- Deduplication strategy and duplicate drop rate
- Quality checks applied (reasons records were failed during verification)

### 3. Model behaviour profiling
- **Intended model behaviour:** what a model fine-tuned on this dataset *should* do differently than a base model (e.g. "it should stop apologising, use direct language, and output exactly 3 JSON keys").
- **Known failure modes & risks:** what the dataset might train the model to do poorly (e.g. "Dataset contains primarily Python — model may over-apply Python idioms to C++ requests").
- **Tonal/Style bias:** known stylistic leanings in the synthetic records.

### 4. Training configuration hints
- Suggested learning rate ranges or epoch counts if known.
- Recommended LoRA rank/alpha vs full fine-tuning guidance for the dataset volume.

## Generation
`scripts/export.py` writes a baseline `workspace/DATA_CARD.md`. Expand or rewrite it to cover the sections above if the user wants a richer narrative card.


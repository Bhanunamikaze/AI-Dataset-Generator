# Contributing

## Development Setup

```bash
git clone https://github.com/Bhanunamikaze/ai-dataset-generator.git
cd ai-dataset-generator
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -p 'test*.py'
```

## Contribution Rules

- keep the canonical internal schema stable unless the change is justified across the whole pipeline
- add or update tests for any behavior change
- prefer deterministic logic in `scripts/` and reasoning guidance in `sub-skills/`
- do not add external LLM-provider API calls to this skill
- keep user-facing export schemas flexible and internal pipeline schema fixed

## Common Change Types

### Add a new sub-skill

1. add a markdown file under `sub-skills/`
2. update `SKILL.md` routing if the new skill changes orchestration
3. update `README.md` if the new skill is user-facing

### Add a new export preset

1. add the preset schema under `resources/target-schemas/`
2. update `scripts/export.py` only if logic changes are required
3. add or extend tests that exercise the preset

### Change canonical normalization

1. update `scripts/utils/canonical.py`
2. keep `resources/internal-schema/canonical_schema.json` aligned
3. add regression tests in `tests/test_pipeline.py`

### Change installation behavior

1. update `install.sh`
2. update the installation section in `README.md`

## Validation

Run before opening a PR:

```bash
python3 -m py_compile scripts/generate.py scripts/augment.py scripts/verify.py scripts/dedup.py scripts/export.py scripts/utils/db.py scripts/utils/files.py scripts/utils/schema.py scripts/utils/canonical.py scripts/utils/security.py tests/test_pipeline.py
python3 -m unittest discover -s tests -p 'test*.py'
```

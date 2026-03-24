# seed-generator

Use this after `dataset-strategy`.

## Goal

Create or normalize draft records into the fixed canonical schema.

## Operating modes

### Topic-driven generation

- Draft canonical records directly in JSONL.
- Spread examples across taxonomy, persona, and difficulty.
- Keep records concrete and non-redundant.
- Unless the user specifies otherwise, target `500` total records.
- For large targets, generate in batches and keep going until the planned count is reached.
- Do not stop after a small starter set unless the user explicitly asked for a prototype or sample.

### URL or reference-material structuring

- Use available browsing, file-reading, and search tools in the IDE.
- Extract facts, examples, or source passages.
- Convert them into canonical records instead of copying raw source dumps.
- If the user does not specify size, aim for `500` structured records by default.

### Existing dataset normalization

- Map source columns into canonical fields.
- Preserve provenance in metadata.
- Keep the source URI/path when available.
- Preserve as much of the usable dataset size as possible unless the user asks for sampling.

## Output path

Write draft records to a JSONL file, then load them with:

```bash
python3 scripts/generate.py --input <drafts.jsonl> --source-type <generated|url_reference|raw_dataset|internet_research> --tool-context <codex|claude|antigravity>
```

The imported drafts will enter the pipeline as `raw_generated` records unless they still contain explicit placeholder responses, in which case they remain `seeded`.

For red-team, security, pentest, jailbreak, or prompt-injection datasets, treat injection-tolerant import as the default. Add `--enforce-security-flags` only when you want those payloads flagged instead of preserved.

## Required metadata

Each canonical record should carry enough metadata for later export and audit:

- `difficulty`
- `persona`
- `source_type`
- optional provenance such as `reference_urls`, tags, source path, or notes

For untrusted imports and web-derived material, also inspect:

- `metadata.security_flags`
- `metadata.requires_manual_review`

## Seed-only fallback

If you only need placeholder slots before writing full examples:

```bash
python3 scripts/generate.py --topic "<topic>" [--count <n>] --task-type <sft|dpo>
```

If `--count` is omitted, the placeholder target defaults to `500`.

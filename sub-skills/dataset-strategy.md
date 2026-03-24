# dataset-strategy

Use this when the user asks to generate, normalize, or restructure a dataset.

## Goal

Turn the user request into a concrete dataset plan before any records are written.

## Required decisions

1. Classify the request:
   - Topic-driven synthetic dataset generation
   - URL or reference-material structuring
   - Existing dataset normalization
   - Verify-only audit
   - Export-only request
2. Choose `task_type`:
   - `sft` for single best answers
   - `dpo` for chosen/rejected preference pairs
3. Define the taxonomy:
   - domains, subtopics, personas, difficulty spread, edge cases
4. Define the target output:
   - OpenAI preset
   - HuggingFace preset
   - Flat CSV/JSONL
   - Custom schema file
5. Define the target example count:
   - use the user-provided size when present
   - default to `500` examples when the user does not specify a size
6. Decide ingestion safety mode:
   - red-team, security, pentest, jailbreak, and prompt-injection corpora should default to injection-tolerant import behavior
   - use strict flagging only when the user clearly wants defensive filtering instead

## Important rule

Do not hardcode one universal user-facing header layout.

- The canonical internal schema stays fixed.
- The final export schema is chosen per user request.
- If the user needs custom columns, create a schema file from `resources/templates/custom_flat_schema.json`.

## Output contract

Produce a concise plan with:

- request type
- task type
- source mode
- source type
- target format
- target schema or custom column list
- intended example count
- taxonomy buckets
- quality requirements
- ingestion safety mode
- resume or fresh-run decision

Always state the intended example count explicitly. Do not leave it implicit.

## Source-type mapping

- Topic-driven generation -> `generated`
- URL/reference-material structuring -> `url_reference`
- Existing dataset normalization -> `raw_dataset`
- Internet-research collection -> `internet_research`

Always state the chosen `source_type` explicitly before moving into the script layer.

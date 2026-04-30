# Screenshot Stock Import Vision Design

Date: 2026-04-30

## Context

The current screenshot import workflow is not stable enough for real portfolio updates.
The skill relies on multiple agents reading the same image, while
`src/tools/screenshot_stock_import.py` relies on local OCR and regex parsing. This can
misread stock rows, confuse holding columns, and fail silently on different broker
interfaces.

The new design makes the Python script the reliable import engine and keeps
`.claude/skills/screenshot-stock-import/SKILL.md` as the human-facing orchestration
entry point.

## Goals

- Recognize stock information from multiple broker screenshots with higher reliability.
- Support configurable vision providers: `auto`, `kimi`, `glm`, and `minimax`.
- Use screenshot color and layout as hints, not as hard truth.
- Ask the user for the broker when screenshot classification is uncertain.
- Allow applying only high-confidence, validated results.
- Keep low-confidence, conflicting, or incomplete results out of the database until
  the user confirms them.
- Preserve the project's config update and audit requirements.
- Keep preview and apply deterministic by applying only a frozen import plan.
- Treat screenshots, model responses, and debug artifacts as sensitive financial data.

## Non-Goals

- Do not build a full generic OCR platform.
- Do not directly edit JSON files or SQLite databases outside the existing config
  update path.
- Do not attempt to perfectly classify every broker theme in the first release.
- Do not auto-delete holdings that are missing from a screenshot.
- Do not support shared or cloud execution environments in v1. This design assumes a
  single-user local machine unless a later enterprise/privacy mode is designed.

## Data Classification

Screenshots, model requests, model responses, debug bundles, and import plans are
sensitive financial data. They may contain real holdings, costs, share counts, account
nicknames, and broker-specific UI details.

Third-party vision recognition is opt-in per run. Before uploading an image to Kimi,
GLM, MiniMax, or another provider, the CLI and skill must disclose that the image and
derived text may be processed on vendor infrastructure. The tool should minimize what
it sends where practical, but accuracy remains the priority for v1.

## Supported First-Release Scope

The architecture supports six target screenshot families:

- Tonghuashun watchlist
- Tonghuashun holdings
- Eastmoney watchlist
- Eastmoney holdings
- HK Panda watchlist
- HK Panda holdings

The first release focuses on a stable pipeline rather than exhaustive broker-specific
rules. It should classify obvious screenshots, ask the user when classification is
uncertain, and improve broker prompts over time using real samples.

## Architecture

The import flow is:

1. Read screenshot image.
2. Extract local visual fingerprint.
3. Classify likely broker and screenshot type.
4. Ask the user for broker/type if confidence is too low.
5. Build a broker-aware prompt.
6. Call the configured vision provider.
7. Parse strict JSON output.
8. Validate and normalize each stock row.
9. Build an import plan with auto-apply, needs-confirmation, and rejected groups.
10. Apply only approved changes through the config update path with audit.

Suggested modules inside `src/tools/screenshot_stock_import.py` or adjacent helpers:

- `screenshot_classifier`: Computes color, layout, and keyword signals and returns
  candidate broker/type classifications.
- `vision_provider`: Wraps Kimi, GLM, and MiniMax vision calls behind one interface.
- `prompt_builder`: Builds broker/type-aware prompts from the classifier result.
- `result_validator`: Normalizes and validates model output.
- `import_planner`: Converts validated rows into safe import actions.
- `config_importer`: Applies approved actions through `POST /api/config`.

## CLI

Keep the existing CLI entry point and add these options:

- `--provider auto|kimi|glm|minimax`
- `--dry-run`
- `--apply`
- `--yes`
- `--plan PATH`
- `--confirm-threshold FLOAT`
- `--type auto|holding|watchlist`
- `--platform auto|ths|eastmoney|hk_panda|other`
- `--output-json PATH`
- `--debug-dir PATH`
- `--debug-sensitive`
- `--allow-path PATH`

Default behavior should be conservative:

- Run the full recognition and validation pipeline.
- Print the import plan and write it to `--output-json` or the default plan location.
- Do not mutate config unless `--apply` is present.
- Never auto-apply rejected, conflicting, or low-confidence rows.

`--dry-run` and `--apply` are mutually exclusive. `--dry-run` must never call
`POST /api/config` and must never write SQLite. `--apply` must apply a frozen plan from
`--plan PATH`; it must not call a vision provider again. `--yes` means the command is
non-interactive: it may apply only the `auto_apply` group from the frozen plan and must
not prompt for broker/type or row confirmation.

Exit codes:

- `0`: plan generated, or apply completed without failed rows.
- `1`: usage error.
- `2`: input error such as missing image, unreadable file, unsupported file type, or
  image over the configured size limit.
- `3`: needs user input, such as low-confidence broker/type in non-interactive mode.
- `4`: provider failure.
- `5`: schema or validation failure.
- `6`: apply failure, including HTTP errors or partial row failures.

Interactive behavior:

- In a TTY run without `--yes`, low-confidence classification may prompt for broker
  or screenshot type, then rerun recognition with the user-supplied context.
- In non-interactive mode, low-confidence classification exits `3` with a JSON hint
  listing candidate platforms and the required `--platform` / `--type` flags.
- `--platform` and `--type` suppress broker/type prompts and become explicit user
  context for prompt building and validation.

Path behavior:

- The input image must resolve to an existing regular file.
- The implementation should reject symlink escapes and non-regular files.
- `--output-json` and `--debug-dir` must be written atomically.
- Non-interactive runs should allow only paths under the current workspace, the user's
  Downloads directory, or roots explicitly provided through `--allow-path`.

## Two-Stage Classification

Classification is split into two stages.

Stage 1 extracts visual fingerprints without committing to a broker:

```json
{
  "dominant_colors": ["dark_blue", "black", "white"],
  "top_bar_color": "dark_blue",
  "background_color": "white",
  "layout": "dense_table",
  "visible_keywords": ["持仓", "可用", "盈亏"],
  "candidate_type": "holding"
}
```

Stage 2 turns those fingerprints into ranked candidates:

```json
{
  "best_guess": "eastmoney_holding",
  "platform": "eastmoney",
  "screenshot_type": "holding",
  "confidence": 0.58,
  "signals": [
    "top_dark_bar",
    "holding_keywords_detected",
    "dense_numeric_table"
  ],
  "candidate_platforms": [
    {"platform": "eastmoney", "confidence": 0.55},
    {"platform": "hk_panda", "confidence": 0.35}
  ]
}
```

Color is a hint:

- Tonghuashun often has a red top area.
- Eastmoney may be orange, dark blue, or black depending on theme.
- HK Panda commonly appears white or light colored.

Classification must also consider layout and visible text. Holdings screenshots tend
to have dense numeric columns such as cost, shares, market value, available quantity,
profit/loss, and current price. Watchlist screenshots tend to have stock rows with
name, code, price, and percentage change.

If classifier confidence is below the threshold, the script must pause and ask:

> I cannot confidently identify this screenshot. Candidate brokers are Eastmoney and
> HK Panda. Which broker is it: Tonghuashun, Eastmoney, HK Panda, or other?

After the user answers, the script reruns recognition with the selected broker/type
as prompt context. In non-interactive mode, the same condition exits `3` instead of
waiting for input.

## Structured Output Contract

The provider response and import plan must have versioned contracts. The implementation
should define Pydantic models and generate JSON Schema from them.

`VisionStockImportResponseV1`:

- `schema_version`: literal `1`.
- `platform`: enum `ths`, `eastmoney`, `hk_panda`, `other`, or `unknown`.
- `screenshot_type`: enum `holding`, `watchlist`, or `unknown`.
- `confidence`: number in `[0, 1]`.
- `stocks`: list of typed rows.
- `warnings`: list of bounded warning codes or sanitized short messages.

Holding rows require `code`, `name`, `cost`, `shares`, `is_holding=true`, and
`field_confidence` for `code`, `name`, `cost`, and `shares`. Watchlist rows require
`code`, `name`, `is_holding=false`, and must not contain `cost` or `shares`.

`ImportPlanV1`:

- `schema_version`: literal `1`.
- `import_run_id`: UUID generated during recognition and reused during apply.
- `created_at`: ISO timestamp.
- `provider` and model identifier.
- `platform`, `screenshot_type`, classifier summary, thresholds, and content
  fingerprint.
- `actionable_rows_hash`: hash of normalized rows that can be applied.
- `auto_apply`, `needs_confirmation`, and `rejected` groups.
- `apply_log`: optional per-row apply status, updated during `--apply`.

`PlannedAction` rows should use stable `reason_codes` rather than only free-form text,
so tests and skill logic can branch deterministically.

The provider request object should pass the actual JSON Schema object, not a prose
placeholder:

```json
{
  "image_path": "/path/to/screenshot.png",
  "mime_type": "image/png",
  "prompt": "broker-aware prompt",
  "json_schema": {"$id": "vision_stock_import_response_v1"}
}
```

## Prompt Strategy

Prompts must be narrow and broker-aware. The prompt should include:

- The classifier's candidate broker/type.
- Key visual signals detected locally.
- The expected row structure for that broker/type.
- Explicit warnings about common column confusion.
- A strict JSON schema.

Examples of broker-specific constraints:

- Tonghuashun holdings: red top area is only a visual hint. Extract stock code, name,
  cost, and shares. Do not confuse current price, market value, profit/loss, or
  available quantity with cost.
- Eastmoney holdings/watchlist: theme color can be orange, dark blue, or black. Use
  text labels and row layout instead of color alone.
- HK Panda holdings/watchlist: white/light interface is a hint. HK stocks must be
  normalized to `HKxxxxx`.

The model must return strict JSON:

```json
{
  "platform": "eastmoney",
  "screenshot_type": "holding",
  "confidence": 0.82,
  "stocks": [
    {
      "code": "HK00700",
      "name": "腾讯控股",
      "is_holding": true,
      "cost": 320.5,
      "shares": 100,
      "field_confidence": {
        "code": 0.95,
        "name": 0.9,
        "cost": 0.88,
        "shares": 0.92
      },
      "evidence": {
        "kind": "row_index",
        "value": "3"
      }
    }
  ],
  "warnings": []
}
```

If a provider supports JSON schema response formats, use them. Otherwise, validate the
returned JSON locally and reject malformed responses.

`evidence` and `warnings` are untrusted model output. Persisted evidence must be
structured, bounded, and sanitized. Raw free-text evidence must not be sent to
`POST /api/config`, written into audit payloads, or forwarded to another model prompt.

## Provider Selection

Provider selection is configurable:

- `--provider kimi`
- `--provider glm`
- `--provider minimax`
- `--provider auto`

`auto` should select the first available configured provider based on environment
variables. Provider credentials must come from environment variables only. No keys may
be hardcoded or logged.

Each provider wrapper should accept the same request object:

```json
{
  "image_path": "/path/to/screenshot.png",
  "prompt": "broker-aware prompt",
  "json_schema": {"$id": "vision_stock_import_response_v1"}
}
```

Each wrapper returns either a parsed provider response or a clear error. Automatic
fallback is allowed only before an import plan is created and only on transport,
timeout, or schema-parse failure. A single import plan must not mix rows from multiple
providers. After any successful `POST /api/config`, fallback is forbidden for that run.

Before the first third-party vision call in a session, the CLI and skill must disclose
that the screenshot may be uploaded to the selected provider and processed on vendor
infrastructure. The disclosure should happen before upload, not after plan generation.
This is required because screenshots and extracted portfolio data are sensitive
financial data.

## Validation And Import Planning

Validation decides whether recognition results are safe to apply.

Normalize codes:

- A-share codes remain six digits such as `000001` and `600519`.
- HK codes become `HKxxxxx`.
- Unsupported or ambiguous codes are rejected for auto-apply. Name-only matches always
  go to `needs_confirmation`, never `auto_apply`.

For holdings:

- `cost` is required.
- `shares` is required.
- `shares` must be a positive integer.
- `cost` must be a plausible stock price.
- The row must not use market value, profit/loss, percentage change, or current price
  as cost.

For watchlists:

- `code` and `name` are enough.
- Do not import `cost` or `shares` from a watchlist screenshot.
- Star/focus information may be imported only when visually clear.

Build an import plan with three groups:

- `auto_apply`: high-confidence, valid, non-conflicting changes.
- `needs_confirmation`: valid enough to review but not safe to apply automatically.
- `rejected`: malformed, unsupported, or clearly unsafe rows.

`auto_apply` requires all of these conditions:

- Classifier confidence is at or above the configured threshold, unless the user
  explicitly supplied `--platform` and `--type`.
- Model top-level confidence is at or above the configured threshold.
- Required field confidences are at or above the configured threshold.
- The row has no duplicate-code conflict in the screenshot.
- The row is not a name-only match.
- Existing system data does not show an abnormal cost or shares delta.

Reasons for `needs_confirmation` include:

- Broker/type was manually supplied after low-confidence classification.
- Model field confidence is below threshold.
- `cost` or `shares` differ sharply from existing holdings.
- The same code appears with conflicting values.
- Only a name was detected and system matching is uncertain.

Missing holdings are never deleted automatically. If an existing holding does not
appear in a screenshot, the script should report it as a possible sale and ask the
user before any removal or conversion to watchlist.

## Data Update Path

Config updates must follow project rules.

Runtime write path:

1. Use `POST /api/config`.
2. Keep audit behavior intact through the existing config API flow.
3. If the web service is unavailable, fail clearly and leave the generated import plan
   on disk so the user can rerun after starting the service.

The existing local DB import helper can remain as a temporary implementation detail
for tests or migration cleanup, but the new screenshot import runtime path should not
write directly to SQLite.

No runtime JSON files should be edited as a configuration source.

Default API target is `http://127.0.0.1:3120/api/config`. Remote API URLs are
disallowed unless the user explicitly opts in, for example through
`SCREENSHOT_IMPORT_ALLOW_REMOTE_API=true`. If a non-loopback API URL is allowed, it
must use HTTPS. Authentication tokens, if configured, must come from environment
variables and must not be logged.

### Frozen Plans

Recognition and apply are separate phases:

1. Recognition generates `ImportPlanV1`.
2. The plan stores `import_run_id`, provider, model id, classifier summary, thresholds,
   content fingerprint, and `actionable_rows_hash`.
3. Apply reads `--plan PATH` and checks the plan hash.
4. Apply must not call a vision provider or re-run prompt generation.
5. If a user edits the plan, the hash mismatch must stop apply unless an explicit
   force mechanism is added in a later design.

This prevents preview/apply drift when a model returns different results across calls.

### API Apply Semantics

Version 1 should not assume a config batch endpoint exists. It should call the current
config API once per symbol:

- Use `add` when the planner intends an upsert-style row from the screenshot.
- Use `update` when the row is known to exist and only selected fields should change.
- Never remove or demote missing holdings in this import path.

The importer should use a stable application order, such as code-sorted rows with
`plan_sequence` numbers. Partial failure is best-effort:

- Continue applying remaining rows unless a fatal authentication or connectivity error
  occurs.
- Record `applied` and `failed` rows in `apply_log`.
- Exit `6` if any row fails.
- Allow rerunning the same frozen plan to skip rows already marked as applied and retry
  failed rows.

Idempotency is importer-side in v1. Each row should have a stable `row_apply_id`
derived from `import_run_id`, code, action, and normalized payload hash. If the config
API later supports server-side idempotency keys or batch transactions, this section can
be replaced by a stronger all-or-nothing apply model.

### Audit Provenance

Each apply run should carry audit context:

- `source`: `screenshot_stock_import`
- `import_run_id`
- provider and model id
- final platform and screenshot type
- classifier confidence summary
- thresholds used
- content fingerprint

If the current config API cannot yet persist all of this metadata into the audit
outbox, the importer must still write it to `apply_log` next to the frozen plan. A later
API extension should allow all mutations in one import run to share a correlation id.
Raw screenshots, raw model responses, and free-text evidence must not be written to
audit payloads.

## Debug Artifacts

Debug artifacts are off by default. Each run may write a debug package only when
`--debug-dir` is set:

```text
.screenshot_import_runs/<timestamp>/
  image_fingerprint.json
  provider_request_redacted.json
  provider_response.json
  validated_result.json
  import_plan.json
```

The debug directory must be created with private permissions where the OS supports
them. The redacted request must not include API keys or secrets.

These artifacts make failures diagnosable: classifier error, prompt error, provider
error, validation too strict, or validation too loose.

Because debug artifacts can contain real portfolio data, use privacy tiers:

- Tier A default: fingerprints, counts, confidence stats, provider name, hashes of
  codes, and no names, cost, or shares.
- Tier B support mode: full codes but masked names and no cost or shares.
- Tier C sensitive mode: full provider response, validated result, and import plan.
  This requires `--debug-sensitive` and a clear CLI warning.

Debug directories must be ignored by git and should not be used as committed test
fixtures. Sanitized or synthetic fixtures belong under the test fixture directory.

## Skill Update

Update `.claude/skills/screenshot-stock-import/SKILL.md` so the skill no longer
starts three agents to independently read images.

The skill should instruct the agent to:

1. Run `uv run python -m src.tools.screenshot_stock_import <image> --dry-run`.
2. Review the generated import plan.
3. If the script asks for broker/type, ask the user and rerun with `--platform` or
   `--type`.
4. Apply high-confidence results only by running `--apply --plan <import_plan.json>`.
5. Never directly edit JSON or SQLite.

## Testing

Unit tests:

- Color and layout fingerprint extraction.
- Broker/type classification from fingerprints.
- Code normalization.
- Holding and watchlist validation.
- Import plan grouping.
- Missing-holding detection.
- Confidence threshold composition.
- Name-only matches entering `needs_confirmation`.
- Path validation and atomic output writes.

Provider tests:

- Mock successful strict JSON responses.
- Mock malformed JSON.
- Mock missing fields.
- Mock low-confidence fields.
- Mock provider errors and timeouts.
- Verify fallback happens only before plan creation and never mixes provider rows.

Integration tests:

- Verify dry-run produces no writes.
- Verify `--apply --plan` calls the config API with expected payloads.
- Verify low-confidence rows are not applied.
- Verify missing holdings are reported but not deleted.
- Verify partial API failure records `applied` and `failed` rows and exits `6`.
- Verify non-interactive low-confidence classification exits `3`.
- Verify debug artifacts default to Tier A and do not contain full cost/shares.

Sample regression tests:

- Add sanitized or synthetic screenshot fixtures over time.
- Store expected `image_fingerprint.json` and `import_plan.json` snapshots.
- Re-run regressions whenever prompts or classifier rules change.

## Success Criteria

- Obvious screenshots are classified without user intervention.
- Ambiguous screenshots ask the user for broker/type instead of guessing.
- Vision providers return structured data through one interface.
- High-confidence rows can be imported through `--apply --plan`.
- Low-confidence or conflicting rows are withheld for confirmation.
- Preview/apply behavior is deterministic because apply uses a frozen plan.
- Debug output is private by default and does not leak full portfolio details unless
  explicitly requested.
- The skill delegates recognition to the script and uses the script's structured plan.
- Config authority and audit requirements remain intact.

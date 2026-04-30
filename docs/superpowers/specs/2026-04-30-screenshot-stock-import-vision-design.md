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
- Auto-apply only high-confidence, validated results.
- Keep low-confidence, conflicting, or incomplete results out of the database until
  the user confirms them.
- Preserve the project's config update and audit requirements.

## Non-Goals

- Do not build a full generic OCR platform.
- Do not directly edit JSON files or SQLite databases outside the existing config
  update path.
- Do not attempt to perfectly classify every broker theme in the first release.
- Do not auto-delete holdings that are missing from a screenshot.

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
- `--yes`
- `--confirm-threshold FLOAT`
- `--type auto|holding|watchlist`
- `--platform auto|ths|eastmoney|hk_panda|other`
- `--output-json PATH`
- `--debug-dir PATH`

Default behavior should be conservative:

- Run the full recognition and validation pipeline.
- Auto-apply only high-confidence items when allowed.
- Print items needing confirmation.
- Never auto-apply rejected or conflicting rows.

`--dry-run` must never write data.
`--yes` means the script can apply high-confidence items without additional prompting,
but it still must not apply low-confidence or rejected items.

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
as prompt context.

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
      "evidence": "row text or visual cue used for this extraction"
    }
  ],
  "warnings": []
}
```

If a provider supports JSON schema response formats, use them. Otherwise, validate the
returned JSON locally and reject malformed responses.

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
  "response_schema": "stock import schema"
}
```

Each wrapper returns either a parsed provider response or a clear error. If one
provider fails, the user can rerun with another provider. Automatic fallback is
allowed only when it does not duplicate writes and preserves the same dry-run/import
plan behavior.

## Validation And Import Planning

Validation decides whether recognition results are safe to apply.

Normalize codes:

- A-share codes remain six digits such as `000001` and `600519`.
- HK codes become `HKxxxxx`.
- Unsupported or ambiguous codes are rejected unless matched to an existing system
  stock by name with high confidence.

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

## Debug Artifacts

Each run may write a debug package when `--debug-dir` is set:

```text
.screenshot_import_runs/<timestamp>/
  image_fingerprint.json
  provider_request_redacted.json
  provider_response.json
  validated_result.json
  import_plan.json
```

The redacted request must not include API keys or secrets.

These artifacts make failures diagnosable: classifier error, prompt error, provider
error, validation too strict, or validation too loose.

## Skill Update

Update `.claude/skills/screenshot-stock-import/SKILL.md` so the skill no longer
starts three agents to independently read images.

The skill should instruct the agent to:

1. Run `uv run python -m src.tools.screenshot_stock_import <image> --dry-run`.
2. Review the generated import plan.
3. If the script asks for broker/type, ask the user and rerun with `--platform` or
   `--type`.
4. Apply high-confidence results only after the script plan is acceptable.
5. Never directly edit JSON or SQLite.

## Testing

Unit tests:

- Color and layout fingerprint extraction.
- Broker/type classification from fingerprints.
- Code normalization.
- Holding and watchlist validation.
- Import plan grouping.
- Missing-holding detection.

Provider tests:

- Mock successful strict JSON responses.
- Mock malformed JSON.
- Mock missing fields.
- Mock low-confidence fields.
- Mock provider errors and timeouts.

Integration tests:

- Verify dry-run produces no writes.
- Verify high-confidence imports write expected config rows and audit outbox entries.
- Verify low-confidence rows are not applied.
- Verify missing holdings are reported but not deleted.

Sample regression tests:

- Add sanitized or synthetic screenshot fixtures over time.
- Store expected `image_fingerprint.json` and `import_plan.json` snapshots.
- Re-run regressions whenever prompts or classifier rules change.

## Success Criteria

- Obvious screenshots are classified without user intervention.
- Ambiguous screenshots ask the user for broker/type instead of guessing.
- Vision providers return structured data through one interface.
- High-confidence rows can be imported automatically.
- Low-confidence or conflicting rows are withheld for confirmation.
- The skill delegates recognition to the script and uses the script's structured plan.
- Config authority and audit requirements remain intact.

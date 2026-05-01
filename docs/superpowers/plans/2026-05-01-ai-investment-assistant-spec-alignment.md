# AI Investment Assistant — SPEC Alignment & Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement remaining tasks below. Steps use checkbox (`- [ ]` / `- [x]`) syntax for tracking.

**Goal:** Map `docs/superpowers/specs/2026-05-01-ai-investment-assistant-design.md` to the repository, record what is already shipped, and list the next concrete work without duplicating the full product vision.

**Architecture:** Keep `config.db` for user truth, `trading.db` for jobs, AI events, summaries, and jobs; poller/notifier authority rules unchanged. New features hang off `job_requests` / `job_runs`, `ai_investment_events`, and existing Web/API + daemon boundaries.

**Tech Stack:** Python 3.13 + uv, pytest; Next.js 15 + Vitest; SQLite WAL (`src/data/trading.db`, `src/data/config.db`).

**Source SPEC:** `docs/superpowers/specs/2026-05-01-ai-investment-assistant-design.md`

---

## A. SPEC “Current Gaps” — repository status


| SPEC gap (verbatim theme)                                      | Status             | Evidence / notes                                                                                                       |
| -------------------------------------------------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `earnings_calendar_daemon.py` not in main startup              | **Resolved**       | `start_ai_investor_full.sh` starts/stops `src/tools/earnings_calendar_daemon.py` (PID `.earnings_calendar_daemon.pid`) |
| Earnings trigger split (API flags vs trigger files)            | **Resolved**       | `POST /api/earnings` → `trading.db:job_requests` + audit; daemon claims `earnings_check` and writes `job_runs`         |
| `daily_summaries` / `morning_briefings` contracts inconsistent | **Addressed**      | `src/sim_trading/db.py` reconciliation + `src/sim_trading/test_db_schema.py`                                           |
| Daily summary Markdown not persisted                           | **Addressed**      | `src/tools/daily_summary_generator.py` writes `report_md`; `web/app/api/summary/route.ts` exposes `report`             |
| Common event stream, jobs, health before more signals          | **Partially done** | `ai_investment_events`, jobs, `/api/health` done; recommendation state machine / Today Action Center not done          |


---

## B. Recommended First PR Sequence (SPEC §268–289)

### B.1 Schema foundation

- **Versioned migration bookkeeping** — `schema_migrations` in `src/sim_trading/db.py`; rows recorded on reconcile steps.
- `**job_requests` / `job_runs`** — DDL + indexes in `src/sim_trading/db.py`; audit tables listed in `src/utils/audit_writer.py`.
- `**ai_investment_events`** — DDL + indexes; tests in `src/sim_trading/test_db_schema.py`.
- `**daily_summaries.report_md` + legacy migration** — `_reconcile_daily_summaries` / init path in `src/sim_trading/db.py`; generator insert in `src/tools/daily_summary_generator.py`.
- `**morning_briefings` contract** — `_reconcile_morning_briefings` in `src/sim_trading/db.py`.
- **Migration / init tests** — `src/sim_trading/test_db_schema.py` (e.g. legacy summary/briefing migration).

### B.2 Earnings trigger contract

- `**POST /api/earnings` inserts `job_requests`** — `web/app/api/earnings/route.ts`; tests `web/app/api/earnings/route.test.ts`.
- **Daemon claims jobs and writes `job_runs`** — `src/tools/earnings_calendar_daemon.py` (`claim_pending_job_request`, `finish_job_run`, stale requeue).

### B.3 Health and startup observability

- `**GET /api/health**` — `web/app/api/health/route.ts` (+ `route.test.ts`); includes job failure / pending signal as designed.
- **Earnings daemon in start / status / stop** — `start_ai_investor_full.sh` (start block ~159+, stop ~264, status ~296).

### B.4 Daily summary contract

- **Persist and expose `report_md` as `report`** — `web/app/api/summary/route.ts` maps `report_md` → `data.report`; tests assert shape.
- **Fallback when today’s row missing** — same route: latest prior row with non-empty `report_md` (`summaryFallback`).

### B.5 Notifier AI-event skeleton

- **Consume pending AI events** — `consume_pending_ai_investment_events()` in `src/tools/stock_notifier.py` (called from notifier loop).
- **Eligible events → `alert_events`** — same module (convert path alongside consume; covered by existing notifier + `src/tools/test_earnings_ai_events.py` for event shape).

---

## C. Phase 1: Automation Reliability (SPEC §130–136)

- Earnings daemon management in `start_ai_investor_full.sh`.
- Manual earnings trigger uses `job_requests` / `job_runs` only (no trigger-file path in API).
- `/api/health` for status and recent job failures.
- Daily summary fallback for missed window (`/api/summary`).
- `/api/summary` returns persisted Markdown as `report`.

---

## D. Phase 2: Earnings Defense (SPEC §138–148)

- Scan holdings + star for upcoming reports — `src/tools/earnings_ai_events.py` (`emit_holdings_star_earnings_ai_events` and related).
- T-5 / T-3 / T-1 / T-day style countdown events — module implements countdown bands and `earnings_countdown` / session variants (see module header and `COUNTDOWN_BANDS`).
- Emit `ai_investment_events` for notifier — inserts with `notify_status`, dedupe keys; tests in `src/tools/test_earnings_ai_events.py`.
- **Post-earnings compare vs prior periods and forecasts** — partially covered by post-window / LLM paths in `earnings_calendar.py` / generators; **no single audited “compare” contract** matching SPEC bullets end-to-end. Track as follow-up if product requires explicit checks.
- **Daily action recommendations for high-risk holdings** — structured `recommendation_json` exists in places; **no dedicated “action center” or approval workflow** (see Phase 5 / Web UX).

---

## E. Phases 3–5 and cross-cutting (SPEC §150+)

### Phase 3: Main-Wave Detection

- Not implemented as specified (scores, regime gates, escalation rules).

### Phase 4: 15% Trend Reversal

- Not implemented as specified (classification taxonomy + suppression rules).

### Phase 5: Daily Investment Review orchestrator

- Not implemented as specified (unified orchestrator output).

### Recommendation workflow (state machine)

- Not implemented (`draft` → `applied` etc.).

### Decision ledger & portfolio risk

- Not implemented.

### Web UX — Today Action Center

- Not implemented.

### Feishu contract (typed AI payloads)

- Partially satisfied via existing notifier templates; **full typed contract** from SPEC not implemented.

### Testing and validation (replay, Web-only gating)

- Not done per SPEC §259–266.

---

## F. Documentation hygiene (SPEC vs repo)

### Task F.1: Refresh SPEC “Current Gaps” in the design doc

**Files:**

- Modify: `docs/superpowers/specs/2026-05-01-ai-investment-assistant-design.md` (§Current Gaps — replace stale bullets with “Resolved / remaining” or add an **Implementation status (2026-05-01)** subsection pointing at this plan).
- **Step 1:** Open the SPEC and add a short subsection after **Current Gaps**, e.g. `## Implementation status`, linking to this plan file and listing only **remaining** gaps (Phases 3–5, recommendation workflow, Today Action Center, full Feishu contract, validation program).
- **Step 2:** Optionally strike or annotate resolved gap lines so readers do not assume daemon/trigger/summary issues still exist.
- **Step 3:** Commit docs only when ready:

```bash
git add docs/superpowers/specs/2026-05-01-ai-investment-assistant-design.md \
  docs/superpowers/plans/2026-05-01-ai-investment-assistant-spec-alignment.md
git commit -m "docs: align AI investment assistant SPEC with implementation status"
```

---

## G. Self-review (writing-plans checklist)


| Check                                 | Result                                                                                    |
| ------------------------------------- | ----------------------------------------------------------------------------------------- |
| SPEC coverage for “First PR sequence” | Every bullet mapped to files; all marked **[x]** where implemented.                       |
| Placeholder scan                      | No “TBD” implementation steps; open work is explicitly **[ ]** or deferred to Phases 3–5. |
| Type / naming consistency             | Table/column names match `db.py` and SPEC snippets.                                       |


---

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-05-01-ai-investment-assistant-spec-alignment.md`.

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per unchecked task (e.g. Task F.1, then Phase 3 spike), review between tasks.

**2. Inline execution** — Run tasks in this session with `executing-plans`, batch with checkpoints.

Which approach do you want for the next **[ ]** item?
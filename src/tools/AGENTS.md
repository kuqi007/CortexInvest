# src/tools/

Monitoring, data polling, alerting, and L2 strategy execution daemons.

Phase 4: Review-ready, deduplicated content. Summary-only; no fluff.

- Purpose: Tools-domain AGENTS.md for monitoring/core infra daemons.
- Key components: market_data_poller, stock_notifier, l2_strategy_daemon, l2_strategy_engine, sector_index_engine, washout_calculator, daily_summary_generator.
- Outputs/IO: market_data.json, alert_events, sim_trading.db, sector_rotation, web-api interactions (read-only web).
- Conventions: shared daemon locks, read/write boundaries, preservation of entry_time in L2, auction-hours handling.
- Data flow (high-level): poller -> market_data.json -> notifier -> alert_events; l2_daemon -> sim_trading.db -> web/sim; sector/index writes sector_rotation.
- API surface: internal api exposed by api.py; web API is read-only for UI; config updates go through POST /api/config.
- Verification: quick sanity checks on presence of key outputs and consistency of file counts.

# src/sim_trading/

Compact summary of the simulated trading engine interfaces and safety gates.

- Broker Hierarchy: AbstractBroker -> VirtualBroker (mock) and FutuBroker (OpenD)
- Signal flow: realtime_engine -> T3/T4 -> signal_mapper
- Position Manager: shadow P&L; preserve entry_time; trailing stop up-only; min_hold 30
- Risk controls (hard): max_single_stock_pct 25%; max_total_invested_pct 80%; position_pct 25%; max_new_positions_per_day 1; min_notional 30000; min_hold_minutes 30
- Testing: run test_sim_trading.py; regression tests for SyncFromFutu and TrailingStop
- Verification: ensure stop-loss path via check_exits; verify entry_time preservation

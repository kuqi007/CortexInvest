# web/

Compact UI doc: Next.js dashboard (port 3120). Read-only frontend; API handles writes.

## Stack

- Next.js 15 App Router
- TypeScript + Tailwind CSS v4
- Dracula theme (`#282a36` bg, `#f8f8f2` text)
- JetBrains Mono font
- MetricsProvider context (30s polling)

## Page → Data Source

| Page | Route | Source |
|------|-------|--------|
| Holdings | `/` | `market_data.json` + SQLite `trades` |
| Watching | `/watching` | `market_data.json` |
| Alerts | `/alerts` | SQLite `alert_events` (today only) |
| Sim | `/sim` | SQLite `live_state`, `trades`, `orders` |
| Sector | `/sector` | SQLite `sector_rotation` |
| Manage | `/manage` | `POST /api/config` (DB-first) |
| Starred | `/starred` | Filtered watchlist |

## API Routes (`app/api/`)

All routes are **read-only** except `config`:
- `GET /api/metrics` — holdings + watching snapshot
- `GET /api/summary` — daily P&L, sector P&L
- `GET /api/alerts` — today's alert_events
- `POST /api/config` — **only** write endpoint; updates DB then exports JSON
- `GET /api/sim/*` — live_state, trade plans, trading status

## State Management

- `MetricsProvider` — global context, polls `/api/metrics` every 30s
- `useAlerts` — polls `/api/alerts`, groups by hour
- `useTradePlans` — polls `/api/sim/trade-plans`
- All data is **read** from backend; no client-side computation of alerts or P&L

## Theme Tokens

Use `theme.ts` constants — never hardcode colors:
- Up: `#50fa7b`, Down: `#ff5555`, Neutral: `#8be9fd`
- Star: `#f1fa8c`, Hidden: `#6272a4`

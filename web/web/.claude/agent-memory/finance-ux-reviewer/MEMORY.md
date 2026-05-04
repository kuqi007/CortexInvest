# Finance UX Reviewer Memory

## Project: ai-investor Web Dashboard

### Design System
- **Theme**: Dracula color palette (`#282a36` bg, `#f8f8f2` fg, `#ff5555` red, `#50fa7b` green, etc.)
- **Font**: JetBrains Mono (monospace) — good for financial data alignment
- **Color convention**: Red=up, Green=down (correct for Chinese A-share market)
- **File**: `web/app/theme.ts` exports `D` constant with all colors

### Key Components
- `AppTabs.tsx` — Navigation with keyboard shortcuts (⌘1-8)
- `AppTitleBar.tsx` — macOS-style window chrome
- `StockTableHeader.tsx` — Fixed-width column headers with sort
- `DataTrustBar.tsx` — Real-time data freshness indicators (excellent)
- `QuoteStaleBanner.tsx` — Stale data warnings
- `StockDrawer.tsx` — Right-panel stock detail/editor
- `CollapsibleSectionTitle.tsx` — Section expand/collapse
- `StockTagChips.tsx` / `TagArea.tsx` — Layered tag system (risk > theme > meta)

### Critical UX Issues Found
1. **Table overflow**: All table pages (Dashboard, Starred, Watching, Sim) use fixed `ch` widths, causing horizontal overflow on standard screens
2. **Missing error boundaries**: No React Error Boundary — component crashes take down entire pages
3. **Async loading states**: Buttons don't show loading/disabled state during API calls
4. **Mobile unresponsive**: Fixed pixel layouts throughout, no breakpoints

### Code Quality Notes
- `display-utils.ts`: `chgColor()` correctly uses red=up/green=down for Chinese market
- `tag-utils.ts`: Well-designed layered tag system with risk priority
- `alerts/page.tsx`: Excellent parser registry pattern (`KIND_PARSERS`)
- `sim/page.tsx`: Comprehensive trading analytics with good visual hierarchy

### Files to Watch for UX Changes
- `web/app/components/StockTableHeader.tsx` — Column widths
- `web/app/page.tsx` — Dashboard layout
- `web/app/starred/page.tsx` — Starred page layout
- `web/app/watching/page.tsx` — Watching page layout
- `web/app/sim/page.tsx` — Sim trading layout
- `web/app/alerts/page.tsx` — Alerts layout
- `web/app/daily/page.tsx` — Daily reports layout

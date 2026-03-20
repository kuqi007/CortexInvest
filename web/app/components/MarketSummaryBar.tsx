'use client';

import { MarketTurnover } from '../types';

interface MarketSummaryBarProps {
  marketTurnover: MarketTurnover;
  market: 'A' | 'HK';
}

function chgColor(pct: number | undefined): string {
  if (pct === undefined || pct === null) return '#6272a4';
  if (pct > 0) return '#50fa7b';
  if (pct < 0) return '#ff5555';
  return '#6272a4';
}

function fmtIdx(val: number | undefined, decimals = 2): string {
  if (!val) return '—';
  return val.toFixed(decimals);
}

function fmtPct(pct: number | undefined): string {
  if (pct === undefined || pct === null) return '—';
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
}

function fmtTurnover(total: number | undefined): string {
  if (!total) return '—';
  if (total >= 1_0000_0000) return `${(total / 1_0000_0000).toFixed(2)}万亿`;
  if (total >= 1_0000) return `${(total / 1_0000).toFixed(0)}亿`;
  return `${total.toFixed(0)}元`;
}

function Divider() {
  return <span style={{ color: '#44475a', margin: '0 8px', userSelect: 'none' }}>|</span>;
}

// A-share tab
function ASummary({ mt }: { mt: MarketTurnover }) {
  const { chiNext, chiNextPct, kc50, kc50Pct, shIndex, shPct, szIndex, szPct, total, amo1, amo2 } = mt;
  const amoUp = (amo1 ?? 0) > (amo2 ?? 0);
  const amoColor = amoUp ? '#50fa7b' : '#ff5555';
  const amoArrow = amoUp ? '↑' : '↓';

  return (
    <div style={{
      display: 'flex', gap: '0', fontSize: '13px', fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      background: '#282a36', border: '1px solid #44475a', borderRadius: '4px',
      padding: '6px 12px', overflowX: 'auto', whiteSpace: 'nowrap',
    }}>
      <span>
        <span style={{ color: '#6272a4' }}>SH </span>
        <span style={{ color: chgColor(shPct) }}>{fmtIdx(shIndex, 2)} {fmtPct(shPct)}</span>
      </span>
      <Divider />
      <span>
        <span style={{ color: '#6272a4' }}>SZ </span>
        <span style={{ color: chgColor(szPct) }}>{fmtIdx(szIndex, 2)} {fmtPct(szPct)}</span>
      </span>
      <Divider />
      <span>
        <span style={{ color: '#6272a4' }}>创业板 </span>
        <span style={{ color: chgColor(chiNextPct) }}>{fmtIdx(chiNext, 2)} {fmtPct(chiNextPct)}</span>
      </span>
      <Divider />
      <span>
        <span style={{ color: '#6272a4' }}>科创50 </span>
        <span style={{ color: chgColor(kc50Pct) }}>{fmtIdx(kc50, 2)} {fmtPct(kc50Pct)}</span>
      </span>
      <Divider />
      <span>
        <span style={{ color: '#6272a4' }}>成交 </span>
        <span>{fmtTurnover(total)}</span>
      </span>
      <Divider />
      <span>
        <span style={{ color: '#6272a4' }}>AMO </span>
        <span style={{ color: amoColor }}>
          {`AMO1=${amo1?.toFixed(2) ?? '—'} AMO2=${amo2?.toFixed(2) ?? '—'} ${amoArrow}`}
        </span>
      </span>
    </div>
  );
}

// HK tab
function HKSummary({ mt }: { mt: MarketTurnover }) {
  const { hkIndex, hkIndexPct, hkTech, hkTechPct, hkTurnover } = mt;

  return (
    <div style={{
      display: 'flex', gap: '0', fontSize: '13px', fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      background: '#282a36', border: '1px solid #44475a', borderRadius: '4px',
      padding: '6px 12px', overflowX: 'auto', whiteSpace: 'nowrap',
    }}>
      <span>
        <span style={{ color: '#6272a4' }}>恒生 </span>
        <span style={{ color: chgColor(hkIndexPct) }}>{fmtIdx(hkIndex, 2)} {fmtPct(hkIndexPct)}</span>
      </span>
      <Divider />
      <span>
        <span style={{ color: '#6272a4' }}>恒生科技 </span>
        <span style={{ color: chgColor(hkTechPct) }}>{fmtIdx(hkTech, 2)} {fmtPct(hkTechPct)}</span>
      </span>
      <Divider />
      <span>
        <span style={{ color: '#6272a4' }}>成交 </span>
        <span>{fmtTurnover(hkTurnover)} <span style={{ color: '#6272a4', fontSize: '11px' }}>(参考)</span></span>
      </span>
    </div>
  );
}

export default function MarketSummaryBar({ marketTurnover, market }: MarketSummaryBarProps) {
  if (!marketTurnover) return null;
  if (market === 'HK') return <HKSummary mt={marketTurnover} />;
  return <ASummary mt={marketTurnover} />;
}

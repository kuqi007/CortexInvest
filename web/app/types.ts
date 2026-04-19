/** Shared types — single source of truth */

export interface WatchEntry {
  name: string;
  alias?: string;
  type?: string;
  cost?: number | null;
  shares?: number | null;
  lot?: number | null;
  hidden?: boolean;
  star?: boolean;
  dip_buy?: boolean;
  tags?: string[];
  pin_order?: number;
  watch_price?: number;
  watch_price_date?: string;
}

export interface MonitorConfig {
  watchlist: Record<string, WatchEntry>;
  holdings?: Record<string, WatchEntry>;
  watching?: Record<string, WatchEntry>;
  settings: Record<string, number>;
}

export interface Service {
  id: string;
  name: string;
  alias?: string;
  type: string;
  price: number;
  change: number;
  chgAmt: number;
  vol: number;
  amount: number;
  amp: number;
  turnover: number;
  volRatio: number;
  high: number;
  low: number;
  open: number;
  prevClose: number;
  cost: number | null;
  shares: number | null;
  pnl: number | null;
  above: number | null;
  below: number | null;
  hidden?: boolean;
  star?: boolean;
  dip_buy?: boolean;
  tags?: string[];
  watch_price?: number;
  watch_price_date?: string;
  pin_order?: number;
  /* Futu L2 enrichment (optional — absent when OpenD offline or A-share no permission) */
  mainNetInflow?: number;
  mainNetInflowPct?: number;
  retailNetInflow?: number;
  bidAskRatio?: number;
  avgPrice?: number;
  /* Daily technical indicators (from indicator_cache, refreshed every 30min) */
  indicators?: {
    close: number;
    rsi: number;
    dif: number;
    dea: number;
    macd_hist: number;
    macd_hist_prev: number;
    ma5: number;
    ma5_prev: number;
    ma5_prev2: number;
    ma10: number;
    ma20: number;
    vol: number;
    vol_ma20: number;
    vol_ratio: number;
    macd_golden_cross: boolean;
    macd_death_cross: boolean;
    macd_bull_divergence: boolean;
    ma5_turn_up: boolean;
  };
}

export interface AlertSettings {
  poll_interval?: number;
  big_move_pct?: number;
  cooldown_minutes?: number;
}

export interface MarketTurnover {
  sh: number;
  sz: number;
  total: number;
  shIndex: number;
  szIndex: number;
  shPct: number;
  szPct: number;
  verdict: string;
  // A-share new index fields
  chiNext?: number;      // 创业板点位
  chiNextPct?: number;  // 创业板涨跌幅
  kc50?: number;        // 科创50点位
  kc50Pct?: number;     // 科创50涨跌幅
  // HK index fields
  hkIndex?: number;     // 恒生指数点位
  hkIndexPct?: number; // 恒生指数涨跌幅
  hkTech?: number;      // 恒生科技点位
  hkTechPct?: number;  // 恒生科技涨跌幅
  hkTurnover?: number;  // 港股成交额（HK.800000 turnover）
  // AMO
  amo1: number;
  amo2: number;
}

export interface AlertEvent {
  ts: number;
  time: string;
  symbol: string;
  kind: string;
  level?: number;
  message: string;
  display: string;
  change_pct: number;
}

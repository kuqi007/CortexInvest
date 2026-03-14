/**
 * Trading hours utilities
 * Now uses real trading calendar from Futu API via /api/trading-status
 */

import { useState, useEffect } from "react";

/** Trading status from API */
interface TradingStatus {
  trading: boolean;
  markets: {
    cn: boolean;
    hk: boolean;
  };
  time: string;
}

/** Global cache for trading status */
let _cachedStatus: TradingStatus | null = null;
let _cacheTime = 0;
const CACHE_TTL = 60000; // 1 minute cache

/** Fetch trading status from API (with cache) */
export async function fetchTradingStatus(): Promise<TradingStatus> {
  const now = Date.now();
  if (_cachedStatus && now - _cacheTime < CACHE_TTL) {
    return _cachedStatus;
  }

  try {
    const res = await fetch("/api/trading-status", { cache: "no-store" });
    const json = await res.json();
    if (json.success && json.data) {
      _cachedStatus = json.data;
      _cacheTime = now;
      return json.data;
    }
  } catch {
    // Fall through to fallback
  }

  // Fallback: simple weekday check
  const day = new Date().getDay();
  const isWeekday = day >= 1 && day <= 5;
  const mins = new Date().getHours() * 60 + new Date().getMinutes();
  const cnHours = (mins >= 9 * 60 + 15 && mins <= 11 * 60 + 30) || (mins >= 13 * 60 && mins <= 15 * 60);
  const hkHours = (mins >= 9 * 60 + 15 && mins <= 12 * 60) || (mins >= 13 * 60 && mins <= 16 * 60);

  return {
    trading: isWeekday && (cnHours || hkHours),
    markets: {
      cn: isWeekday && cnHours,
      hk: isWeekday && hkHours,
    },
    time: new Date().toISOString(),
  };
}

/** Check if any market is trading (synchronous fallback) */
export function isAnyMarketTrading(_hasHK: boolean): boolean {
  // This is now async, use the React hook instead
  const day = new Date().getDay();
  if (day < 1 || day > 5) return false;
  const mins = new Date().getHours() * 60 + new Date().getMinutes();
  return (mins >= 9 * 60 + 15 && mins <= 11 * 60 + 30) ||
         (mins >= 13 * 60 && mins <= 15 * 60) ||
         (mins >= 9 * 60 + 15 && mins <= 12 * 60) ||
         (mins >= 13 * 60 && mins <= 16 * 60);
}

/** Get current market status text (sync fallback) */
export function getMarketStatus(_hasHK: boolean): string {
  // Prefer async version, but provide sync fallback
  if (isAnyMarketTrading(_hasHK)) {
    return "交易中";
  }
  return "休市";
}

/** React hook for real trading status */
export function useTradingStatus() {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;

    const fetchStatus = async () => {
      const data = await fetchTradingStatus();
      if (mounted) {
        setStatus(data);
        setLoading(false);
      }
    };

    fetchStatus();

    // Refresh every minute
    const interval = setInterval(fetchStatus, 60000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  return { status, loading };
}

"use client";

import { useEffect, useState, useCallback } from "react";

export interface EarningsEntry {
  symbol: string;
  name: string;
  report_date: string;
  period: string;
  // may also have 中文 keys from akshare cache
  代码?: string;
  简称?: string;
  公告时间?: string;
  公告标题?: string;
}

interface EarningsData {
  success: boolean;
  count: number;
  upcoming: EarningsEntry[];
  last_updated: string;
  error?: string;
}

const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

export function useEarnings(daysAhead: number = 30) {
  const [data, setData] = useState<EarningsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const fetchEarnings = useCallback(async () => {
    try {
      const resp = await fetch(`/api/earnings?days=${daysAhead}`, { cache: "no-store" });
      const json: EarningsData = await resp.json();
      setData(json);
      setError(json.success ? null : json.error || "fetch failed");
      setLastRefresh(new Date());
    } catch (e: any) {
      setError(`network: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [daysAhead]);

  useEffect(() => {
    fetchEarnings();
    const timer = setInterval(fetchEarnings, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [fetchEarnings]);

  return { data, loading, error, lastRefresh, refresh: fetchEarnings };
}

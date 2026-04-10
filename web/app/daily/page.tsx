"use client";

import { useEffect, useState, useCallback } from "react";
import { D } from "../theme";
import { AppTabs } from "../components/AppTabs";
import { AppTitleBar } from "../components/AppTitleBar";

interface MorningBriefing {
  generated_at: string;
  us_markets: Record<string, { name: string; change_pct: number }>;
  asia_markets: Record<string, { name: string; change_pct: number }>;
  global_news: { title: string; summary: string; source: string; time: string; url: string }[];
}

interface DailySummary {
  date: string;
  generatedAt: number;
  report?: string;
  morning?: MorningBriefing | null;
}

interface CloseEvent {
  time: string;
  message: string;
  display: string;
}

function stripBold(s: string) {
  return s.replace(/\*\*([^*]+)\*\*/g, "$1");
}

function TerminalMarkdown({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        const trimmed = line.trimStart();
        if (trimmed.startsWith("### ")) {
          return (
            <div key={i} style={{ color: D.cyan, fontWeight: 600, marginTop: 6, marginBottom: 2 }}>
              {stripBold(trimmed.slice(4))}
            </div>
          );
        }
        if (trimmed.startsWith("## ")) {
          return (
            <div key={i} style={{ color: D.purple, fontWeight: 700, marginTop: 8, marginBottom: 2 }}>
              {stripBold(trimmed.slice(3))}
            </div>
          );
        }
        if (trimmed.startsWith("# ")) {
          return (
            <div key={i} style={{ color: D.yellow, fontWeight: 700, marginTop: i > 0 ? 8 : 0, marginBottom: 4 }}>
              {stripBold(trimmed.slice(2))}
            </div>
          );
        }
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          const content = trimmed.slice(2);
          return (
            <div key={i} style={{ color: D.fg, paddingLeft: 16 }}>
              <span style={{ color: D.comment }}>  - </span>
              <MarkdownInline text={content} />
            </div>
          );
        }
        const numMatch = trimmed.match(/^(\d+)\.\s(.+)/);
        if (numMatch) {
          return (
            <div key={i} style={{ color: D.fg, paddingLeft: 16 }}>
              <span style={{ color: D.comment }}>  {numMatch[1]}. </span>
              <MarkdownInline text={numMatch[2]} />
            </div>
          );
        }
        if (!trimmed) return <div key={i} style={{ height: 6 }} />;
        return (
          <div key={i} style={{ color: D.fg }}>
            <MarkdownInline text={trimmed} />
          </div>
        );
      })}
    </>
  );
}

function MarkdownInline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <span key={i} style={{ color: D.orange, fontWeight: 600 }}>
              {part.slice(2, -2)}
            </span>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function MarketCard({ label, markets }: { label: string; markets: Record<string, { name: string; change_pct: number }> }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ color: D.comment, fontSize: 11, marginBottom: 4 }}>{label}</div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {Object.values(markets).map((m, i) => (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span style={{ color: D.fg, fontSize: 12 }}>{m.name}</span>
            <span style={{ color: m.change_pct >= 0 ? D.green : D.red, fontWeight: 600, fontSize: 12 }}>
              {m.change_pct >= 0 ? "+" : ""}{m.change_pct.toFixed(2)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function NewsCard({ news }: { news: MorningBriefing["global_news"] }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ color: D.purple, fontSize: 11, marginBottom: 6 }}>重要新闻</div>
      {news.slice(0, 6).map((n, i) => (
        <div key={i} style={{ marginBottom: 8, paddingLeft: 8, borderLeft: `2px solid ${D.comment}44` }}>
          <div style={{ color: D.yellow, fontSize: 12, marginBottom: 2 }}>{n.title}</div>
          <div style={{ color: D.comment, fontSize: 10 }}>
            <span>{n.source}</span>
            <span style={{ marginLeft: 8 }}>{n.time}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function DailyPage() {
  const [data, setData] = useState<DailySummary | null>(null);
  const [closeEvent, setCloseEvent] = useState<CloseEvent | null>(null);
  const [briefingOpen, setBriefingOpen] = useState(true);
  const [summaryOpen, setSummaryOpen] = useState(true);
  const [closeOpen, setCloseOpen] = useState(true);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/summary", { cache: "no-store" });
      const json = await res.json();
      if (json.data) setData(json.data);
    } catch {
      // non-critical
    }
  }, []);

  const fetchCloseEvent = useCallback(async () => {
    try {
      const res = await fetch("/api/close-events", { cache: "no-store" });
      const json = await res.json();
      if (json.data) setCloseEvent(json.data);
    } catch {
      // non-critical
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    fetchCloseEvent();
    const t = setInterval(() => {
      fetchData();
      fetchCloseEvent();
    }, 60_000);
    return () => clearInterval(t);
  }, [fetchData, fetchCloseEvent]);

  const morning = data?.morning;
  const hasReport = data?.report && data.date === new Date().toISOString().slice(0, 10);

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        background: D.bg,
        fontFamily: '"JetBrains Mono", Menlo, Monaco, "Courier New", monospace',
        color: D.fg,
        fontSize: 13,
      }}
    >
      <AppTitleBar title="daily — morning & evening reports" />

      <AppTabs active="daily" />

      <div style={{ flex: 1, overflow: "auto", padding: "16px 20px", lineHeight: 1.6 }}>
        {loading ? (
          <div style={{ color: D.comment }}>Loading...</div>
        ) : (
          <>
            {/* Market Close Notification Card */}
            {closeEvent && (
              <div
                style={{
                  border: `1px solid ${D.yellow}44`,
                  borderRadius: 6,
                  marginBottom: 16,
                  background: "#21222c",
                }}
              >
                <div
                  onClick={() => setCloseOpen(!closeOpen)}
                  style={{
                    padding: "10px 16px",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    userSelect: "none",
                    borderBottom: closeOpen ? `1px solid ${D.yellow}33` : "none",
                  }}
                >
                  <span style={{ color: D.yellow, fontSize: 12 }}>
                    {closeOpen ? "\u25be" : "\u25b8"}
                  </span>
                  <span style={{ color: D.yellow, fontWeight: 700, fontSize: 13 }}>
                    收盘通知 — {closeEvent.time}
                  </span>
                </div>

                {closeOpen && (
                  <div style={{ padding: "16px 20px", lineHeight: 1.7 }}>
                    <div style={{ color: D.fg, whiteSpace: "pre-wrap" }}>
                      {closeEvent.display}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Morning Briefing Card */}
            {morning && (
              <div
                style={{
                  border: `1px solid ${D.cyan}44`,
                  borderRadius: 6,
                  marginBottom: 16,
                  background: "#21222c",
                }}
              >
                <div
                  onClick={() => setBriefingOpen(!briefingOpen)}
                  style={{
                    padding: "10px 16px",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    userSelect: "none",
                    borderBottom: briefingOpen ? `1px solid ${D.cyan}33` : "none",
                  }}
                >
                  <span style={{ color: D.cyan, fontSize: 12 }}>
                    {briefingOpen ? "\u25be" : "\u25b8"}
                  </span>
                  <span style={{ color: D.cyan, fontWeight: 700, fontSize: 13 }}>
                    早间简报 — {morning.generated_at?.slice(0, 10)}
                  </span>
                  {Object.keys(morning.us_markets).length > 0 && (
                    <span style={{ color: D.comment, fontSize: 11 }}>
                      美股:{" "}
                      {Object.values(morning.us_markets).map((m, i) => (
                        <span key={i} style={{ color: m.change_pct >= 0 ? D.green : D.red }}>
                          {m.name}{m.change_pct >= 0 ? "+" : ""}{m.change_pct}%{" "}
                        </span>
                      ))}
                    </span>
                  )}
                  {Object.keys(morning.asia_markets).length > 0 && (
                    <span style={{ color: D.comment, fontSize: 11 }}>
                      亚股:{" "}
                      {Object.values(morning.asia_markets).map((m, i) => (
                        <span key={i} style={{ color: m.change_pct >= 0 ? D.green : D.red }}>
                          {m.name}{m.change_pct >= 0 ? "+" : ""}{m.change_pct}%{" "}
                        </span>
                      ))}
                    </span>
                  )}
                  <span style={{ color: D.comment, fontSize: 11, marginLeft: "auto" }}>
                    {morning.global_news.length} 条新闻
                  </span>
                </div>

                {briefingOpen && (
                  <div style={{ padding: "16px 20px" }}>
                    {Object.keys(morning.us_markets).length > 0 && (
                      <MarketCard label="美股" markets={morning.us_markets} />
                    )}
                    {Object.keys(morning.asia_markets).length > 0 && (
                      <MarketCard label="亚太" markets={morning.asia_markets} />
                    )}
                    {morning.global_news.length > 0 && (
                      <NewsCard news={morning.global_news} />
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Daily Summary Card */}
            {hasReport && data.report && (
              <div
                style={{
                  border: `1px solid ${D.purple}44`,
                  borderRadius: 6,
                  background: "#21222c",
                }}
              >
                <div
                  onClick={() => setSummaryOpen(!summaryOpen)}
                  style={{
                    padding: "10px 16px",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    userSelect: "none",
                    borderBottom: summaryOpen ? `1px solid ${D.purple}33` : "none",
                  }}
                >
                  <span style={{ color: D.purple, fontSize: 12 }}>
                    {summaryOpen ? "\u25be" : "\u25b8"}
                  </span>
                  <span style={{ color: D.purple, fontWeight: 700, fontSize: 13 }}>
                    信号日报 — {data.date}
                  </span>
                </div>

                {summaryOpen && (
                  <div style={{ padding: "16px 20px", lineHeight: 1.7 }}>
                    <TerminalMarkdown text={data.report} />
                  </div>
                )}
              </div>
            )}

            {!morning && !hasReport && (
              <div style={{ color: D.comment, padding: "20px 0" }}>
                # 暂无数据
                <br />
                早间简报在 8:30 前生成，收盘后生成信号日报。
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

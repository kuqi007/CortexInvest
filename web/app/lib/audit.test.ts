import { describe, expect, it, vi } from "vitest";
import goldenEvent from "../../../tests/fixtures/audit_event_v2.json";
import { auditTableEnabled, buildAuditEventV2, makeActor, recordDbChangeBestEffort } from "./audit";

describe("audit helper", () => {
  it("builds the shared v2 golden event", () => {
    const event = buildAuditEventV2({
      eventId: "01HWNM4Z7G8E6Q9M3R2T1V0X5K",
      tsMs: 1777376520000,
      correlationId: "01HWNM4Z7G8E6Q9M3R2T1V0X5Y",
      source: "api_config",
      actor: makeActor({ type: "user", id: "local-user", host: "localhost" }),
      action: "update",
      entity: "monitor_watchlist",
      key: "HK09988",
      dbName: "config.db",
      before: { shares: 1000 },
      after: { shares: 800 },
      metadata: { request_id: "req-1" },
    });

    expect(event).toEqual(goldenEvent);
  });

  it("formats ts like Python by truncating sub-second milliseconds", () => {
    const event = buildAuditEventV2({
      eventId: "evt",
      tsMs: 1777376520123,
      source: "api_config",
      actor: makeActor({ type: "user", id: "local-user" }),
      action: "update",
      entity: "monitor_watchlist",
      key: "HK09988",
      dbName: "config.db",
      before: null,
      after: {},
    });

    expect(event.ts).toBe("2026-04-28T11:42:00Z");
  });

  it("rejects sensitive fields like the Python helper", () => {
    expect(() =>
      buildAuditEventV2({
        eventId: "evt",
        tsMs: 1777376520000,
        source: "api_config",
        actor: makeActor({ type: "user", id: "local-user" }),
        action: "update",
        entity: "monitor_watchlist",
        key: "HK09988",
        dbName: "config.db",
        before: null,
        after: { api_token: "secret" },
      }),
    ).toThrow(/sensitive audit field/);
  });

  it("rejects obvious sensitive values like the Python helper", () => {
    expect(() =>
      buildAuditEventV2({
        eventId: "evt",
        tsMs: 1777376520000,
        source: "api_config",
        actor: makeActor({ type: "user", id: "local-user" }),
        action: "update",
        entity: "monitor_watchlist",
        key: "HK09988",
        dbName: "config.db",
        before: null,
        after: { note: "Bearer abcdefghijklmnopqrstuvwxyz" },
      }),
    ).toThrow(/sensitive audit value/);
  });

  it("marks high-frequency trading tables as excluded", () => {
    expect(auditTableEnabled("trading.db", "price_snapshots")).toBe(false);
    expect(auditTableEnabled("trading.db", "market_turnover")).toBe(false);
    expect(auditTableEnabled("trading.db", "signals")).toBe(false);
    expect(auditTableEnabled("trading.db", "session_snapshots")).toBe(false);
    expect(auditTableEnabled("trading.db", "tick_monitor_events")).toBe(false);
    expect(auditTableEnabled("trading.db", "trade_plans")).toBe(true);
  });

  it("best-effort writer skips excluded tables", () => {
    const db = {} as never;
    const ok = recordDbChangeBestEffort(db, {
      dbName: "trading.db",
      table: "signals",
      action: "create",
      key: "sig-1",
      source: "test",
      actor: makeActor({ type: "system", id: "vitest" }),
      before: null,
      after: { code: "HK00700" },
    });

    expect(ok).toBe(false);
  });

  it("best-effort writer catches audit failures", () => {
    const db = {
      exec: vi.fn(() => {
        throw new Error("ddl unavailable");
      }),
    } as never;
    const ok = recordDbChangeBestEffort(db, {
      dbName: "trading.db",
      table: "trade_plans",
      action: "create",
      key: "p1",
      source: "test",
      actor: makeActor({ type: "system", id: "vitest" }),
      before: null,
      after: { id: "p1" },
    });

    expect(ok).toBe(false);
  });
});

describe("db path override guard", () => {
  it("rejects DB path overrides unless explicitly allowed for tests", async () => {
    process.env.AI_INVESTOR_CONFIG_DB_PATH = "/tmp/unsafe-config.db";
    delete process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE;
    vi.resetModules();

    await expect(import("./db")).rejects.toThrow(/AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE/);

    delete process.env.AI_INVESTOR_CONFIG_DB_PATH;
    vi.resetModules();
  });
});

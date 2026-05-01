/**
 * Tests for /api/health GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/health/route.test.ts
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import Database from "better-sqlite3";
import { join } from "path";
import { tmpdir } from "os";
import { unlinkSync } from "fs";

const TEST_DB_PATH = join(tmpdir(), "test_health.db");
let healthGet: typeof import("./route").GET;

function cleanupTestDb() {
  try {
    unlinkSync(TEST_DB_PATH);
  } catch {
    /* ignore */
  }
  try {
    unlinkSync(TEST_DB_PATH + "-wal");
  } catch {
    /* ignore */
  }
  try {
    unlinkSync(TEST_DB_PATH + "-shm");
  } catch {
    /* ignore */
  }
}

function setupEmptyDb() {
  cleanupTestDb();
  const db = new Database(TEST_DB_PATH);
  db.pragma("journal_mode = WAL");
  db.exec(`
    CREATE TABLE price_snapshots (ts INTEGER NOT NULL);
    CREATE TABLE market_turnover (ts INTEGER NOT NULL);
    CREATE TABLE job_requests (
      job_type TEXT NOT NULL,
      status TEXT NOT NULL
    );
    CREATE TABLE job_runs (
      id TEXT PRIMARY KEY,
      job_type TEXT NOT NULL,
      runner TEXT NOT NULL,
      status TEXT NOT NULL,
      started_at_ms INTEGER NOT NULL,
      finished_at_ms INTEGER,
      error TEXT,
      correlation_id TEXT NOT NULL
    );
  `);
  db.close();
}

describe("GET /api/health", () => {
  beforeAll(async () => {
    process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE = "1";
    process.env.AI_INVESTOR_TRADING_DB_PATH = TEST_DB_PATH;
    setupEmptyDb();
    healthGet = (await import("./route")).GET;
  });

  afterAll(() => {
    delete process.env.AI_INVESTOR_TRADING_DB_PATH;
    delete process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE;
    cleanupTestDb();
  });

  it("returns unknown freshness when no snapshots exist", async () => {
    setupEmptyDb();
    const res = await healthGet();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.success).toBe(true);
    expect(body.freshness.overall).toBe("unknown");
    expect(body.freshness.overallScope).toBe("price_snapshots");
    expect(body.data.priceSnapshotsMs).toBeNull();
    expect(body.data.jobs.earningsCheckPending).toBe(0);
    expect(body.data.jobs.recentFailures).toEqual([]);
    expect(body.warnings).toEqual([]);
  });

  it("marks live when snapshots are recent", async () => {
    setupEmptyDb();
    const now = Date.now();
    const db = new Database(TEST_DB_PATH);
    db.exec(`INSERT INTO price_snapshots (ts) VALUES (${now});`);
    db.exec(`INSERT INTO market_turnover (ts) VALUES (${now});`);
    db.close();

    const res = await healthGet();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.freshness.overall).toBe("live");
    expect(body.warnings).toEqual([]);
    expect(body.data.priceSnapshotsMs).toBe(now);
  });

  it("warns when snapshots are stale", async () => {
    setupEmptyDb();
    const old = Date.now() - 200_000;
    const db = new Database(TEST_DB_PATH);
    db.exec(`INSERT INTO price_snapshots (ts) VALUES (${old});`);
    db.exec(`INSERT INTO market_turnover (ts) VALUES (${old});`);
    db.close();

    const res = await healthGet();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.freshness.overall).toBe("stale");
    expect(body.warnings).toContain("price_snapshots_stale");
    expect(body.warnings).toContain("market_turnover_stale");
  });

  it("reports pending earnings jobs and recent failures", async () => {
    setupEmptyDb();
    const db = new Database(TEST_DB_PATH);
    db.exec(
      `INSERT INTO job_requests (job_type, status) VALUES ('earnings_check', 'pending'), ('earnings_check', 'pending');`,
    );
    db.exec(
      `INSERT INTO job_runs (id, job_type, runner, status, started_at_ms, finished_at_ms, error, correlation_id)
       VALUES ('r1', 'earnings_check', 'test', 'failed', 1, 9999, 'boom', 'corr-1');`,
    );
    db.close();

    const res = await healthGet();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.data.jobs.earningsCheckPending).toBe(2);
    expect(body.data.jobs.recentFailures).toHaveLength(1);
    expect(body.data.jobs.recentFailures[0].error).toBe("boom");
    expect(body.warnings).toContain("recent_job_failures");
  });

  it("only lists earnings_check failures and redacts long errors", async () => {
    setupEmptyDb();
    const db = new Database(TEST_DB_PATH);
    const longErr = "x".repeat(400);
    db.exec(
      `INSERT INTO job_runs (id, job_type, runner, status, started_at_ms, finished_at_ms, error, correlation_id)
       VALUES ('r-earn', 'earnings_check', 'd', 'failed', 1, 9999, '${longErr}', '${"c".repeat(80)}');`,
    );
    db.exec(
      `INSERT INTO job_runs (id, job_type, runner, status, started_at_ms, finished_at_ms, error, correlation_id)
       VALUES ('r-other', 'other_job', 'd', 'failed', 2, 9999, 'other-err', 'corr-x');`,
    );
    db.close();

    const res = await healthGet();
    const body = await res.json();
    expect(body.data.jobs.recentFailures).toHaveLength(1);
    expect(body.data.jobs.recentFailures[0].id).toBe("r-earn");
    expect(body.data.jobs.recentFailures[0].error?.length).toBeLessThanOrEqual(201);
    expect(body.data.jobs.recentFailures[0].error?.endsWith("…")).toBe(true);
    expect(body.data.jobs.recentFailures[0].correlation_id.length).toBeLessThanOrEqual(65);
  });
});

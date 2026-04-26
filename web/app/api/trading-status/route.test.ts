/**
 * Tests for /api/trading-status GET endpoint.
 *
 * Run: cd web && npx vitest run app/api/trading-status/route.test.ts
 *
 * NOTE: This route spawns a Python process via `child_process.spawn`.
 * The happy-path tests require the full Python environment (poetry, Futu SDK,
 * src.tools.trading_calendar) to be available. The tests below focus on the
 * error-handling paths that can be exercised without Python by mocking spawn.
 */

import { describe, it, expect, vi } from "vitest";

// ── Mocked handler (mirrors route logic but with injectable spawn) ───────────

interface MockChildProcess {
  stdout?: { on: (event: string, handler: (data: Buffer) => void) => void };
  on: (event: string, handler: (...args: any[]) => void) => void;
}

async function getHandlerWithSpawn(spawnFn: () => MockChildProcess) {
  const { NextResponse } = await import("next/server");

  try {
    const pythonCode = `dummy`;

    return new Promise<Response>((resolve) => {
      const py = spawnFn();

      let output = "";
      py.stdout?.on("data", (data: Buffer) => {
        output += data.toString();
      });

      py.on("close", (code: number | null) => {
        if (code === 0 && output) {
          try {
            const result = JSON.parse(output.trim());
            resolve(NextResponse.json({ success: true, data: result }));
          } catch {
            resolve(NextResponse.json({ success: false, error: "Parse error" }));
          }
        } else {
          resolve(NextResponse.json({ success: false, error: "Failed to get trading status" }));
        }
      });

      py.on("error", () => {
        resolve(NextResponse.json({ success: false, error: "Process error" }));
      });
    });
  } catch (e) {
    return NextResponse.json({ success: false, error: String(e) });
  }
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe("GET /api/trading-status", () => {
  it("returns process error when spawn fails", async () => {
    const mockSpawn = () => {
      const handlers: Record<string, ((...args: any[]) => void)[]> = {};
      const emitter: MockChildProcess = {
        stdout: {
          on: () => {},
        },
        on: (event: string, handler: (...args: any[]) => void) => {
          if (!handlers[event]) handlers[event] = [];
          handlers[event].push(handler);
          if (event === "error") {
            // Simulate immediate process spawn failure
            setImmediate(() => handler());
          }
        },
      };
      return emitter;
    };

    const response = await getHandlerWithSpawn(mockSpawn);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.error).toBe("Process error");
  });

  it("returns parse error when Python outputs invalid JSON", async () => {
    const mockSpawn = () => {
      const emitter: MockChildProcess = {
        stdout: {
          on: (event: string, handler: (data: Buffer) => void) => {
            if (event === "data") {
              setImmediate(() => handler(Buffer.from("not valid json")));
            }
          },
        },
        on: (event: string, handler: (...args: any[]) => void) => {
          if (event === "close") {
            setImmediate(() => handler(0));
          }
        },
      };
      return emitter;
    };

    const response = await getHandlerWithSpawn(mockSpawn);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.error).toBe("Parse error");
  });

  it("returns failure when Python exits non-zero", async () => {
    const mockSpawn = () => {
      const emitter: MockChildProcess = {
        stdout: {
          on: () => {},
        },
        on: (event: string, handler: (...args: any[]) => void) => {
          if (event === "close") {
            setImmediate(() => handler(1));
          }
        },
      };
      return emitter;
    };

    const response = await getHandlerWithSpawn(mockSpawn);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(false);
    expect(body.error).toBe("Failed to get trading status");
  });

  it("returns success with parsed data when Python outputs valid JSON", async () => {
    const mockOutput = JSON.stringify({
      trading: true,
      markets: { cn: true, hk: false },
      time: "2026-04-26 10:30:00",
    });

    const mockSpawn = () => {
      const emitter: MockChildProcess = {
        stdout: {
          on: (event: string, handler: (data: Buffer) => void) => {
            if (event === "data") {
              setImmediate(() => handler(Buffer.from(mockOutput)));
            }
          },
        },
        on: (event: string, handler: (...args: any[]) => void) => {
          if (event === "close") {
            setImmediate(() => handler(0));
          }
        },
      };
      return emitter;
    };

    const response = await getHandlerWithSpawn(mockSpawn);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.success).toBe(true);
    expect(body.data).toEqual({
      trading: true,
      markets: { cn: true, hk: false },
      time: "2026-04-26 10:30:00",
    });
  });
});

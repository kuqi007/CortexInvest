import { describe, expect, it } from "vitest";

import { buildPollHint, chgColor, fmtAmt, fmtMoney, pad } from "./display-utils";
import { D } from "../theme";

describe("display-utils", () => {
  it("builds consistent poll hints for dashboard pages", () => {
    expect(buildPollHint(30_000, "holdings")).toBe("每 30 秒拉取 /api/metrics · holdings");
    expect(buildPollHint(45_000, "starred")).toBe("每 45 秒拉取 /api/metrics · starred");
    expect(buildPollHint(1_500, "watching")).toBe("每 1.5 秒拉取 /api/metrics · watching");
  });

  it("formats terminal-aligned values consistently", () => {
    expect(pad("7", 3)).toBe("7  ");
    expect(pad("7", 3, true)).toBe("  7");
    expect(fmtAmt(123_456_789)).toBe("1.2亿");
    expect(fmtAmt(-12_345)).toBe("-1万");
    expect(fmtMoney(12_345)).toBe("+1.2万");
    expect(fmtMoney(-999)).toBe("-999");
  });

  it("uses market color semantics for change values", () => {
    expect(chgColor(1)).toBe(D.red);
    expect(chgColor(-1)).toBe(D.green);
    expect(chgColor(0)).toBe(D.comment);
  });
});

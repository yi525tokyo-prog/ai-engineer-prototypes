import { describe, expect, it } from "vitest";
import { ago, componentLabel, estimateDelta, num, signed, sortedComponents, sparkPoints, statusTone, symmetricMax } from "./format";

describe("format", () => {
  it("formats numbers compactly", () => {
    expect(num(180000)).toBe("180,000");
    expect(num(0.5)).toBe("0.5");
    expect(num(null)).toBe("—");
    expect(signed(0.1234)).toBe("+0.123");
    expect(signed(-0.5, 1)).toBe("-0.5");
  });
  it("relative time", () => {
    const now = Date.parse("2026-09-29T00:10:00Z");
    expect(ago("2026-09-29T00:09:30Z", now)).toBe("30s ago");
    expect(ago("2026-09-29T00:00:00Z", now)).toBe("10m ago");
  });
  it("sorts score components by absolute contribution", () => {
    const s = sortedComponents({ a: 0.1, b: -0.9, c: 0.5 });
    expect(s.map((x) => x[0])).toEqual(["b", "c", "a"]);
    expect(symmetricMax([0.1, -0.9, 0.5])).toBe(0.9);
    expect(componentLabel("constitution:keeps_project_alive")).toBe("Constitution: keeps project alive");
  });
  it("maps a series into a sparkline box", () => {
    const pts = sparkPoints([1, 2, 3], 100, 20, 0).split(" ");
    expect(pts[0]).toBe("0.0,20.0");
    expect(pts[2]).toBe("100.0,0.0");
    expect(sparkPoints([], 10, 10)).toBe("");
  });
  it("status tones and estimate deltas", () => {
    expect(statusTone("succeeded")).toBe("good");
    expect(statusTone("waiting_human")).toBe("warn");
    expect(statusTone("failed")).toBe("bad");
    expect(estimateDelta({ success_probability: { from: 0.55, to: 0.1375 } })).toEqual(["success probability 0.55 → 0.14"]);
  });
});

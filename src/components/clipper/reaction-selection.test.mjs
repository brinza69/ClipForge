import assert from "node:assert/strict";
import test from "node:test";
import { selectionRect, readSourceFrame, REACTION_ASPECT } from "./reaction-selection.ts";

test("scaled browser coordinates produce exact original pixels, in either direction", () => {
  const a = { x: 174 * 2, y: 64 * 2 }, b = { x: 831 * 2, y: 463 * 2 };
  const expected = { x: 348, y: 128, w: 1314, h: 798 };
  assert.deepEqual(selectionRect(a, b, 1920, 1080, false), expected);
  assert.deepEqual(selectionRect(b, a, 1920, 1080, false), expected);
});

test("reaction drawing stays within the drag and source with a non-stretching aspect", () => {
  for (const a of [{ x: 0, y: 0 }, { x: 1919, y: 1079 }, { x: 417, y: 333 }]) {
    for (const b of [{ x: -50, y: -20 }, { x: 2000, y: 1200 }, { x: 821, y: 953 }]) {
      const r = selectionRect(a, b, 1920, 1080, true);
      if (!r) continue;
      assert.ok(Object.values(r).every((n) => Number.isInteger(n) && n % 2 === 0));
      assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.w <= 1920 && r.y + r.h <= 1080);
      assert.ok(Math.abs(r.w - r.h * REACTION_ASPECT) <= 1);
    }
  }
  assert.equal(selectionRect({ x: 2, y: 2 }, { x: 3, y: 3 }, 1920, 1080, true), null);
});

const valid = () => new Headers({
  "X-Source-Width": "1920", "X-Source-Height": "1080", "X-Source-Time": "2692.5167",
  "X-Source-Version": "guard", "X-Clip-Start": "2692", "X-Clip-End": "2704",
});

test("metadata retains actual decoded time, and missing values never become zero", () => {
  assert.equal(readSourceFrame(valid()).time, 2692.5167);
  for (const key of valid().keys()) {
    const headers = valid(); headers.delete(key);
    assert.throws(() => readSourceFrame(headers));
  }
  for (const time of ["NaN", "Infinity", "2691.99", "2704"]) {
    const headers = valid(); headers.set("X-Source-Time", time);
    assert.throws(() => readSourceFrame(headers));
  }
});

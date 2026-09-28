import assert from "node:assert/strict";
import test from "node:test";
import { rangeTooLong } from "./range-limit.ts";

// codex-verdict-next-15 R2: a clip [0, 100.0004] whose stored duration rounds to 100.000,
// in a project whose maximum is 90 s.
const long = { start: 0, end: 100.0004 };

test("a title-only edit of a clip already over the maximum is not blocked", () => {
  assert.equal(rangeTooLong(long, 0, 100.0004, 90), false);
});

test("trimming such a clip is allowed, lengthening it is not", () => {
  assert.equal(rangeTooLong(long, 0, 100.0, 90), false);
  assert.equal(rangeTooLong(long, 0.5, 100.5004, 90), false);    // a same-length move
  assert.equal(rangeTooLong(long, 0, 100.1, 90), true);
});

test("the limit is the server's, whatever it is (R1: not only 90/90)", () => {
  const clip = { start: 0, end: 30 };
  assert.equal(rangeTooLong(clip, 0, 110, 120), false);            // server default 120 s allows it
  assert.equal(rangeTooLong(clip, 0, 121, 120), true);
  assert.equal(rangeTooLong(clip, 0, 61, 60), true);
  assert.equal(rangeTooLong(clip, 0, 60, 60), false);              // exactly the maximum
});

test("without the server's limit nothing is refused here", () => {
  assert.equal(rangeTooLong({ start: 0, end: 30 }, 0, 5000, undefined), false);
  assert.equal(rangeTooLong({ start: 0, end: 30 }, 0, 5000, Number.NaN), false);
});

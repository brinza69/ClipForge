"use strict";
/** Plain-node tests for the scheduling core. Run: `node n8n/lib/schedule.test.js` */

const assert = require("assert");
const { planSchedule, parseSlots, localToUTC, tzOffsetMinutes } = require("./schedule");

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log("  ok  " + name); }
  catch (e) { console.error("FAIL  " + name + "\n      " + e.message); process.exitCode = 1; }
}

// ── timezone helpers ────────────────────────────────────────────────────────
test("tzOffsetMinutes: Bucharest is UTC+2 or +3", () => {
  const off = tzOffsetMinutes(new Date("2026-07-03T00:00:00Z"), "Europe/Bucharest");
  assert.ok(off === 120 || off === 180, "got " + off);
});

test("localToUTC: 09:00 Bucharest summer = 06:00 UTC", () => {
  const utc = localToUTC(2026, 7, 3, 9, 0, "Europe/Bucharest"); // EEST = UTC+3
  assert.strictEqual(utc.toISOString(), "2026-07-03T06:00:00.000Z");
});

test("localToUTC: 09:00 Paris summer = 07:00 UTC (different country IP/tz)", () => {
  const utc = localToUTC(2026, 7, 3, 9, 0, "Europe/Paris"); // CEST = UTC+2
  assert.strictEqual(utc.toISOString(), "2026-07-03T07:00:00.000Z");
});

test("parseSlots sorts and parses", () => {
  const s = parseSlots("19:00, 09:00,13:30");
  assert.deepStrictEqual(s.map((x) => x.label), ["09:00", "13:30", "19:00"]);
});

// ── scheduling logic ────────────────────────────────────────────────────────
const rows = [{
  nr: "42", DESCRIERE: "O poveste faină",
  narator_url: "https://drive/n1.mp4",
  comentator_url: "https://drive/c1.mp4",
  povestitor_url: "https://drive/p1.mp4",
}];

test("assigns two videos of one role to the next two open slots (RO)", () => {
  const now = new Date("2026-07-03T07:00:00Z"); // 10:00 Bucharest
  const rows2 = [{
    nr: "1", DESCRIERE: "cap",
    narator_url: "https://drive/a.mp4\nhttps://drive/b.mp4", // 2 parts
  }];
  const accounts = [{
    account_id: "ro_naratorul", role: "narator", country: "RO",
    integration_id: "postiz_ro_1", slots: "09:00,13:00,19:00", tz: "Europe/Bucharest",
  }];
  const { jobs } = planSchedule({ readyRows: rows2, accounts, now });
  assert.strictEqual(jobs.length, 2);
  // 09:00 already past (now=10:00 local) → 13:00 then 19:00 Bucharest = 10:00/16:00 UTC
  assert.strictEqual(jobs[0].schedule, "2026-07-03T10:00:00.000Z");
  assert.strictEqual(jobs[1].schedule, "2026-07-03T16:00:00.000Z");
});

test("rolls to next day when all today's slots are past", () => {
  const now = new Date("2026-07-03T20:00:00Z"); // 23:00 Bucharest, all slots past
  const accounts = [{
    account_id: "ro", role: "narator", country: "RO",
    integration_id: "i", slots: "09:00,13:00,19:00", tz: "Europe/Bucharest",
  }];
  const { jobs } = planSchedule({ readyRows: rows, accounts, now });
  assert.strictEqual(jobs[0].schedule, "2026-07-04T06:00:00.000Z"); // tomorrow 09:00 RO
});

test("different countries get their own tz slots + caption suffix", () => {
  const now = new Date("2026-07-03T05:00:00Z");
  const accounts = [
    { account_id: "ro", role: "narator", country: "RO", integration_id: "i_ro",
      slots: "09:00", tz: "Europe/Bucharest", caption_suffix: "#ro" },
    { account_id: "fr", role: "narator", country: "FR", integration_id: "i_fr",
      slots: "09:00", tz: "Europe/Paris", caption_suffix: "#fr" },
  ];
  const { jobs } = planSchedule({ readyRows: rows, accounts, now });
  const ro = jobs.find((j) => j.account_id === "ro");
  const fr = jobs.find((j) => j.account_id === "fr");
  assert.strictEqual(ro.schedule, "2026-07-03T06:00:00.000Z"); // 09:00 RO = 06:00Z
  assert.strictEqual(fr.schedule, "2026-07-03T07:00:00.000Z"); // 09:00 FR = 07:00Z
  assert.ok(ro.caption.endsWith("#ro") && fr.caption.endsWith("#fr"));
});

test("never double-books an already-used slot", () => {
  const now = new Date("2026-07-03T05:00:00Z");
  const accounts = [{ account_id: "ro", role: "narator", country: "RO",
    integration_id: "i", slots: "09:00,13:00", tz: "Europe/Bucharest" }];
  const used = { ro: ["2026-07-03T06:00:00.000Z"] }; // 09:00 RO taken
  const { jobs } = planSchedule({ readyRows: rows, accounts, now, usedSlots: used });
  assert.strictEqual(jobs[0].schedule, "2026-07-03T10:00:00.000Z"); // 13:00 RO
});

test("only maps videos to accounts of the matching role", () => {
  const now = new Date("2026-07-03T05:00:00Z");
  const accounts = [{ account_id: "pov", role: "povestitor", country: "RO",
    integration_id: "i", slots: "09:00", tz: "Europe/Bucharest" }];
  const { jobs } = planSchedule({ readyRows: rows, accounts, now });
  assert.strictEqual(jobs.length, 1);
  assert.strictEqual(jobs[0].video, "https://drive/p1.mp4"); // povestitor only
});

console.log(`\n${passed} passed`);

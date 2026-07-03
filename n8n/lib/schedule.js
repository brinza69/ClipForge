"use strict";
/**
 * Scheduling core for the ClipForge → Postiz poster (Level B).
 *
 * Pure, dependency-free. Mirrors exactly what the n8n "Plan schedule" Code
 * node runs, so it can be unit-tested with plain `node` (see schedule.test.js).
 *
 * Given the "ready" sheet rows, the per-account config (role, country slots,
 * timezone), the current time, and the slots already taken, it assigns each
 * variant video to the NEXT free daily slot of every account mapped to that
 * role — in the account's own timezone. It NEVER double-books a slot and never
 * schedules in the past.
 *
 * It does NOT talk to any network, spoof anything, or touch fingerprints — it
 * only decides "which video goes to which account at which UTC instant".
 */

const ROLES = ["narator", "comentator", "povestitor"];

/** Local wall-clock (y,mo,d,h,mi) in `tz` → the UTC Date for that instant. */
function localToUTC(y, mo, d, h, mi, tz) {
  // Treat the wall-clock as if UTC, then correct by the zone's offset at that
  // moment. One correction pass is exact except within the DST switch hour.
  const guess = Date.UTC(y, mo - 1, d, h, mi, 0);
  const off = tzOffsetMinutes(new Date(guess), tz);
  return new Date(guess - off * 60000);
}

/** Minutes to ADD to UTC to get local time in `tz` at `date` (e.g. +120 CEST). */
function tzOffsetMinutes(date, tz) {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hour12: false,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  const p = {};
  for (const part of dtf.formatToParts(date)) p[part.type] = part.value;
  // 'en-US' can emit hour '24' at midnight — normalize to 0.
  const hour = p.hour === "24" ? 0 : Number(p.hour);
  const asUTC = Date.UTC(+p.year, +p.month - 1, +p.day, hour, +p.minute, +p.second);
  return Math.round((asUTC - date.getTime()) / 60000);
}

/** {year,month,day} of `date` as seen in `tz` (for iterating days locally). */
function ymdInTz(date, tz) {
  const dtf = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
  });
  const [y, mo, d] = dtf.format(date).split("-").map(Number);
  return { y, mo, d };
}

function parseSlots(slots) {
  // Accept "09:00,13:00,19:00" or ["09:00", ...]; sorted ascending.
  const arr = Array.isArray(slots) ? slots : String(slots || "").split(/[,\s]+/);
  return arr
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const [h, m] = s.split(":").map(Number);
      return { h, m, label: s };
    })
    .filter((s) => Number.isInteger(s.h) && Number.isInteger(s.m))
    .sort((a, b) => a.h - b.h || a.m - b.m);
}

/** Videos (URLs) of one role from one ready row. Newline-separated = split parts. */
function videosOfRole(row, role) {
  const raw = row[`${role}_url`] || row.urls?.[role] || "";
  const list = Array.isArray(raw) ? raw : String(raw).split(/\r?\n/);
  return list.map((u) => u.trim()).filter(Boolean);
}

/**
 * @param {object} p
 * @param {Array}  p.readyRows  sheet rows with nr, caption/DESCRIERE, *_url
 * @param {Array}  p.accounts   [{account_id, role, country, integration_id, slots, tz, caption_suffix}]
 * @param {Date}   p.now        current instant
 * @param {object} [p.usedSlots] {account_id: [isoString,...]} already booked
 * @param {number} [p.horizonDays=14] how far ahead to look for a free slot
 * @returns {{jobs: Array, usedSlots: object}}
 */
function planSchedule({ readyRows, accounts, now, usedSlots = {}, horizonDays = 14 }) {
  const used = {};
  for (const k of Object.keys(usedSlots)) used[k] = new Set(usedSlots[k]);

  const jobs = [];
  for (const acc of accounts) {
    if (!ROLES.includes(acc.role)) continue;
    const tz = acc.tz || "UTC";
    const slots = parseSlots(acc.slots);
    if (!slots.length) continue;
    if (!used[acc.account_id]) used[acc.account_id] = new Set();

    for (const row of readyRows) {
      const caption = (row.caption ?? row.DESCRIERE ?? "").toString();
      for (const videoUrl of videosOfRole(row, acc.role)) {
        const slotISO = nextFreeSlot(now, tz, slots, used[acc.account_id], horizonDays);
        if (!slotISO) break; // horizon exhausted for this account
        used[acc.account_id].add(slotISO);
        jobs.push({
          account_id: acc.account_id,
          integration_id: acc.integration_id,
          role: acc.role,
          country: acc.country,
          nr: row.nr ?? row.NR ?? null,
          video: videoUrl,
          caption: acc.caption_suffix ? `${caption} ${acc.caption_suffix}`.trim() : caption,
          schedule: slotISO,
        });
      }
    }
  }

  const out = {};
  for (const k of Object.keys(used)) out[k] = [...used[k]].sort();
  return { jobs, usedSlots: out };
}

function nextFreeSlot(now, tz, slots, usedSet, horizonDays) {
  for (let dayOffset = 0; dayOffset <= horizonDays; dayOffset++) {
    const base = new Date(now.getTime() + dayOffset * 86400000);
    const { y, mo, d } = ymdInTz(base, tz);
    for (const s of slots) {
      const utc = localToUTC(y, mo, d, s.h, s.m, tz);
      if (utc.getTime() <= now.getTime()) continue; // past
      const iso = utc.toISOString();
      if (usedSet.has(iso)) continue; // already booked
      return iso;
    }
  }
  return null;
}

module.exports = { planSchedule, parseSlots, localToUTC, tzOffsetMinutes, ymdInTz, ROLES };

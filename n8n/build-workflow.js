"use strict";
/**
 * Generates `clipforge-postiz-poster.json` (n8n import) from the TESTED
 * scheduling core in lib/schedule.js — so the Code node that runs inside n8n
 * is byte-for-byte the logic covered by schedule.test.js. Run:
 *   node n8n/build-workflow.js
 */
const fs = require("fs");
const path = require("path");

const scheduleSrc = fs
  .readFileSync(path.join(__dirname, "lib", "schedule.js"), "utf8")
  .replace(/module\.exports[\s\S]*$/, ""); // drop the CommonJS export tail

// The Code node: embed the scheduler, then map claimed sheet rows + the
// Accounts tab into planSchedule() and emit one n8n item per post job.
const codeNode = `${scheduleSrc}

// ── n8n glue ────────────────────────────────────────────────────────────────
// Inputs (set via node references in n8n):
//   $('Read ready rows').all()  → the claimed rows (status was 'ready')
//   $('Read Accounts tab').all()→ the account map rows
const rows = $('Read ready rows').all().map(i => i.json);
const accounts = $('Read Accounts tab').all().map(i => ({
  account_id: i.json.account_id,
  role: (i.json.role || '').trim(),
  country: i.json.country,
  integration_id: i.json.postiz_integration_id,
  slots: i.json.slots,
  tz: i.json.timezone || 'UTC',
  caption_suffix: i.json.caption_suffix || '',
}));

// usedSlots: reuse what Postiz already has scheduled if you feed it in; empty =
// this run books from scratch (Postiz itself rejects exact-duplicate times).
const now = new Date();
const { jobs } = planSchedule({ readyRows: rows, accounts, now });

// Optional jitter ±JITTER_MIN so posts aren't on the exact minute across accounts.
const JITTER_MIN = 12;
for (const j of jobs) {
  const d = new Date(j.schedule);
  d.setMinutes(d.getMinutes() + Math.floor((Math.random() * 2 - 1) * JITTER_MIN));
  j.schedule = d.toISOString();
}

return jobs.map(j => ({ json: j }));
`;

// Syntactic self-check of the embedded code (throws on bad JS).
new Function(codeNode.replace(/\$\(/g, "(() => ({all:()=>[]}))().all;(").replace(/return jobs[\s\S]*$/, "return [];"));

const wf = {
  name: "ClipForge → Postiz TikTok poster (scheduled, Level B)",
  nodes: [
    {
      parameters: { rule: { interval: [{ field: "minutes", minutesInterval: 15 }] } },
      id: "cron", name: "Every 15 min",
      type: "n8n-nodes-base.scheduleTrigger", typeVersion: 1.1, position: [220, 300],
    },
    {
      parameters: {
        documentId: { __rl: true, value: "={{ $env.CLIPFORGE_SHEET_ID }}", mode: "id" },
        sheetName: { __rl: true, value: "Sheet1", mode: "name" },
        options: {},
      },
      id: "read_rows", name: "Read ready rows",
      type: "n8n-nodes-base.googleSheets", typeVersion: 4.5, position: [440, 220],
    },
    {
      parameters: {
        documentId: { __rl: true, value: "={{ $env.CLIPFORGE_SHEET_ID }}", mode: "id" },
        sheetName: { __rl: true, value: "Accounts", mode: "name" },
        options: {},
      },
      id: "read_accounts", name: "Read Accounts tab",
      type: "n8n-nodes-base.googleSheets", typeVersion: 4.5, position: [440, 400],
    },
    {
      parameters: {
        conditions: {
          options: { caseSensitive: true, typeValidation: "strict" },
          conditions: [{
            leftValue: "={{ $json.status }}", rightValue: "ready",
            operator: { type: "string", operation: "equals" },
          }],
          combinator: "and",
        },
      },
      id: "filter_ready", name: "Only status = ready",
      type: "n8n-nodes-base.filter", typeVersion: 2, position: [660, 220],
    },
    {
      parameters: { jsCode: codeNode },
      id: "plan", name: "Plan schedule (tested core)",
      type: "n8n-nodes-base.code", typeVersion: 2, position: [900, 300],
    },
    {
      parameters: {
        method: "POST",
        url: "={{ $env.POSTIZ_URL }}/public/v1/posts",
        authentication: "genericCredentialType",
        genericAuthType: "httpHeaderAuth",
        sendBody: true, bodyContentType: "json",
        jsonBody: "={\n"
          + '  "type": "schedule",\n'
          + '  "date": "={{ $json.schedule }}",\n'
          + '  "integrations": [{ "id": "={{ $json.integration_id }}" }],\n'
          + '  "content": [{ "content": "={{ $json.caption }}",\n'
          + '                "media": [{ "url": "={{ $json.video }}" }] }]\n'
          + "}",
        options: { batching: { batch: { batchSize: 1, batchInterval: 90000 } } },
      },
      id: "postiz", name: "Postiz → schedule TikTok",
      type: "n8n-nodes-base.httpRequest", typeVersion: 4.2, position: [1140, 300],
    },
  ],
  connections: {
    "Every 15 min": { main: [[{ node: "Read ready rows", type: "main", index: 0 },
                              { node: "Read Accounts tab", type: "main", index: 0 }]] },
    "Read ready rows": { main: [[{ node: "Only status = ready", type: "main", index: 0 }]] },
    "Only status = ready": { main: [[{ node: "Plan schedule (tested core)", type: "main", index: 0 }]] },
    "Plan schedule (tested core)": { main: [[{ node: "Postiz → schedule TikTok", type: "main", index: 0 }]] },
  },
  settings: { executionOrder: "v1" },
  meta: { generatedBy: "n8n/build-workflow.js", source: "n8n/lib/schedule.js" },
};

const outPath = path.join(__dirname, "clipforge-postiz-poster.json");
fs.writeFileSync(outPath, JSON.stringify(wf, null, 2));
console.log("wrote " + outPath + " (" + wf.nodes.length + " nodes, embedded code " +
  codeNode.length + " chars)");

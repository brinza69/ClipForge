"use client";

/**
 * Parallel from Sheets — same processor as /parallel, but the source URL +
 * filename are pulled from a Google Sheet and the AI-generated description
 * is written back into the row after the run completes.
 *
 * Configure ONCE (spreadsheet, tab, column letters, start row). After that,
 * each run does:
 *   1. Pull next  → reads <url_col><next_row> + <number_col><next_row>
 *   2. Preview → Run  → variants render with filename = <number>{_p1,_p2,…}
 *   3. After job done → backend writes the AI description into the row and
 *      advances next_row. Sheets section refreshes to show the new row.
 */

import { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  FileSpreadsheet, Loader2, Save, Edit3, Trash2, ArrowRight, AlertCircle, SkipForward,
} from "lucide-react";
import { toast } from "sonner";
import { ParallelProcessor } from "@/components/parallel/parallel-processor";

interface SheetsConfig {
  configured: boolean;
  spreadsheet_id?: string;
  spreadsheet_url?: string;
  spreadsheet_title?: string;
  tab?: string;
  col_url?: string;
  col_number?: string;
  col_description?: string;
  start_row?: number;
  next_row?: number;
}

interface PulledRow {
  row: number;
  number: string;
  url: string;
}

type SheetsCommit =
  | { status: "written"; row: number; next_row: number }
  // For "failed" we also stash the description that was supposed to be
  // written, so the Retry button has something to send.
  | { status: "failed"; row: number; reason?: string; description?: string }
  | { status: "skipped_empty_description"; row: number }
  | null;

export default function ParallelSheetsPage() {
  const [url, setUrl] = useState("");
  const [config, setConfig] = useState<SheetsConfig>({ configured: false });
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [pulled, setPulled] = useState<PulledRow | null>(null);
  const [lastCommit, setLastCommit] = useState<SheetsCommit>(null);
  const [retryCommitting, setRetryCommitting] = useState(false);

  // Form state for the configure / edit form
  const [fSpread, setFSpread] = useState("");
  const [fTab, setFTab] = useState("Sheet1");
  const [fColUrl, setFColUrl] = useState("B");
  const [fColNumber, setFColNumber] = useState("A");
  const [fColDesc, setFColDesc] = useState("C");
  const [fStart, setFStart] = useState<number>(2);

  const loadConfig = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/sheets/config`);
      if (!r.ok) throw new Error(`Could not load config (${r.status})`);
      const j: SheetsConfig = await r.json();
      setConfig(j);
      if (!j.configured) setEditing(true);
      else {
        // Hydrate form fields so Edit reuses the saved values
        setFSpread(j.spreadsheet_url || j.spreadsheet_id || "");
        setFTab(j.tab || "Sheet1");
        setFColUrl(j.col_url || "B");
        setFColNumber(j.col_number || "A");
        setFColDesc(j.col_description || "C");
        setFStart(j.start_row || 2);
      }
    } catch (e: any) {
      toast.error("Sheets config failed", { description: e.message });
    }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);

  const saveConfig = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/worker-api/sheets/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          spreadsheet_url: fSpread.trim(),
          tab: fTab.trim(),
          col_url: fColUrl.trim(),
          col_number: fColNumber.trim(),
          col_description: fColDesc.trim(),
          start_row: Number(fStart),
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || `Save failed (${r.status})`);
      }
      const j: SheetsConfig = await r.json();
      setConfig(j);
      setEditing(false);
      toast.success(`Sheets config saved — next row ${j.next_row}`);
    } catch (e: any) {
      toast.error("Save failed", { description: e.message });
    } finally {
      setSaving(false);
    }
  };

  const deleteConfig = async () => {
    if (!window.confirm("Delete Sheets config? You'll need to re-enter everything.")) return;
    try {
      await fetch(`/worker-api/sheets/config`, { method: "DELETE" });
      setConfig({ configured: false });
      setPulled(null);
      setEditing(true);
      toast.success("Sheets config cleared");
    } catch (e: any) {
      toast.error("Could not clear", { description: e.message });
    }
  };

  const pullNext = async () => {
    setPulling(true);
    try {
      const r = await fetch(`/worker-api/sheets/pull-next`, { method: "POST" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `Pull failed (${r.status})`);
      if (j.empty) {
        toast.warning(`Row ${j.row} is empty in the URL column`, {
          description: j.message,
          action: { label: "Skip row", onClick: skipRow },
        });
      } else {
        setPulled({ row: j.row, number: String(j.number || ""), url: j.url });
        setUrl(j.url);
        toast.success(`Pulled row ${j.row}${j.number ? ` (#${j.number})` : ""}`);
      }
    } catch (e: any) {
      toast.error("Pull failed", { description: e.message });
    } finally {
      setPulling(false);
    }
  };

  const skipRow = async () => {
    try {
      const r = await fetch(`/worker-api/sheets/skip-row`, { method: "POST" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `Skip failed (${r.status})`);
      toast.info(`Advanced to row ${j.next_row}`);
      await loadConfig();
    } catch (e: any) {
      toast.error("Skip failed", { description: e.message });
    }
  };

  // After a job completes, inspect the backend's sheets_commit verdict.
  // The auto-commit happens after the pipeline runs, and it CAN fail (token
  // expired between start and end, Sheets API hiccup, rate limit, ...). In
  // that case the videos are on disk and Drive, but the row is NOT written
  // and next_row was NOT advanced — the user has to retry or commit manually.
  // Pre-fix, this failure was silent and the user thought the row was done.
  const handleJobDone = useCallback((data: { raw: Record<string, unknown> }) => {
    const commit = (data.raw?.sheets_commit ?? null) as SheetsCommit;
    if (!commit) {
      // No sheets_row was attached to this run (e.g. user typed URL manually
      // even on this page) — just reset.
      setLastCommit(null);
      setPulled(null);
      setUrl("");
      loadConfig();
      return;
    }
    if (commit.status === "written") {
      setLastCommit(commit);
      toast.success(`Row ${commit.row} written → next: row ${commit.next_row}`);
      setPulled(null);
      setUrl("");
      loadConfig();
    } else if (commit.status === "failed") {
      // Stash the AI description so the inline Retry button has something
      // to send via POST /api/sheets/commit without re-running the pipeline.
      const desc = ((data.raw?.descriptions as Record<string, unknown> | undefined)
        ?.ai_generated as string | undefined) || "";
      setLastCommit({ ...commit, description: desc });
      toast.error(`Sheets commit FAILED for row ${commit.row}`, {
        description:
          (commit.reason || "Unknown reason.") +
          " Videos are saved/uploaded; row is NOT written and next_row was NOT advanced. Use the Retry button on the Sheets card.",
        duration: 15000,
      });
      // Keep `pulled` so the user can also re-run the pipeline on the same row.
    } else if (commit.status === "skipped_empty_description") {
      setLastCommit(commit);
      toast.warning(`Row ${commit.row}: AI description was empty — row not written`, {
        description: "Pipeline succeeded but the description stage produced no text. next_row was NOT advanced.",
        duration: 12000,
      });
    }
  }, [loadConfig]);

  // Retry the Sheets write without re-running the pipeline. Only available
  // when the auto-commit failed AND we have the description text in memory
  // (set by handleJobDone above).
  const retryCommit = async () => {
    if (!lastCommit || lastCommit.status !== "failed" || !lastCommit.description) return;
    setRetryCommitting(true);
    try {
      const r = await fetch(`/worker-api/sheets/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ row: lastCommit.row, description: lastCommit.description }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `Commit failed (${r.status})`);
      toast.success(`Row ${lastCommit.row} written on retry → next: row ${j.next_row}`);
      setLastCommit({ status: "written", row: lastCommit.row, next_row: j.next_row || lastCommit.row + 1 });
      setPulled(null);
      setUrl("");
      await loadConfig();
    } catch (e: any) {
      toast.error("Retry failed", { description: e.message });
    } finally {
      setRetryCommitting(false);
    }
  };

  const startExtras = useCallback(() => {
    if (!pulled) return {};
    return { sheets_row: pulled.row, sheets_number: pulled.number };
  }, [pulled]);

  // Sheets card rendered at the top of the processor body
  const topContent = (
    <Card className="p-4 space-y-3 border-emerald-500/30 bg-emerald-500/[0.03]">
      <div className="flex items-center gap-2">
        <FileSpreadsheet className="h-4 w-4 text-emerald-400" />
        <div className="text-xs uppercase tracking-wider font-semibold text-emerald-400">Google Sheets</div>
        {config.configured && !editing && (
          <div className="ml-auto flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)} title="Edit config">
              <Edit3 className="h-3.5 w-3.5" />
            </Button>
            <Button size="sm" variant="ghost" onClick={deleteConfig} title="Clear config">
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </div>

      {/* Compact summary when configured + not editing */}
      {config.configured && !editing && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
            <SummaryField label="Spreadsheet" value={config.spreadsheet_title || "(unnamed)"} />
            <SummaryField label="Tab" value={config.tab || "—"} />
            <SummaryField label="URL col" value={config.col_url || "—"} mono />
            <SummaryField label="Number col" value={config.col_number || "—"} mono />
            <SummaryField label="Desc col" value={config.col_description || "—"} mono />
            <SummaryField label="Start row" value={String(config.start_row || "—")} mono />
            <div className="col-span-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-2 py-1.5">
              <div className="text-[10px] uppercase text-emerald-400/80">Next row</div>
              <div className="text-sm font-mono text-emerald-300">{config.next_row}</div>
            </div>
          </div>

          {/* Pull + skip controls */}
          <div className="flex items-center gap-2 pt-1">
            {pulled ? (
              <div className="flex flex-1 items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm">
                <Badge variant="outline" className="text-[10px] border-emerald-500/40 text-emerald-300">
                  Row {pulled.row}
                </Badge>
                {pulled.number && (
                  <Badge variant="outline" className="text-[10px] border-emerald-500/40 text-emerald-300 font-mono">
                    #{pulled.number}
                  </Badge>
                )}
                <span className="truncate text-xs text-muted-foreground">{pulled.url}</span>
              </div>
            ) : (
              <div className="flex-1 text-xs text-muted-foreground">
                Click <span className="font-medium text-foreground">Pull next</span> to load row {config.next_row}.
              </div>
            )}
            <Button size="sm" onClick={pullNext} disabled={pulling}>
              {pulling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : (
                <><ArrowRight className="h-3.5 w-3.5 mr-1" /> Pull next</>
              )}
            </Button>
            <Button size="sm" variant="outline" onClick={skipRow} title="Skip current row, advance next_row by 1">
              <SkipForward className="h-3.5 w-3.5" />
            </Button>
          </div>

          {/* Last commit status — persistent indicator after each run, so a
              silent failure (token expired, Sheets API hiccup) can't slip past
              the user. */}
          {lastCommit && (
            <div className={
              "rounded-md border px-3 py-2 text-xs flex items-center gap-2 " +
              (lastCommit.status === "written"
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : lastCommit.status === "failed"
                  ? "border-destructive/40 bg-destructive/10 text-destructive"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-400")
            }>
              {lastCommit.status === "written" && (
                <>
                  <span className="font-medium">✓ Last commit</span>
                  <span>— row {lastCommit.row} written, next: row {lastCommit.next_row}</span>
                </>
              )}
              {lastCommit.status === "failed" && (
                <>
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                  <span className="flex-1 truncate" title={lastCommit.reason || ""}>
                    <span className="font-medium">Commit FAILED</span> — row {lastCommit.row}: {lastCommit.reason || "unknown"}
                  </span>
                  {lastCommit.description ? (
                    <Button size="sm" variant="outline" onClick={retryCommit} disabled={retryCommitting}>
                      {retryCommitting ? <Loader2 className="h-3 w-3 animate-spin" /> : "Retry"}
                    </Button>
                  ) : (
                    <span className="text-[10px] opacity-70">(no desc cached — re-run)</span>
                  )}
                </>
              )}
              {lastCommit.status === "skipped_empty_description" && (
                <>
                  <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                  <span>Row {lastCommit.row} skipped — pipeline ran but produced no AI description</span>
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* Edit form */}
      {editing && (
        <div className="space-y-2">
          <div>
            <Label className="text-[11px] text-muted-foreground">Spreadsheet URL or ID</Label>
            <Input
              value={fSpread}
              onChange={(e) => setFSpread(e.target.value)}
              placeholder="https://docs.google.com/spreadsheets/d/.../edit"
              className="h-8 text-xs font-mono"
            />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            <div>
              <Label className="text-[11px] text-muted-foreground">Tab</Label>
              <Input value={fTab} onChange={(e) => setFTab(e.target.value)} className="h-8 text-sm" />
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">URL col</Label>
              <Input value={fColUrl} onChange={(e) => setFColUrl(e.target.value.toUpperCase())} className="h-8 text-sm font-mono" />
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">Number col</Label>
              <Input value={fColNumber} onChange={(e) => setFColNumber(e.target.value.toUpperCase())} className="h-8 text-sm font-mono" />
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">Desc col</Label>
              <Input value={fColDesc} onChange={(e) => setFColDesc(e.target.value.toUpperCase())} className="h-8 text-sm font-mono" />
            </div>
            <div>
              <Label className="text-[11px] text-muted-foreground">Start row</Label>
              <Input
                type="number"
                min={1}
                value={fStart}
                onChange={(e) => setFStart(Math.max(1, Number(e.target.value) || 1))}
                className="h-8 text-sm font-mono"
              />
            </div>
          </div>
          <div className="flex items-center gap-2 pt-1">
            <Button size="sm" onClick={saveConfig} disabled={saving}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : (
                <><Save className="h-3.5 w-3.5 mr-1" /> Save</>
              )}
            </Button>
            {config.configured && (
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            )}
          </div>
          <p className="text-[10px] text-muted-foreground flex items-start gap-1">
            <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
            Filename on Drive: <code className="font-mono">&lt;number&gt;.mp4</code>
            {" "}(or <code className="font-mono">&lt;number&gt;_p1.mp4</code>, <code className="font-mono">_p2.mp4</code>… when split). The AI-generated description of variant #1 is written into the row's description column after the run.
          </p>
        </div>
      )}
    </Card>
  );

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-500 to-emerald-300">
          <FileSpreadsheet className="h-5 w-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight">Parallel from Sheets</h1>
          <p className="text-sm text-muted-foreground">
            Pull source URL + number from a Google Sheet. Auto-writes the description back when done.
          </p>
        </div>
      </div>

      <ParallelProcessor
        url={url}
        setUrl={setUrl}
        topContent={topContent}
        startPayloadExtras={startExtras}
        onJobDone={handleJobDone}
        runDisabled={!pulled}
        runDisabledReason={
          !config.configured
            ? "Configure Sheets first (top of page)"
            : "Pull a row from Sheets before running"
        }
      />
    </div>
  );
}

function SummaryField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-md border border-border/40 bg-card px-2 py-1.5">
      <div className="text-[10px] uppercase text-muted-foreground">{label}</div>
      <div className={"text-xs truncate " + (mono ? "font-mono" : "")}>{value}</div>
    </div>
  );
}

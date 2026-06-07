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

export default function ParallelSheetsPage() {
  const [url, setUrl] = useState("");
  const [config, setConfig] = useState<SheetsConfig>({ configured: false });
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pulling, setPulling] = useState(false);
  const [pulled, setPulled] = useState<PulledRow | null>(null);

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

  // After a job completes, the backend has already written the description
  // and advanced next_row server-side. Refresh the config to show the new
  // next_row + clear the pulled-row badge so the next click is fresh.
  const handleJobDone = useCallback(() => {
    setPulled(null);
    setUrl("");
    loadConfig();
  }, [loadConfig]);

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

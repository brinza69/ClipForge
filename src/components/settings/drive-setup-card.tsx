"use client";

/**
 * Settings → Google Drive setup card.
 *
 * Two-step setup, end-to-end from this card (no need to drop files in data/):
 *   1. Upload the OAuth Client JSON (Desktop type, downloaded from
 *      Google Cloud Console → Credentials)
 *   2. Click Connect → consents in a popup; the loopback server saves the
 *      token. Status flips to "Connected as <email>".
 *
 * Reset removes both the client JSON and the saved token, forcing a fresh
 * setup (useful when switching Google accounts or rotating the client).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { HardDrive, Loader2, Upload, Trash2, CheckCircle2, Plug } from "lucide-react";
import { toast } from "sonner";

interface Status {
  connected: boolean;
  client_configured: boolean;
  email: string | null;
}

export function DriveSetupCard() {
  const [status, setStatus] = useState<Status>({ connected: false, client_configured: false, email: null });
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(`/worker-api/drive-auth/status`);
      const j: Status = await r.json();
      setStatus(j);
    } catch {}
    finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const uploadClient = async (file: File) => {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`/worker-api/drive-auth/client`, { method: "POST", body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `Upload failed (${r.status})`);
      toast.success("OAuth client saved. Click Connect to authorize your Google account.");
      await refresh();
    } catch (e: any) {
      toast.error("Upload failed", { description: e.message });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const removeClient = async () => {
    if (!window.confirm("Remove the OAuth client and any saved token? You'll need to redo the setup.")) return;
    try {
      const r = await fetch(`/worker-api/drive-auth/client`, { method: "DELETE" });
      if (!r.ok) throw new Error(`Delete failed (${r.status})`);
      toast.success("Drive setup cleared");
      await refresh();
    } catch (e: any) {
      toast.error("Reset failed", { description: e.message });
    }
  };

  const connect = async () => {
    setConnecting(true);
    try {
      const r = await fetch(`/worker-api/drive-auth/connect`, { method: "POST" });
      const j = await r.json();
      if (!r.ok || !j.auth_url) throw new Error(j.detail || "Could not start auth");
      window.open(j.auth_url, "_blank", "noopener");
      toast.info("Complete the Google login in the new tab…");
      const deadline = Date.now() + 180000;
      const poll = setInterval(async () => {
        await refresh();
        if (status.connected || Date.now() > deadline) {
          clearInterval(poll);
          setConnecting(false);
        }
      }, 2000);
      // The interval reads stale `status`. Re-poll inside ourselves until done.
      const pollSelf = setInterval(async () => {
        try {
          const r2 = await fetch(`/worker-api/drive-auth/status`);
          const s2: Status = await r2.json();
          setStatus(s2);
          if (s2.connected || Date.now() > deadline) {
            clearInterval(pollSelf);
            clearInterval(poll);
            setConnecting(false);
            if (s2.connected) toast.success(`Drive connected${s2.email ? ` as ${s2.email}` : ""}`);
          }
        } catch {}
      }, 2000);
    } catch (e: any) {
      setConnecting(false);
      toast.error("Drive connect failed", { description: e.message });
    }
  };

  const disconnect = async () => {
    try { await fetch(`/worker-api/drive-auth/disconnect`, { method: "POST" }); } catch {}
    await refresh();
    toast.success("Drive disconnected (client config kept)");
  };

  return (
    <Card className="border-border/30 bg-card/50 p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <HardDrive className="h-4 w-4 text-primary" /> Google Drive
        </h3>
        <p className="text-[11px] text-muted-foreground max-w-md text-right">
          Personal account OAuth — uploads use YOUR 15 GB quota. Service-account keys fail with 0 GB on My Drive.
        </p>
      </div>

      {/* Step 1 — OAuth client JSON */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">1. OAuth Client</span>
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
          ) : status.client_configured ? (
            <Badge variant="outline" className="text-[10px] border-emerald-500/40 text-emerald-400">
              <CheckCircle2 className="h-3 w-3 mr-1" /> Saved
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">Missing</Badge>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          In Google Cloud Console → APIs &amp; Services → Credentials → <b>Create Credentials</b> → <b>OAuth client ID</b> →
          Application type <b>Desktop app</b>. After creating, click <b>Download JSON</b> and upload the file here.
        </p>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) uploadClient(f);
            }}
          />
          <Button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            variant={status.client_configured ? "outline" : "default"}
            size="sm"
          >
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : (
              <><Upload className="h-3.5 w-3.5 mr-1" /> {status.client_configured ? "Replace client JSON" : "Upload client JSON"}</>
            )}
          </Button>
          {status.client_configured && (
            <Button onClick={removeClient} variant="outline" size="sm" title="Remove client + token">
              <Trash2 className="h-3.5 w-3.5 mr-1" /> Reset
            </Button>
          )}
        </div>
      </div>

      {/* Step 2 — Connect / consent */}
      <div className="space-y-2 border-t border-border/30 pt-4">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">2. Connect account</span>
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
          ) : status.connected ? (
            <Badge variant="outline" className="text-[10px] border-emerald-500/40 text-emerald-400">
              <CheckCircle2 className="h-3 w-3 mr-1" /> Connected{status.email ? ` · ${status.email}` : ""}
            </Badge>
          ) : status.client_configured ? (
            <Badge variant="outline" className="text-[10px] border-muted-foreground/40 text-muted-foreground">Not connected</Badge>
          ) : (
            <Badge variant="outline" className="text-[10px] border-muted-foreground/40 text-muted-foreground">Upload client first</Badge>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          Opens a Google consent page in a new tab. The popup must include <b>Drive (drive.file)</b> AND <b>Sheets (spreadsheets)</b> scopes — needed for the Sheets integration. If you only see Drive, the OAuth consent screen in Cloud Console is missing the Sheets scope.
        </p>
        <div className="flex gap-2">
          {status.connected ? (
            <Button onClick={disconnect} variant="outline" size="sm">
              <Plug className="h-3.5 w-3.5 mr-1" /> Disconnect
            </Button>
          ) : (
            <Button onClick={connect} disabled={connecting || !status.client_configured} size="sm">
              {connecting ? <Loader2 className="h-4 w-4 animate-spin" /> : (
                <><Plug className="h-3.5 w-3.5 mr-1" /> Connect Google Drive</>
              )}
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

"use client";

/**
 * Drive connection status card. Used by both /parallel and /parallel-sheets.
 * Owns its own status state — polls /api/drive-auth/status on mount and
 * during the consent flow.
 */

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { HardDrive, Loader2 } from "lucide-react";
import { toast } from "sonner";

interface Status {
  connected: boolean;
  client_configured: boolean;
  email: string | null;
}

export function DriveCard() {
  const [drive, setDrive] = useState<Status>({ connected: false, client_configured: false, email: null });
  const [connecting, setConnecting] = useState(false);

  const refresh = async () => {
    try { const j = await (await fetch(`/worker-api/drive-auth/status`)).json(); setDrive(j); return j; } catch { return null; }
  };

  useEffect(() => { refresh(); }, []);

  const connect = async () => {
    setConnecting(true);
    try {
      const r = await fetch(`/worker-api/drive-auth/connect`, { method: "POST" });
      const j = await r.json();
      if (!r.ok || !j.auth_url) throw new Error(j.detail || "Could not start Drive auth");
      window.open(j.auth_url, "_blank", "noopener");
      toast.info("Complete the Google login in the new tab…");
      const deadline = Date.now() + 180000;
      const poll = setInterval(async () => {
        const s = await refresh();
        if ((s && s.connected) || Date.now() > deadline) {
          clearInterval(poll);
          setConnecting(false);
          if (s && s.connected) toast.success(`Drive connected${s.email ? ` as ${s.email}` : ""}`);
        }
      }, 2000);
    } catch (e: any) {
      setConnecting(false);
      toast.error("Drive connect failed", { description: e.message });
    }
  };

  const disconnect = async () => {
    try { await fetch(`/worker-api/drive-auth/disconnect`, { method: "POST" }); } catch {}
    refresh();
    toast.success("Drive disconnected");
  };

  return (
    <Card className="p-4 border-border/40">
      <div className="flex items-center gap-3">
        <HardDrive className="h-4 w-4 text-muted-foreground shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium">Google Drive</div>
          <div className="text-[11px] text-muted-foreground">
            {drive.connected
              ? `Connected${drive.email ? ` as ${drive.email}` : ""} — variant uploads go to your Drive.`
              : drive.client_configured
                ? "Not connected. Connect to enable auto-upload of finished videos."
                : "OAuth client not set up. Place data/drive_oauth_client.json first (see chat)."}
          </div>
        </div>
        {drive.connected ? (
          <Button size="sm" variant="outline" onClick={disconnect}>Disconnect</Button>
        ) : (
          <Button size="sm" onClick={connect} disabled={connecting || !drive.client_configured}>
            {connecting ? <Loader2 className="h-4 w-4 animate-spin" /> : "Connect Google Drive"}
          </Button>
        )}
      </div>
    </Card>
  );
}

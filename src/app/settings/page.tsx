"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Zap, HardDrive, Cpu, Monitor, Info,
} from "lucide-react";

export default function SettingsPage() {
  const { data: system } = useQuery({
    queryKey: ["system"],
    queryFn: api.system,
    retry: false,
  });

  return (
    <div className="space-y-8 max-w-3xl">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="mt-1 text-muted-foreground">
          System configuration and preferences.
        </p>
      </div>

      {/* System Info */}
      <Card className="border-border/30 bg-card/50 p-6 space-y-4">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Cpu className="h-4 w-4 text-primary" /> System Information
        </h3>
        <div className="grid gap-3 sm:grid-cols-2 text-sm">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">GPU</p>
            <p className="font-medium flex items-center gap-2">
              {system?.gpu_available ? (
                <>
                  <Zap className="h-3.5 w-3.5 text-amber-400" />
                  {system.gpu_name}
                </>
              ) : (
                <span className="text-muted-foreground">CPU Mode</span>
              )}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Whisper Model</p>
            <p className="font-medium">{system?.whisper_model || "base"}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Data Directory</p>
            <p className="font-mono text-xs truncate">{system?.data_dir || "-"}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Exports Directory</p>
            <p className="font-mono text-xs truncate">{system?.exports_dir || "-"}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Disk Free</p>
            <p className="font-medium">{system?.disk_free_gb ?? "-"} GB</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Disk Total</p>
            <p className="font-medium">{system?.disk_total_gb ?? "-"} GB</p>
          </div>
        </div>
      </Card>

      {/* Export Defaults */}
      <Card className="border-border/30 bg-card/50 p-6 space-y-4">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Monitor className="h-4 w-4 text-primary" /> Export Defaults
        </h3>
        <div className="grid gap-3 sm:grid-cols-2 text-sm">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Resolution</p>
            <p className="font-medium">1080 × 1920 (9:16)</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Codec</p>
            <p className="font-medium">H.264 / AAC</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Bitrate</p>
            <p className="font-medium">8M video / 192k audio</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Target Clip Length</p>
            <p className="font-medium">60 – 120 seconds</p>
          </div>
        </div>
      </Card>

      {/* Legal Note */}
      <Card className="border-border/30 bg-card/50 p-6 space-y-3">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Info className="h-4 w-4 text-muted-foreground" /> Usage Notice
        </h3>
        <p className="text-xs text-muted-foreground leading-relaxed">
          ClipForge is a local tool for personal use. You are responsible for
          ensuring you have the rights to download, process, and redistribute
          any content you use with this application. This tool does not host,
          distribute, or claim ownership over any media content.
        </p>
      </Card>

      {/* Version */}
      <div className="text-center text-xs text-muted-foreground pt-4">
        ClipForge v0.1.0 • Local AI Clipping Studio
      </div>
    </div>
  );
}

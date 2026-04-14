"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatDuration, formatBytes, getScoreColor } from "@/lib/constants";
import { toast } from "sonner";
import {
  FolderOpen, HardDrive, Trash2, Download, Play,
  Check, Film, Sparkles, RefreshCw, AlertCircle,
} from "lucide-react";
import { motion } from "framer-motion";

const WORKER_BASE_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

function getExportUrl(exportPath: string) {
  if (!exportPath) return "";
  const parts = exportPath.replace(/\\/g, "/").split("/exports/");
  return parts.length > 1 ? `${WORKER_BASE_URL}/exports/${parts[1]}` : "";
}

export default function ExportsPage() {
  const queryClient = useQueryClient();

  const { data: exports, isLoading } = useQuery({
    queryKey: ["exports"],
    queryFn: api.exports.list,
    refetchInterval: 5000,
  });

  const { data: storage } = useQuery({
    queryKey: ["storage"],
    queryFn: api.exports.storage,
    refetchInterval: 15000,
  });

  const cleanupMutation = useMutation({
    mutationFn: (target: string) => api.exports.cleanup(target),
    onSuccess: (data) => {
      toast.success(`Cleaned ${data.cleaned_bytes > 0 ? formatBytes(data.cleaned_bytes) : "0 B"}`);
      queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
  });

  const reexportMutation = useMutation({
    mutationFn: (clipId: string) => api.clips.export(clipId),
    onSuccess: (_data, clipId) => {
      toast.success("Re-export queued", { description: `Clip ${clipId.slice(0, 8)} will be re-processed.` });
      queryClient.invalidateQueries({ queryKey: ["exports"] });
    },
    onError: (err: Error) => toast.error("Re-export failed", { description: err.message }),
  });

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Exports & Storage</h1>
        <p className="mt-1 text-muted-foreground">
          Manage exported clips and monitor storage usage.
        </p>
      </div>

      {/* Storage Overview */}
      {storage && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { label: "Media Files", value: storage.media_size, icon: Film, color: "text-blue-400" },
            { label: "Exports", value: storage.exports_size, icon: Download, color: "text-emerald-400" },
            { label: "Cache", value: storage.cache_size, icon: HardDrive, color: "text-amber-400" },
            {
              label: "Disk Free",
              value: storage.disk_free,
              icon: HardDrive,
              color: storage.disk_free < 10 * 1024 * 1024 * 1024 ? "text-red-400" : "text-slate-400",
            },
          ].map(({ label, value, icon: Icon, color }) => (
            <Card key={label} className="border-border/30 bg-card/50 p-4">
              <div className="flex items-center gap-3">
                <Icon className={`h-5 w-5 ${color}`} />
                <div>
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="text-lg font-bold">{formatBytes(value)}</p>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Cleanup Actions */}
      <Card className="border-border/30 bg-card/50 p-5">
        <h3 className="text-sm font-semibold mb-3">Storage Cleanup</h3>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={() => cleanupMutation.mutate("temp")}
          >
            <Trash2 className="h-3.5 w-3.5" /> Clean Temp Files
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={() => cleanupMutation.mutate("cache")}
          >
            <Trash2 className="h-3.5 w-3.5" /> Clean Cache
          </Button>
        </div>
      </Card>

      {/* Exports List */}
      <div>
        <h2 className="text-lg font-semibold mb-4">Exported Clips</h2>
        {!exports || exports.length === 0 ? (
          <Card className="flex min-h-[200px] items-center justify-center border-dashed border-border/40 bg-card/30">
            <div className="text-center">
              <Download className="mx-auto h-10 w-10 text-muted-foreground/30 mb-3" />
              <p className="text-muted-foreground text-sm">No exported clips yet</p>
            </div>
          </Card>
        ) : (
          <div className="space-y-2">
            {exports.map((exp: any) => (
              <Card
                key={exp.clip_id}
                className={`flex items-center gap-4 border-border/30 p-4 transition-colors ${exp.file_exists ? "bg-card/50 hover:bg-card/70" : "bg-red-500/5 border-red-500/20"}`}
              >
                <div className={`flex h-10 w-10 items-center justify-center rounded-lg shrink-0 ${exp.file_exists ? "bg-emerald-500/10" : "bg-red-500/10"}`}>
                  {exp.file_exists
                    ? <Check className="h-5 w-5 text-emerald-400" />
                    : <AlertCircle className="h-5 w-5 text-red-400" />}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{exp.title}</p>
                  <div className="flex items-center gap-3 mt-0.5 text-xs text-muted-foreground">
                    <span>{formatDuration(exp.duration)}</span>
                    {exp.file_size > 0 && <span>{formatBytes(exp.file_size)}</span>}
                    <span className={getScoreColor(exp.momentum_score)}>
                      Score: {Math.round(exp.momentum_score)}
                    </span>
                    {!exp.file_exists && <span className="text-red-400">File deleted from disk</span>}
                  </div>
                </div>
                {exp.file_exists ? (
                  <div className="flex items-center gap-1.5">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 gap-1.5 text-xs text-muted-foreground hover:text-primary"
                      onClick={() => window.open(`/worker-api/exports/${exp.clip_id}/download`, "_blank")}
                    >
                      <Play className="h-3 w-3" /> Play
                    </Button>
                    <a href={`/worker-api/exports/${exp.clip_id}/download`}>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 gap-1.5 text-xs text-muted-foreground hover:text-primary"
                      >
                        <Download className="h-3 w-3" /> Download
                      </Button>
                    </a>
                    <Badge variant="outline" className="text-emerald-400 border-emerald-400/30">Available</Badge>
                  </div>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 gap-1.5 text-xs border-amber-500/40 text-amber-400 hover:bg-amber-500/10"
                    disabled={reexportMutation.isPending}
                    onClick={() => reexportMutation.mutate(exp.clip_id)}
                  >
                    <RefreshCw className={`h-3 w-3 ${reexportMutation.isPending ? "animate-spin" : ""}`} />
                    Re-export
                  </Button>
                )}
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

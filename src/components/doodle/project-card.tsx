"use client";

import Link from "next/link";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Trash2, ImageIcon, AlertTriangle } from "lucide-react";
import type { DoodleProjectSummary } from "@/types/doodle";
import { STATUS_LABELS, STATUS_BADGE_CLASS, nicheLabel } from "@/components/doodle/constants";

interface Props {
  project: DoodleProjectSummary;
  onDelete: (id: string) => void;
}

export function ProjectCard({ project, onDelete }: Props) {
  return (
    <Card className="p-4 space-y-3 border-border/40 bg-card/60 hover:border-primary/40 transition-colors">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/doodle/${project.id}`} className="font-semibold text-sm hover:text-primary transition-colors truncate block">
            {project.title || project.topic || "Untitled"}
          </Link>
          <p className="text-[11px] text-muted-foreground mt-0.5">{nicheLabel(project.niche)}</p>
        </div>
        <Badge variant="outline" className={STATUS_BADGE_CLASS[project.status] || ""}>
          {STATUS_LABELS[project.status] || project.status}
        </Badge>
      </div>

      <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
        <span>{project.scene_count} scenes</span>
        <span className="flex items-center gap-1">
          <ImageIcon className="h-3 w-3" /> {project.images_uploaded}/{project.scene_count}
        </span>
        {project.missing_images > 0 && (
          <span className="flex items-center gap-1 text-amber-400">
            <AlertTriangle className="h-3 w-3" /> {project.missing_images} missing
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Link href={`/doodle/${project.id}`} className="flex-1">
          <Button variant="outline" size="sm" className="w-full">Open</Button>
        </Link>
        <Button variant="ghost" size="icon-sm" onClick={() => onDelete(project.id)} title="Delete project">
          <Trash2 className="h-3.5 w-3.5 text-destructive" />
        </Button>
      </div>
    </Card>
  );
}

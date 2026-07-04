"use client";

import { Card } from "@/components/ui/card";
import { SceneRow } from "@/components/doodle/scene-row";
import type { DoodleScene } from "@/types/doodle";

interface Props {
  projectId: string;
  scenes: DoodleScene[];
  onImageUploaded: (index: number, scene: DoodleScene) => void;
  onImageRemoved: (index: number) => void;
  onReorder: (order: number[]) => void;
}

export function StoryboardTable({ projectId, scenes, onImageUploaded, onImageRemoved, onReorder }: Props) {
  const handleMove = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= scenes.length) return;
    const order = scenes.map((s) => s.index);
    const tmp = order[index];
    order[index] = order[target];
    order[target] = tmp;
    onReorder(order);
  };

  return (
    <Card className="p-4 space-y-3 border-border/40">
      <div className="text-xs uppercase tracking-wider text-muted-foreground font-semibold">
        Storyboard ({scenes.length} scenes)
      </div>
      <div className="space-y-2">
        {scenes.map((scene, i) => (
          <SceneRow
            key={scene.index}
            projectId={projectId}
            scene={scene}
            isFirst={i === 0}
            isLast={i === scenes.length - 1}
            onImageUploaded={onImageUploaded}
            onImageRemoved={onImageRemoved}
            onMove={handleMove}
          />
        ))}
        {scenes.length === 0 && (
          <p className="text-sm text-muted-foreground">No scenes yet — script generation may still be running.</p>
        )}
      </div>
    </Card>
  );
}

// ClipForge — API Client
// Communicates with the Python worker backend on port 8420.

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || "http://localhost:8420";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${WORKER_URL}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(body.detail || res.statusText, res.status);
  }

  return res.json();
}

// ============================================================================
// Projects
// ============================================================================

import type {
  Project, ProjectMetadata, Clip, Job, Transcript,
  StorageInfo, SystemInfo,
} from "@/types";

export const api = {
  // Health
  health: () => request<{ status: string }>("/api/health"),
  system: () => request<SystemInfo>("/api/system"),

  // Projects
  projects: {
    list: () => request<Project[]>("/api/projects/"),
    get: (id: string) => request<Project>(`/api/projects/${id}`),
    create: (data: { source_url?: string; title?: string; processing_mode?: "clipping" | "full_video_parts" }) =>
      request<Project>("/api/projects/", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    update: (id: string, data: { processing_mode?: "clipping" | "full_video_parts"; title?: string }) =>
      request<Project>(`/api/projects/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    delete: (id: string) =>
      request<{ deleted: string }>(`/api/projects/${id}`, { method: "DELETE" }),
    metadata: (id: string) => request<ProjectMetadata>(`/api/projects/${id}/metadata`),
    action: (id: string, action: string, formatId?: string) =>
      request<{ job_id?: string; action: string; status: string }>(
        `/api/projects/${id}/action`,
        {
          method: "POST",
          body: JSON.stringify({ action, format_id: formatId }),
        },
      ),
  },

  // Jobs
  jobs: {
    list: (params?: { project_id?: string; status?: string }) => {
      const qs = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params || {}).filter(([, v]) => v != null),
        ) as Record<string, string>,
      ).toString();
      return request<Job[]>(`/api/jobs/?${qs}`);
    },
    get: (id: string) => request<Job>(`/api/jobs/${id}`),
    cancel: (id: string) =>
      request<{ cancelled: string }>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  },

  // Clips
  clips: {
    list: (projectId: string) => request<Clip[]>(`/api/clips/?project_id=${projectId}`),
    get: (id: string) => request<Clip>(`/api/clips/${id}`),
    update: (id: string, data: Partial<Clip>) =>
      request<Clip>(`/api/clips/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    export: (id: string) =>
      request<{ job_id: string; clip_id: string }>(`/api/clips/${id}/export`, {
        method: "POST",
      }),
    approve: (id: string) =>
      request<{ clip_id: string }>(`/api/clips/${id}/approve`, { method: "POST" }),
    reject: (id: string) =>
      request<{ clip_id: string }>(`/api/clips/${id}/reject`, { method: "POST" }),
    transcript: (projectId: string) =>
      request<Transcript>(`/api/clips/project/${projectId}/transcript`),
  },

  // Campaigns
  campaigns: {
    list: (params?: { min_budget_pct?: number; platform?: string; min_priority?: number }) => {
      const qs = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params || {}).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]),
        ),
      ).toString();
      return request<any[]>(`/api/campaigns?${qs}`);
    },
    discover: (sources?: string[]) =>
      request<{ discovered: number; campaigns: any[] }>("/api/campaigns/discover", {
        method: "POST",
        body: JSON.stringify(sources ? { sources } : {}),
      }),
    add: (data: any) =>
      request<any>("/api/campaigns/add", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    guidance: (data: { campaign_id?: string; clip_title?: string; clip_hook?: string; category?: string }) =>
      request<any>("/api/campaigns/guidance", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    logPerformance: (data: { campaign_id: string; clip_id: string; platform?: string; views?: number; approved?: boolean; payout?: number }) =>
      request<{ status: string }>("/api/campaigns/performance", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    stats: () => request<any>("/api/campaigns/stats"),
    categories: () => request<any[]>("/api/campaigns/categories"),
    detectCategory: (data: { title?: string; description?: string; channel_name?: string; duration?: number }) =>
      request<{ detected_category: string; config: any }>("/api/campaigns/detect-category", {
        method: "POST",
        body: JSON.stringify(data),
      }),
  },

  // Exports / Storage
  exports: {
    list: () => request<any[]>("/api/exports/"),
    storage: () => request<StorageInfo>("/api/exports/storage"),
    cleanup: (target: string) =>
      request<{ target: string; cleaned_bytes: number }>("/api/exports/cleanup", {
        method: "POST",
        body: JSON.stringify({ target }),
      }),
    folder: () => request<{ path: string }>("/api/exports/open-folder"),
  },
};

// Worker URL for static assets (thumbnails)
export const THUMBNAIL_URL = (path: string) => {
  if (!path) return "";
  // Convert absolute path to relative URL via worker
  const parts = path.replace(/\\/g, "/").split("/thumbnails/");
  if (parts.length > 1) {
    return `${WORKER_URL}/thumbnails/${parts[1]}`;
  }
  return "";
};

// Build a playable video URL from the project's video_path field.
// Falls back to the legacy /media/{id}/video.mp4 pattern.
export const VIDEO_URL = (projectId: string, videoPath?: string | null) => {
  if (videoPath) {
    const parts = videoPath.replace(/\\/g, "/").split("/media/");
    if (parts.length > 1) {
      return `${WORKER_URL}/media/${parts[parts.length - 1]}`;
    }
  }
  // Fallback
  return `${WORKER_URL}/media/${projectId}/video.mp4`;
};

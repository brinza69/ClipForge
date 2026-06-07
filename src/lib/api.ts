// ClipForge — Minimal API client.
//
// The legacy clip-flow (projects, clips, campaigns, exports) was removed in
// S2.9. Everything that survives here is what other code STILL imports —
// today just `api.system` for the /settings page. Other features call the
// backend directly with fetch() through the /worker-api/* proxy.

import type { SystemInfo } from "@/types";

async function request<T>(path: string): Promise<T> {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error((body as { detail?: string }).detail || r.statusText);
  }
  return r.json();
}

export const api = {
  // Routes through Next.js /worker-api/* rewrite (see next.config.ts) so we
  // get the same-origin proxy treatment everything else uses.
  system: () => request<SystemInfo>("/worker-api/system"),
};

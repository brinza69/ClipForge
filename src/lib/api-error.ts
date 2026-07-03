// Extracts a human-readable message from a failed backend response.
//
// The doodle backend returns structured errors: {detail: {error, message,
// details}}. Older endpoints return {detail: "string"}. The Next.js proxy
// returns a plain-text "Internal Server Error" when the backend is not
// reachable at WORKER_URL_INTERNAL (dead port → ECONNREFUSED → opaque 500).
//
// Always logs the full payload to the console so the real error is never
// hidden during development.

export interface ApiError {
  message: string;
  code?: string;
  details?: string;
}

export async function readApiError(r: Response, fallback: string): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await r.clone().json();
  } catch {
    try {
      body = await r.text();
    } catch {
      body = null;
    }
  }

  // eslint-disable-next-line no-console
  console.error(`[api] ${r.status} ${r.url}`, body);

  const detail = (body as { detail?: unknown } | null)?.detail;

  if (detail && typeof detail === "object") {
    const d = detail as { error?: string; message?: string; details?: string };
    return {
      message: d.message || fallback,
      code: d.error,
      details: d.details ?? undefined,
    };
  }
  if (typeof detail === "string" && detail.trim()) {
    return { message: detail };
  }
  if (typeof body === "string" && body.trim().startsWith("Internal Server Error")) {
    return {
      message:
        "Backend not reachable through the dev proxy. Is the FastAPI worker running " +
        "on the port set in .env.local (WORKER_URL_INTERNAL)?",
      code: "PROXY_UNREACHABLE",
      details: `${r.status} from ${r.url}`,
    };
  }
  return { message: `${fallback} (${r.status})` };
}

/** Toast description with the debug details appended when present. */
export function errorDescription(e: ApiError): string {
  return e.details ? `${e.message} — ${e.details}` : e.message;
}

// Consistent toast wording across the app. Before this, error toasts were a
// mix of "Save failed", "FAILED", "Could not connect", "Pull failed"…
//
// Usage:
//   import { errorToast, okToast } from "@/lib/toast-helpers";
//   try { ... } catch (e) { errorToast.api("save the preset", e); }
//   okToast.saved("Preset");

import { toast } from "sonner";

function reasonOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Unknown error";
}

export const errorToast = {
  /**
   * Standard error: "Couldn't <operation>" with the reason as description.
   * `operation` is a verb phrase: "save the preset", "load voices",
   * "connect Google Drive".
   */
  api(operation: string, error: unknown, opts?: { duration?: number }) {
    toast.error(`Couldn't ${operation}`, {
      description: reasonOf(error),
      duration: opts?.duration,
    });
  },
};

export const okToast = {
  saved: (thing: string) => toast.success(`${thing} saved`),
  deleted: (thing: string) => toast.success(`${thing} deleted`),
  copied: (thing: string) => toast.success(`${thing} copied to clipboard`),
};

// ClipForge — Frontend type definitions.
//
// The legacy clip-flow types (Project, Clip, Campaign, Transcript,
// StorageInfo, ...) belonged to the removed S2.9 pipeline and were pruned.
// Only types used by current frontend code remain. Add new ones here as
// features land — keep this file focused, not a kitchen sink.

export interface SystemInfo {
  gpu_available: boolean;
  gpu_name: string | null;
  whisper_model: string;
  whisper_device: string;
  data_dir: string;
  exports_dir: string;
  disk_free_gb: number;
  disk_total_gb: number;
}

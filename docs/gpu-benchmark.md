# ClipForge — GPU Benchmark Reference

> Purpose: baseline numbers to compare GPU performance after swapping cards.
> The erase (LaMa inpaint) stage is the pure GPU-bound stage — compare that.

## How to reproduce (run the SAME video after the swap)

- **Reference video:** `https://vm.tiktok.com/ZNR74hmxf/` (TikTok, 20s, 1080×1920 @ 60fps)
- Run it through **Parallel Processing** (same 3-variant config) — or any flow
  that hits the erase stage.
- The erase numbers are logged as:
  ```
  [clipforge.inpaint] INFO: Inpaint start: ... N frames, ... LaMa(GPU)
  [clipforge.inpaint] INFO: Inpaint done: N frames, ... KB
  ```
  Subtract the two timestamps → seconds. Then ms/frame = seconds × 1000 / N.
- ⚠️ `dev.sh` truncates `logs/backend.log` on every restart (`>`), so read the
  inpaint lines BEFORE restarting, or switch the redirect to `>>` to keep history.

---

## Baseline — RTX 3060 (current, 2026-06-03)

| Field | Value |
|---|---|
| GPU | **NVIDIA GeForce RTX 3060** |
| VRAM | 12 GB (12288 MiB) |
| Driver | 561.09 (NVENC blocked — needs 570+; encoder = libx264:ultrafast) |
| LaMa batch | 16 |

### Erase (LaMa inpaint) — the GPU-bound stage

| Metric | RTX 3060 |
|---|---|
| Resolution / fps | 1080×1920 @ 60fps |
| Frames | 1243 |
| Inpaint wall time | 83 s (16:50:43 → 16:52:06) |
| **Throughput** | **~15.0 frames/s** |
| **Per frame** | **~66.8 ms/frame** |

> Note: the 83 s includes the concurrent libx264 encode, not just the neural
> inpaint — keep encoder settings identical (`libx264:ultrafast`) when comparing.

### Full parallel run context (for reference)

| Field | Value |
|---|---|
| Job id | `1a4774671d70` |
| Source | 20s TikTok (`ZNR74hmxf`), 1080×1920 @ 60fps |
| Variants | 3 |
| Total wall time | 7m 30s |
| Stage split | download ~6s · transcribe ~65s · **erase ~83s** · clean ~9s · per-variant ~94s avg |

### Other historical GPU figure (old card, from session-handover.md)

- **RTX 2080 Super** (old card, 8 GB): rembg / U²-Net commentator AI ≈ **87 ms/frame**
  (different model than LaMa inpaint — not directly comparable to the erase numbers above).

---

## Baseline — RTX 2080 Super (2026-06-03, same 1243-frame video)

| Field | Value |
|---|---|
| GPU | **NVIDIA GeForce RTX 2080 SUPER** |
| VRAM | 8 GB (8192 MiB) |
| Driver | 561.09 (NVENC blocked — libx264:ultrafast) |
| Frames | 1243 (identical video, ZNR74hmxf) |
| Inpaint wall time | **67 s** (18:04:01 → 18:05:08) |
| Throughput | **~18.6 frames/s** |
| Per frame | **~53.9 ms/frame** |

## Head-to-head — erase (LaMa inpaint), identical 1243-frame video

| | RTX 2080 Super | RTX 3060 |
|---|---|---|
| Inpaint time | **67 s** | 83 s |
| frames/s | **18.6** | 15.0 |
| ms/frame | **53.9** | 66.8 |

**RTX 2080 Super is ~1.24× faster per frame (~24%)** — 16 s quicker on the erase
stage for this clip. The 2080 Super (more CUDA cores + memory bandwidth) beats
the 3060 (Ampere, 12 GB) on raw inpaint throughput. The 3060's only edge is
VRAM headroom (12 GB vs 8 GB). For ClipForge's erase workload, the 2080 Super
is the better card unless you hit the 8 GB limit.

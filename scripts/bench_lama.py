"""
LaMa inpaint micro-benchmark.

Measures LaMa-only forward-pass throughput on a synthetic batch of ROIs
that mimics a typical caption-erasure workload (e.g. a 9:16 vertical
clip with a caption band roughly 1080x256 padded to multiple-of-8 or 16).

Usage:
    python scripts/bench_lama.py [--batch N] [--iters N] [--fp16] [--compile] [--cudnn-bench] [--roi WxH]
"""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch


def parse_size(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--fp16", action="store_true")
    ap.add_argument("--compile", action="store_true")
    ap.add_argument("--cudnn-bench", action="store_true")
    ap.add_argument("--channels-last", action="store_true")
    ap.add_argument("--roi", type=str, default="1088x272",
                    help="WxH (must be multiple of 8). Default ~caption band on 1080p vertical.")
    args = ap.parse_args()

    if args.cudnn_bench:
        torch.backends.cudnn.benchmark = True
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    from simple_lama_inpainting import SimpleLama
    lama = SimpleLama()
    model = lama.model
    device = lama.device

    if args.fp16:
        model = model.half()

    if args.channels_last:
        model = model.to(memory_format=torch.channels_last)

    if args.compile:
        # reduce-overhead = CUDA-graph capture path (fastest for static shapes)
        model = torch.compile(model, mode="reduce-overhead", fullgraph=False)

    W, H = parse_size(args.roi)
    B = args.batch

    img = torch.rand(B, 3, H, W, device=device)
    mask = torch.zeros(B, 1, H, W, device=device)
    mask[:, :, H // 4 : H * 3 // 4, W // 8 : W * 7 // 8] = 1.0
    if args.fp16:
        img = img.half()
        mask = mask.half()
    if args.channels_last:
        img = img.contiguous(memory_format=torch.channels_last)
        mask = mask.contiguous(memory_format=torch.channels_last)

    # Warm-up
    for _ in range(args.warmup):
        with torch.inference_mode():
            _ = model(img, mask)
    torch.cuda.synchronize()

    t0 = time.perf_counter()
    for _ in range(args.iters):
        with torch.inference_mode():
            _ = model(img, mask)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0

    frames = args.iters * B
    print(f"\n  roi          : {W}x{H}")
    print(f"  batch        : {B}")
    print(f"  iters        : {args.iters}")
    print(f"  fp16         : {args.fp16}")
    print(f"  compile      : {args.compile}")
    print(f"  cudnn.bench  : {args.cudnn_bench}")
    print(f"  channels_last: {args.channels_last}")
    print(f"  total time   : {dt:.3f} s")
    print(f"  per batch    : {dt / args.iters * 1000:.1f} ms")
    print(f"  per frame    : {dt / frames * 1000:.2f} ms")
    print(f"  throughput   : {frames / dt:.1f} fps")
    mem_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
    print(f"  peak VRAM    : {mem_mb:.0f} MB")


if __name__ == "__main__":
    main()

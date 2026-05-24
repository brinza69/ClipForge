"""Profile each stage of the inpaint pipeline to find the real bottleneck."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import services.inpaint as inp  # noqa: E402

# Instrument: monkey-patch torch ops
_stages = {"decode": 0.0, "preprocess": 0.0, "lama": 0.0, "postprocess": 0.0, "encode_write": 0.0}


# Wrap the model forward
def main():
    import torch
    from simple_lama_inpainting import SimpleLama
    lama = inp._try_load_lama()
    orig_forward = lama.model.forward

    def timed_forward(*a, **kw):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        out = orig_forward(*a, **kw)
        torch.cuda.synchronize()
        _stages["lama"] += time.perf_counter() - t0
        return out

    lama.model.forward = timed_forward

    input_path = "data/exports/7a71777cadc3/clip_bbf92df6e00b.mp4"
    output_path = "/tmp/erased_profile.mp4"

    t0 = time.perf_counter()
    asyncio.run(inp.inpaint_region(
        input_path, output_path,
        x=0, y=1600, w=1080, h=260,
    ))
    total = time.perf_counter() - t0

    print(f"\nTotal: {total:.2f}s")
    print(f"  LaMa GPU forwards : {_stages['lama']:.2f}s ({_stages['lama']/total*100:.1f}%)")
    print(f"  Everything else   : {total - _stages['lama']:.2f}s ({(total-_stages['lama'])/total*100:.1f}%)")


if __name__ == "__main__":
    main()

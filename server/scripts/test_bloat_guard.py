"""Test the anti-bloat length guard in transcript_cleaner (S5)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.transcript_cleaner as tc

SRC = ("This is a normal transcript about a walk to the store. " * 6).strip()  # ~300 chars


async def main():
    ok = 0

    def check(name, cond):
        nonlocal ok
        print(("  ok  " if cond else "FAIL  ") + name)
        ok += 1 if cond else 0

    # 1. _is_bloated math
    check("_is_bloated: 1.3x flagged", tc._is_bloated("a" * 100, "b" * 131))
    check("_is_bloated: 1.2x exactly NOT flagged", not tc._is_bloated("a" * 100, "b" * 120))
    check("_is_bloated: normal 1.05x NOT flagged", not tc._is_bloated("a" * 100, "b" * 105))

    # 2. _trim_to_ratio caps at 1.2x on sentence boundary
    bloated = "First sentence here. Second sentence here. " * 10  # ~430 chars
    trimmed = tc._trim_to_ratio(bloated, "x" * 100)  # cap = 120
    check("_trim_to_ratio: result <= 1.2x cap", len(trimmed) <= 120)
    check("_trim_to_ratio: ends at sentence boundary", trimmed.rstrip().endswith("."))
    check("_trim_to_ratio: non-empty", len(trimmed) > 0)

    # 3. clean_transcript with a MONKEYPATCHED engine that bloats every call
    calls = {"n": 0}

    async def fake_bloat(engine, chunk, lang, model):
        calls["n"] += 1
        return chunk + " " + chunk + " " + chunk  # 3x bloat, always

    orig = tc._clean_one_chunk
    tc._clean_one_chunk = fake_bloat
    try:
        out = await tc.clean_transcript(SRC, engine="ollama")
        # bloated both times → hard-trim → must be <= 1.2x source
        check("clean_transcript: persistent bloat trimmed to <=1.2x",
              len(out) <= tc.MAX_LEN_RATIO * len(SRC.strip()))
        check("clean_transcript: retried once (2 calls for 1 chunk)", calls["n"] == 2)
    finally:
        tc._clean_one_chunk = orig

    # 4. clean_transcript with a NORMAL engine (~1.0x) passes untouched
    async def fake_ok(engine, chunk, lang, model):
        return chunk  # same length

    tc._clean_one_chunk = fake_ok
    try:
        out2 = await tc.clean_transcript(SRC, engine="ollama")
        check("clean_transcript: normal output untouched", out2.strip() == SRC.strip())
    finally:
        tc._clean_one_chunk = orig

    print(f"\n{ok}/9 passed")
    sys.exit(0 if ok == 9 else 1)


if __name__ == "__main__":
    asyncio.run(main())

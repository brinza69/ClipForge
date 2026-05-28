"""Quick smoke test for caption_aligner."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from services.caption_aligner import align_words, group_into_caption_chunks  # noqa: E402

CLEANED = """Fold the long ends of the towel about this much, and roll each short end to the center. Flip it over and fold it in half.

Take a hand towel and fold it three-quarters down, then tuck the corners in, leaving a small gap. Turn it face down and roll the bottom up. Bend it in half to create the head.

Wrap a rubber band around the base and place the head inside the large towel. Fold the towel over it and stand it upright to complete your bear towel."""


async def main():
    voice = "data/media/eddae58305a9/voice.wav"
    aligned = await align_words(voice, CLEANED)
    print(f"\naligned {len(aligned)} words from voice.wav\n")
    print("first 8:")
    for w in aligned[:8]:
        print(f"  {w['start']:6.3f}–{w['end']:6.3f}  {w['word']!r}")
    print("...")
    print("last 3:")
    for w in aligned[-3:]:
        print(f"  {w['start']:6.3f}–{w['end']:6.3f}  {w['word']!r}")

    chunks = group_into_caption_chunks(aligned, words_per_chunk=4)
    print(f"\ngrouped into {len(chunks)} caption chunks:")
    for c in chunks:
        print(f"  {c['start']:6.2f}–{c['end']:6.2f}  {c['text']!r}")


if __name__ == "__main__":
    asyncio.run(main())

"""Throwaway repro: compare Ollama qwen2.5:7b raw output size for EN clean
vs FR clean+translate, on the same sample transcript. Diagnoses the 2.3x
FR TTS duration anomaly found in job 9109324011d0 (S5 auto-run test)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE = """
so today i want to talk about something that happened to me last week
uh it was honestly one of the craziest things i've ever experienced
i was walking down the street minding my own business you know just
heading to the store to grab some groceries and uh i saw this guy
just standing there like completely frozen just staring at his phone
and i thought okay that's a little weird but whatever people do weird
stuff all the time right but then i noticed like three more people
doing the exact same thing just frozen staring at their phones and i
was like okay something is definitely going on here so i pulled out my
own phone to see what everyone was looking at and turns out there was
this huge breaking news alert that had just gone out to everyone at
the exact same time and everyone in like a two block radius just
stopped what they were doing to read it it was honestly one of the
weirdest collective moments i've ever witnessed like we were all just
standing there frozen reading the same thing at the same time and
nobody said a word to each other we just all silently absorbed this
information and then slowly went back to our days like nothing
happened it really made me think about how connected we all are now
whether we like it or not
""".strip()


async def main():
    from services.transcript_cleaner import (
        _call_ollama, _call_openai, DEFAULT_OLLAMA_MODEL, DEFAULT_OPENAI_MODEL,
    )

    async def _run(engine, label, target_lang):
        if engine == "ollama":
            out = await _call_ollama(SAMPLE, target_lang, DEFAULT_OLLAMA_MODEL)
        else:
            out = await _call_openai(SAMPLE, target_lang, DEFAULT_OPENAI_MODEL)
        print(f"\n=== [{engine}] {label} ===")
        print(f"input words: {len(SAMPLE.split())} | output chars: {len(out)} | words: {len(out.split())}")
        print(f"first 200: {out[:200]}")
        print(f"last 200:  {out[-200:]}")
        return len(out)

    print("############ OLLAMA (qwen2.5:7b) ############")
    en_o = await _run("ollama", "EN", None)
    fr_o = await _run("ollama", "FR", "fr")
    de_o = await _run("ollama", "DE", "de")

    print("\n############ OPENAI (gpt-4o-mini) ############")
    en_a = await _run("openai", "EN", None)
    fr_a = await _run("openai", "FR", "fr")
    de_a = await _run("openai", "DE", "de")

    print("\n############ RATIO vs EN (target ~1.0-1.2) ############")
    print(f"ollama FR/EN = {fr_o/en_o:.2f}   DE/EN = {de_o/en_o:.2f}")
    print(f"openai FR/EN = {fr_a/en_a:.2f}   DE/EN = {de_a/en_a:.2f}")


if __name__ == "__main__":
    asyncio.run(main())

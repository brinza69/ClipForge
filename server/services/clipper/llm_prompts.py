"""
ClipForge — AI Stream Clipper: what we ask a model, in words.

Split from llm_select.py when Batch 4's chunk planner and overlap dedupe took
that file past the repo's 500-line limit. The seam is the same one llm_engine
already draws from the other side: llm_engine is HOW we reach a model, this is
WHAT we ask it, and llm_select is what it does with the answer.

Both prompts here are versioned, because the cached anchor artifact is only
valid for the prompt that produced it — see `clipper_build._anchor_stamp`.
"""

from __future__ import annotations

from typing import Sequence

def nominate_prompt(lines: str, want: int) -> str:
    return (
        f"Below is a livestream transcript, one line per segment, each prefixed "
        f"with its start time in seconds.\n\n"
        f"List up to {want} moments that would work as standalone short clips. "
        f"Favour: a joke that lands, a story with a payoff, a genuine reaction, "
        f"someone being proved wrong, a rule or a plan being broken, an argument. "
        f"Ignore: narrating routine actions, listing inventory, filler, and "
        f"stretches where nothing is resolved.\n\n"
        f'Answer as JSON only: [{{"t": <seconds>, "duration": <15-90>, '
        f'"why": "<max 8 words>"}}]\n\n'
        f"--- TRANSCRIPT ---\n{lines}"
    )


# v2 asks for exact quotes, which is what story_evidence grounds against.
ANCHOR_PROMPT_VERSION = "anchor_v2_quoted"


def anchor_prompt(lines: str, want: int,
                  promises: Sequence[dict] | None = None,
                  episodes: Sequence[dict] | None = None) -> str:
    """Payoff-first. The question is not where a window should start."""
    from services.clipper.story import ARCHETYPES

    # What the stream has been about before this chunk (§2). Without it a
    # moment at hour seven is read as though the stream started at hour seven:
    # the model cannot tell that the fight it is watching has been going for
    # forty minutes, or that an argument began long before these lines.
    #
    # Stated BEFORE the transcript rather than after, because it is context for
    # reading what follows, not an extra instruction to remember.
    so_far = ""
    if episodes:
        from services.clipper.episodes import to_lines as episode_lines

        body = episode_lines(episodes)
        if body:
            so_far = ("WHAT THE STREAM HAS BEEN ABOUT SO FAR, before the lines "
                      "below:\n" + body + "\n\nUse it to tell a moment that "
                      "stands on its own from one that only makes sense to "
                      "someone who has been watching. Do not clip from it — it "
                      "is background, and none of it is in the window.\n\n")

    recall = ""
    if promises:
        # Only the setups still open at this point in the stream. A payoff
        # that lands on one of these is a callback, and it is the one kind of
        # clip a per-chunk pass cannot otherwise see.
        #
        # Stated as its own numbered step, not as an aside. Measured: with the
        # instruction buried and `callback_to` last in a ten-field schema, a
        # model found "finds diamond AFTER ASKING ADMINS" — recognising the
        # link in prose — and still left the field null on all four anchors.
        recall = (
            "\n  5. CALLBACK — these were said EARLIER in the stream and are "
            "still unresolved:\n"
            + "\n".join(f"       [{p['t']:.0f}] ({p['kind']}) {p['text']}"
                        for p in promises[:12])
            + "\n     If a moment below is the answer to one of them, you MUST "
              "set `callback_to` to that timestamp. A payoff that resolves "
              "something said earlier is worth far more than one that does "
              "not, so do not leave it out. Set it to null when nothing "
              "matches.\n")

    return (
        so_far
        + "Below is a livestream transcript, one line per segment, prefixed with "
        "its start time in seconds.\n\n"
        f"Find up to {want} moments that deserve a standalone short clip. For "
        "each one, work BACKWARDS from what happened:\n"
        "  1. the PAYOFF — the thing that makes the moment worth watching, and "
        "when it happens\n"
        "  2. the REQUIRED CONTEXT — every fact a viewer who saw nothing else "
        "must already know for that payoff to land, each with the timestamp "
        "where it is established. Usually one or two. Never more than 90 "
        "seconds before the payoff.\n"
        "  3. the HOOK — the earliest line that gives a stranger a reason to "
        "keep watching, if there is one\n"
        "  4. UNRESOLVED CONTEXT — anything the clip would still leave "
        "unexplained: a name never introduced, an event referred to but not "
        "shown\n\n"
        f"{recall}\n"
        f"Archetypes, choose one or two: {', '.join(ARCHETYPES)}\n\n"
        "Ignore moments that are only loud. Narrating routine actions, reading "
        "an inventory and filler are not payoffs.\n"
        "Some lines end in <angle brackets> with what the audio and picture "
        "were doing there. Treat that as evidence, never as the reason on its "
        "own — LOUD without something happening is not a payoff.\n\n"
        "QUOTE, DO NOT PARAPHRASE. `payoff_quote` and each context `quote` "
        "must be words copied EXACTLY from the lines above. That is what lets "
        "the claim be checked against the transcript — a paraphrase is not "
        "wrong, it is unverifiable, and it is recorded as such rather than "
        "thrown away.\n\n"
        'Answer as JSON only: [{"payoff_t": <seconds>, "payoff_strength": '
        '<0-1>, "payoff_quote": "<exact words from the payoff line>", '
        '"archetypes": ["..."], "why": "<max 12 words>", '
        '"required_context": [{"t": <seconds>, "fact": "<max 8 words>", '
        '"quote": "<exact words from that line>"}], '
        '"hook": {"t": <seconds>}, "unresolved_context": ["..."], '
        '"callback_to": <seconds or null>, "confidence": <0-1>}]\n\n'
        f"--- TRANSCRIPT ---\n{lines}"
    )



# --------------------------------------------------------------------------
# the two passes
# --------------------------------------------------------------------------

"""Chat-replay highlight proxy (Lightor-style, unsupervised) and board comparison.

Usage:
    python analyze_chat.py chat.json [board.json] [--lag 25] [--top 12]

chat.json  : list of chat-downloader messages (time_in_seconds, message, ...)
board.json : list of {"rank", "start", "end", ...} clips chosen by the clipper

Lightor (arXiv:1910.12201) trains a logistic regression on three features per
25 s window: message count, mean message length, message similarity. We have
no labels here, so the three normalised features are averaged with equal
weights. That is an UNCALIBRATED proxy, and the output says so.
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
from collections import Counter

WINDOW_S = 25.0
STEP_S = 5.0
MIN_GAP_S = 120.0  # Lightor: peaks closer than this are one moment

EMOTES = {
    "funny": {"kekw", "lul", "lulw", "omegalul", "icant", "lmao", "lmfao", "xd",
              "kekl", "lol", "💀", "😭", "😂", "🤣", "pepelaugh", "forsenlaughingathim"},
    "hype": {"pog", "pogchamp", "poggers", "pogu", "w", "ww", "www", "hype",
             "letsgo", "lets", "goat", "🔥", "catjam", "peepoclap", "ez", "gigachad"},
    "tense": {"monkas", "monkaw", "d:", "widepeepohappy", "scared", "😱", "😳"},
    "sad": {"sadge", "biblethump", "pepehands", "l", "ll", "f", "😢", "💔"},
}
CLIP_RE = re.compile(r"\bclip\s*(it|that|this)?\b|\bclipped\b", re.I)
TOKEN_RE = re.compile(r"[\w:']+|[\U0001F300-\U0001FAFF]", re.U)


def tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def load_chat(path: str) -> list[dict]:
    raw = json.load(open(path, encoding="utf-8"))
    out = []
    for m in raw:
        t = m.get("time_in_seconds")
        if t is None or t < 0:
            continue
        mtype = m.get("message_type") or "text_message"
        out.append({"t": float(t), "text": m.get("message") or "", "type": mtype,
                    "author": ((m.get("author") or {}).get("name") or "")})
    out.sort(key=lambda m: m["t"])
    return out


def window_features(msgs: list[dict], duration: float) -> list[dict]:
    rows = []
    n_steps = int(max(0.0, duration - WINDOW_S) // STEP_S) + 1
    j0 = 0
    for k in range(n_steps):
        a, b = k * STEP_S, k * STEP_S + WINDOW_S
        while j0 < len(msgs) and msgs[j0]["t"] < a:
            j0 += 1
        j = j0
        win = []
        while j < len(msgs) and msgs[j]["t"] < b:
            if msgs[j]["type"] == "text_message":
                win.append(msgs[j])
            j += 1
        toks = [tokens(m["text"]) for m in win]
        count = len(win)
        mean_len = sum(len(t) for t in toks) / count if count else 0.0
        # Lightor's similarity: one-cluster k-means on binary bag-of-words,
        # i.e. mean cosine of each message to the centroid.
        sim = 0.0
        if count >= 2:
            vocab = Counter(w for t in toks for w in set(t))
            centroid = {w: c / count for w, c in vocab.items()}
            cnorm = math.sqrt(sum(v * v for v in centroid.values())) or 1.0
            sims = []
            for t in toks:
                s = set(t)
                if not s:
                    continue
                dot = sum(centroid.get(w, 0.0) for w in s)
                sims.append(dot / (math.sqrt(len(s)) * cnorm))
            sim = sum(sims) / len(sims) if sims else 0.0
        cats = Counter()
        for t in toks:
            for cat, words in EMOTES.items():
                if any(w in words for w in t):
                    cats[cat] += 1
        clip_asks = sum(1 for m in win if CLIP_RE.search(m["text"]))
        rows.append({"start": a, "end": b, "count": count, "mean_len": mean_len,
                     "sim": sim, "cats": dict(cats), "clip_asks": clip_asks})
    return rows


def normalise(values: list[float]) -> list[float]:
    """Percentile rank on 0..1 — robust to the heavy tail of chat volume."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    for rank, i in enumerate(order):
        out[i] = rank / max(1, len(values) - 1)
    return out


def volume_is_capped(rows: list[dict]) -> bool:
    """Twitch VOD replay on very large channels is rate-capped (~10 msg/s): the
    count barely moves, so it cannot be a feature. Coefficient of variation of
    the per-window count under 0.10 is treated as capped."""
    counts = [r["count"] for r in rows if r["count"]]
    if len(counts) < 10:
        return False
    mean = sum(counts) / len(counts)
    var = sum((c - mean) ** 2 for c in counts) / len(counts)
    return (math.sqrt(var) / mean) < 0.10 if mean else False


def score_windows(rows: list[dict], capped: bool) -> None:
    s = normalise([-r["mean_len"] for r in rows])  # shorter = more reactive
    m = normalise([r["sim"] for r in rows])
    feats = [s, m]
    if capped:
        # Volume is flat: use WHAT chat says instead — the share of hype and
        # funny reactions in the window, each against the whole stream.
        share = lambda r, cat: r["cats"].get(cat, 0) / r["count"] if r["count"] else 0.0
        feats.append(normalise([max(share(r, "hype"), share(r, "funny")) for r in rows]))
    else:
        feats.append(normalise([r["count"] for r in rows]))
    for i, r in enumerate(rows):
        r["score"] = sum(f[i] for f in feats) / len(feats)


def peaks(rows: list[dict], top: int) -> list[dict]:
    ranked = sorted(rows, key=lambda r: -r["score"])
    chosen: list[dict] = []
    for r in ranked:
        centre = (r["start"] + r["end"]) / 2
        if all(abs(centre - (p["start"] + p["end"]) / 2) >= MIN_GAP_S for p in chosen):
            chosen.append(r)
        if len(chosen) >= top:
            break
    return chosen


def covered(t: float, board: list[dict], slack_after: float) -> int | None:
    for clip in board:
        if clip["start"] <= t <= clip["end"] + slack_after:
            return clip["rank"]
    return None


def random_baseline(board: list[dict], duration: float, targets: list[float],
                    slack_after: float, trials: int = 2000, seed: int = 7) -> float:
    rng = random.Random(seed)
    lengths = [c["end"] - c["start"] for c in board]
    hits = 0
    for _ in range(trials):
        fake = []
        for rank, L in enumerate(lengths, 1):
            s = rng.uniform(0, max(0.0, duration - L))
            fake.append({"rank": rank, "start": s, "end": s + L})
        hits += sum(1 for t in targets if covered(t, fake, slack_after) is not None)
    return hits / (trials * max(1, len(targets)))


def main(argv: list[str]) -> int:
    lag = 25.0
    top = 12
    args = [a for a in argv[1:]]
    if "--lag" in args:
        i = args.index("--lag"); lag = float(args[i + 1]); del args[i:i + 2]
    if "--top" in args:
        i = args.index("--top"); top = int(args[i + 1]); del args[i:i + 2]
    msgs = load_chat(args[0])
    text_msgs = [m for m in msgs if m["type"] == "text_message"]
    if not text_msgs:
        print("chat: unavailable (0 text messages)")
        return 2
    duration = msgs[-1]["t"] + 1.0
    per_hour = len(text_msgs) / (duration / 3600.0)
    print(f"chat: {len(text_msgs)} text messages over {duration/60:.1f} min "
          f"= {per_hour:.0f}/h (Lightor needed > 500/h)")
    rows = window_features(msgs, duration)
    capped = volume_is_capped(rows)
    print(f"volume capped: {capped} (count feature {'replaced by emote shares' if capped else 'used'})")
    score_windows(rows, capped)
    # Stream start/end greetings and goodbyes spike every emote and are not
    # moments (Lightor's own caveat): peaks are taken from the middle only.
    edge = 120.0
    middle = [r for r in rows if r["start"] >= edge and r["end"] <= duration - edge]
    print(f"peaks exclude the first and last {edge:.0f}s ({len(rows) - len(middle)} of {len(rows)} windows)")
    top_peaks = peaks(middle, top)
    print(f"\nTop {len(top_peaks)} chat moments (uncalibrated proxy; start estimate = window peak - {lag:.0f}s):")
    for p in top_peaks:
        cat = max(p["cats"], key=p["cats"].get) if p["cats"] else "-"
        print(f"  window {p['start']/60:6.2f}-{p['end']/60:6.2f} min  score {p['score']:.2f}  "
              f"msgs {p['count']:4d}  mean_len {p['mean_len']:4.1f}  sim {p['sim']:.2f}  "
              f"top_emote {cat:6s}  clip_asks {p['clip_asks']}")
    clip_total = sum(r["clip_asks"] for r in rows) / (WINDOW_S / STEP_S)
    print(f"\n'clip it/that' messages in chat: ~{clip_total:.0f}")

    result = {"messages": len(text_msgs), "per_hour": per_hour, "lag_s": lag,
              "peaks": [{"start": p["start"], "end": p["end"], "score": p["score"],
                         "count": p["count"], "cats": p["cats"],
                         "clip_asks": p["clip_asks"]} for p in top_peaks]}
    if len(args) > 1:
        board = json.load(open(args[1], encoding="utf-8"))
        # The moment the chat reacted to happens BEFORE the chat peak.
        targets = [max(0.0, (p["start"] + p["end"]) / 2 - lag) for p in top_peaks]
        if not targets:
            print("\nBoard vs chat: unavailable (0 chat moments to compare)")
            return 2
        slack = 0.0
        hits = [covered(t, board, slack) for t in targets]
        n_hit = sum(1 for h in hits if h is not None)
        base = random_baseline(board, duration, targets, slack)
        print(f"\nBoard vs chat: {n_hit} of {len(targets)} chat moments fall inside a board clip "
              f"({n_hit/len(targets):.0%}); random boards of the same lengths: {base:.0%}")
        clip_hits = []
        for c in board:
            inside = [p for p in top_peaks
                      if c["start"] <= max(0.0, (p["start"] + p["end"]) / 2 - lag) <= c["end"]]
            clip_hits.append(len(inside))
            print(f"  rank {c['rank']:2d}  {c['start']/60:6.2f}-{c['end']/60:6.2f} min  "
                  f"chat moments inside: {len(inside)}")
        result.update({"board_hits": n_hit, "targets": len(targets), "random_rate": base,
                       "per_clip_hits": clip_hits})
    json.dump(result, open("chat_result.json", "w"), indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

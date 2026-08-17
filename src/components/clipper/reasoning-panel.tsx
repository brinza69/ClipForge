"use client";

// Why this clip, and why these boundaries.
//
// The backend has recorded all of this in `clips.reasoning` since the story
// engine shipped — the anchor, the payoff it was reasoned back from, the facts
// a cold viewer needs, the archetype, which edit variant won, and the three
// judges' verdicts — and nothing has ever displayed it. This is that panel.
//
// Everything is optional. A clip scored by the legacy chain carries only the
// boundary reasons and, if the judge ran, its verdict; it should still open and
// show what it has rather than a wall of blanks.

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { ClipperClip, ClipReview, ClipStory } from "@/types/clipper";

// Timestamps are stored on the SOURCE clock, which is what makes them useful:
// they point into the original VOD, not into the cut.
function at(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = `${m}`.padStart(h > 0 ? 2 : 1, "0");
  return `${h > 0 ? `${h}:` : ""}${mm}:${`${sec}`.padStart(2, "0")}`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 text-xs">
      <span className="w-28 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}

function Chip({ children, tone = "neutral" }: {
  children: React.ReactNode;
  tone?: "neutral" | "good" | "bad" | "warn";
}) {
  const tones = {
    neutral: "border-border/50 bg-muted/30 text-foreground/80",
    good: "border-emerald-500/30 bg-emerald-500/10 text-emerald-500",
    bad: "border-rose-500/30 bg-rose-500/10 text-rose-500",
    warn: "border-amber-500/30 bg-amber-500/10 text-amber-500",
  };
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${tones[tone]}`}>
      {children}
    </span>
  );
}

// A cost, not a score: 0 is free, 1 is unwatchable without context.
//
// `fill` and `shown` are separate on purpose. Hook latency is measured in
// SECONDS and has to be scaled to fill a 0..1 bar, but the number beside the
// bar must stay the seconds — the first version printed the scaled value and
// labelled it "s", so a 5-second hook read as "0.50s".
function Cost({ label, fill, shown }: { label: string; fill: number; shown: string }) {
  const pct = Math.max(0, Math.min(100, fill * 100));
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[11px]">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">{shown}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full ${pct >= 50 ? "bg-rose-500" : pct >= 20 ? "bg-amber-500" : "bg-emerald-500"}`}
          style={{ width: `${Math.max(2, pct)}%` }}
        />
      </div>
    </div>
  );
}

// Pass D. Everything above this line explains why the MOMENT was chosen; this
// is the only part that says anything about the CUT that was made of it.
//
// Its timestamps are clip-relative, unlike the story's — a finding is something
// to go and look at in the exported file, not in the VOD.
function ReviewBlock({ review }: { review: ClipReview }) {
  const tone = { APPROVE: "good", REVISE: "warn", REJECT: "bad" } as const;
  const blind = review.sampled === 0;

  return (
    <div className="space-y-2.5 rounded-lg border border-border/40 bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-muted-foreground">What the review saw in the cut</p>
        <Chip tone={tone[review.verdict]}>{review.verdict}</Chip>
      </div>

      {blind ? (
        <p className="text-xs text-muted-foreground">
          Nothing was sampled, so this verdict is a default rather than a
          judgement{review.warnings?.length ? ` — ${review.warnings[0]}` : ""}.
        </p>
      ) : review.findings.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {review.sampled} frames checked for a caption over game UI, a blank
          frame, and a crop cutting the speaker&apos;s face. None of the three.
        </p>
      ) : (
        <div className="space-y-1.5">
          {review.findings.map((f, i) => (
            <div key={`${f.kind}-${i}`} className="flex gap-2 text-xs">
              <span className="shrink-0 tabular-nums text-muted-foreground">
                +{f.at.toFixed(1)}s
              </span>
              <span className="min-w-0 flex-1">{f.detail}</span>
              {f.severity === "reject" && <Chip tone="bad">reject</Chip>}
            </div>
          ))}
          <p className="pt-1 text-[11px] text-muted-foreground">
            Advisory — the clip was exported anyway. The caption check reads flat
            mid-grey as game UI and can call grey terrain a panel, so look before
            you act on one.
          </p>
        </div>
      )}
    </div>
  );
}

function StoryBlock({ story }: { story: ClipStory }) {
  const context = story.required_context ?? [];
  return (
    <div className="space-y-3">
      {story.why && <p className="text-xs leading-relaxed">{story.why}</p>}

      <div className="space-y-1.5">
        <Row label="Payoff at">
          <span className="tabular-nums">{at(story.payoff_t)}</span>
          {story.anchor_t !== undefined && story.anchor_t !== story.payoff_t && (
            <span className="text-muted-foreground"> · anchor {at(story.anchor_t)}</span>
          )}
        </Row>
        <Row label="Hook at">
          <span className="tabular-nums">{at(story.hook_t)}</span>
        </Row>
        {story.archetypes?.length ? (
          <Row label="Archetype">
            <span className="flex flex-wrap gap-1">
              {story.archetypes.map((a) => (
                <Chip key={a}>{a}</Chip>
              ))}
            </span>
          </Row>
        ) : null}
        {story.edit_reason && <Row label="Cut this way">{story.edit_reason}</Row>}
        {story.thread_id && (
          <Row label="Story thread">
            <code className="text-[11px] text-foreground/70">{story.thread_id}</code>
          </Row>
        )}
      </div>

      {context.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] text-muted-foreground">
            What a cold viewer has to already know
          </p>
          {context.map((c, i) => (
            <div key={i} className="flex gap-2 text-xs">
              <span className="shrink-0 tabular-nums text-muted-foreground">{at(c.t)}</span>
              <span>{c.fact}</span>
            </div>
          ))}
        </div>
      )}

      {story.callback_to && (
        <p className="rounded-lg border border-border/40 bg-muted/20 p-2.5 text-xs">
          <span className="text-muted-foreground">Pays off a setup from </span>
          <span className="tabular-nums">{at(story.callback_to.t)}</span>
          {story.callback_to.text && <> — “{story.callback_to.text}”</>}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {story.context_debt !== undefined && (
          <Cost
            label="Context debt"
            fill={story.context_debt}
            shown={story.context_debt.toFixed(2)}
          />
        )}
        {story.hook_latency !== undefined && (
          <Cost
            label="Hook latency"
            // Full bar at 10s: past that a cold viewer has long gone, so the
            // exact value stops mattering to the eye. The number keeps it.
            fill={Math.min(1, story.hook_latency / 10)}
            shown={`${story.hook_latency.toFixed(1)}s`}
          />
        )}
      </div>
    </div>
  );
}

export function ReasoningPanel({
  clip,
  open,
  onOpenChange,
}: {
  clip: ClipperClip | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  if (!clip) return null;
  const r = clip.reasoning;
  const verdict = r?.llm_verdict;
  const rejects = verdict?.reject_reasons ?? [];
  const versions = [r?.story?.story_version, verdict?.prompt_version].filter(Boolean);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base">Why this clip</DialogTitle>
        </DialogHeader>

        {/* Outside the `!r` branch on purpose: a clip exported by the legacy
            chain has no reasoning and can still carry a review, and that is
            exactly the clip whose findings someone wants to see. */}
        {clip.review && (
          <div className="mb-4">
            <ReviewBlock review={clip.review} />
          </div>
        )}

        {!r ? (
          <p className="rounded-lg border border-border/40 bg-muted/20 p-3 text-xs text-muted-foreground">
            This clip was scored before the reasoning was recorded, or by the legacy
            chain with the LLM passes off. There is nothing to show — which is itself
            the answer to “why this clip”: signal strength, not a story.
          </p>
        ) : (
          <div className="space-y-4">
            {r.story ? (
              <StoryBlock story={r.story} />
            ) : (
              <p className="text-xs text-muted-foreground">
                Found by the heuristic chain — no anchor was reasoned back from a payoff.
              </p>
            )}

            {(verdict || r.llm_reason) && (
              <div className="space-y-2 border-t border-border/40 pt-3">
                <p className="text-[11px] text-muted-foreground">
                  The judge, from three perspectives
                  {r.llm_rank !== undefined && <> · ranked #{r.llm_rank}</>}
                </p>
                {r.llm_reason && <p className="text-xs">{r.llm_reason}</p>}
                <div className="flex flex-wrap gap-1.5">
                  {verdict?.story_editor && <Chip>editor: {verdict.story_editor}</Chip>}
                  {/* Weighted highest on purpose: a cold viewer is who decides a short. */}
                  {verdict?.cold_viewer && <Chip>cold viewer: {verdict.cold_viewer}</Chip>}
                  {verdict?.critic && <Chip>critic: {verdict.critic}</Chip>}
                </div>
                {rejects.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {rejects.map((x) => (
                      <Chip key={x} tone="bad">
                        {x.replace(/_/g, " ")}
                      </Chip>
                    ))}
                  </div>
                )}
              </div>
            )}

            {r.reasons?.length ? (
              <div className="space-y-1.5 border-t border-border/40 pt-3">
                <p className="text-[11px] text-muted-foreground">
                  How the boundaries were chosen
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {r.variant && <Chip tone="good">variant: {r.variant}</Chip>}
                  {r.reasons.map((x) => (
                    <Chip key={x}>{x.replace(/_/g, " ")}</Chip>
                  ))}
                </div>
              </div>
            ) : null}

            {versions.length > 0 && (
              <p className="border-t border-border/40 pt-3 text-[11px] text-muted-foreground">
                Produced by{" "}
                {versions.map((v, i) => (
                  <span key={v}>
                    {i > 0 && ", "}
                    <code className="text-foreground/70">{v}</code>
                  </span>
                ))}
                . The models are not deterministic — the same candidates scored twice
                have given different orderings.
              </p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

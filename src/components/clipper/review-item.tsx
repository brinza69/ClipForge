"use client";

// One clip, the six questions, and nothing that says which board asked for it.
//
// The blind is enforced server-side — `public_item` is an allowlist — so this
// component *cannot* leak membership even if it tried. What it still has to get
// right is the reviewer: the video comes first and the transcript stays folded
// until they ask for it, because "would you export this" is a question about
// what the clip is like to watch, and reading it first answers a different one.

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  OPTION_LABELS,
  QUESTION_LABELS,
  REASON_LABELS,
  type ReviewItem,
  type ReviewRubric,
} from "@/types/clipper-review";

export type Answers = Record<string, string>;

type Props = {
  item: ReviewItem;
  rubric: ReviewRubric;
  videoSrc: string;
  submitting: boolean;
  onSubmit: (answers: Answers, reasons: string[], note: string,
             watchFraction: number | null) => void;
};

export function ReviewItemCard({ item, rubric, videoSrc, submitting, onSubmit }: Props) {
  const [answers, setAnswers] = useState<Answers>({});
  const [reasons, setReasons] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [showTranscript, setShowTranscript] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  // How much of the clip they actually watched. A verdict given after two
  // seconds is a different measurement from one given after the whole clip, and
  // without this the results cannot tell them apart.
  const watched = useRef(0);

  // Everything resets when the item does. Without this the next clip inherits
  // the previous verdict, and a reviewer clicking through agreement would
  // submit answers they never gave.
  useEffect(() => {
    setAnswers({});
    setReasons([]);
    setNote("");
    setShowTranscript(false);
    watched.current = 0;
  }, [item.review_item_id]);

  const rejected = answers.worth_exporting === "no";
  const complete =
    rubric.questions.every((q) => answers[q.key]) && (!rejected || reasons.length > 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          Clip {item.position} din {item.total}
        </span>
        <span>
          {item.duration != null ? `${item.duration.toFixed(1)}s` : "—"}
        </span>
      </div>

      {item.preview_ready ? (
        <video
          ref={video}
          key={item.review_item_id}
          src={videoSrc}
          controls
          autoPlay
          className="mx-auto max-h-[60vh] rounded-lg bg-black"
          onTimeUpdate={(e) => {
            const el = e.currentTarget;
            if (el.duration > 0) {
              watched.current = Math.max(watched.current, el.currentTime / el.duration);
            }
          }}
        />
      ) : (
        <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
          Preview-ul nu e randat pentru acest clip. Răspunde din transcript sau
          sari peste sesiune și randează-l întâi.
        </div>
      )}

      <div className="space-y-3">
        {rubric.questions.map((q) => (
          <div key={q.key} className="flex flex-wrap items-center gap-2">
            <span className="w-44 shrink-0 text-sm">
              {QUESTION_LABELS[q.key] ?? q.key}
            </span>
            {q.options.map((option) => (
              <Button
                key={option}
                type="button"
                size="sm"
                variant={answers[q.key] === option ? "default" : "outline"}
                onClick={() => setAnswers((a) => ({ ...a, [q.key]: option }))}
              >
                {OPTION_LABELS[option] ?? option}
              </Button>
            ))}
          </div>
        ))}
      </div>

      {rejected && (
        <div className="space-y-2 rounded-lg border p-3">
          <p className="text-sm">
            De ce nu? Cel puțin un motiv — „a fost slab" nu spune dacă e de
            reparat selecția, marginile sau randarea.
          </p>
          <div className="flex flex-wrap gap-2">
            {rubric.reject_reasons.map((reason) => (
              <Button
                key={reason}
                type="button"
                size="sm"
                variant={reasons.includes(reason) ? "default" : "outline"}
                onClick={() =>
                  setReasons((r) =>
                    r.includes(reason) ? r.filter((x) => x !== reason) : [...r, reason],
                  )
                }
              >
                {REASON_LABELS[reason] ?? reason}
              </Button>
            ))}
          </div>
        </div>
      )}

      <div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setShowTranscript((v) => !v)}
        >
          {showTranscript ? "Ascunde transcriptul" : "Arată transcriptul"}
        </Button>
        {showTranscript && (
          <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-sm">
            {item.transcript || "—"}
          </p>
        )}
      </div>

      <Textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Notă (opțional)"
        rows={2}
      />

      <Button
        className="w-full"
        disabled={!complete || submitting}
        onClick={() =>
          onSubmit(answers, reasons, note, watched.current || null)
        }
      >
        {submitting ? "Se salvează…" : "Următorul clip"}
      </Button>
    </div>
  );
}

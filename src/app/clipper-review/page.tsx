"use client";

// Blind review — the page Batch 10 is done in, and a debugging surface after.
//
// Shadow mode computes a v2 board and ships legacy's. On the pilot's four
// sources the two disagreed on 7, 6, 8 and 8 of 8 winners, and no artefact can
// say which is better. This is where a person answers that.
//
// State lives on the server, deliberately. The session's order, its seed and
// which board asked for each clip are all written down at creation; the page
// asks for "the next unanswered item" and never computes what that is. A page
// that tracked its own position would renumber itself on reload, and answers
// would land on different clips than the ones they were given for.

import { useCallback, useEffect, useState } from "react";
import { Eye } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ReviewItemCard, type Answers } from "@/components/clipper/review-item";
import { readApiError, errorDescription } from "@/lib/api-error";
import { CLIPPER_API, type ClipperProjectSummary } from "@/types/clipper";
import {
  REVIEW_API,
  type ReviewNext,
  type ReviewResult,
  type ReviewRubric,
} from "@/types/clipper-review";

const SESSION_KEY = "clipforge.review.session";

export default function ClipperReviewPage() {
  const [projects, setProjects] = useState<ClipperProjectSummary[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [rubric, setRubric] = useState<ReviewRubric | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [next, setNext] = useState<ReviewNext | null>(null);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessionIssue, setSessionIssue] = useState<string | null>(null);
  const [historical, setHistorical] = useState(false);

  useEffect(() => {
    // The session id is the only thing kept on the client, and only so a reload
    // does not strand a half-finished session. Everything it means lives on the
    // server.
    setSessionId(window.localStorage.getItem(SESSION_KEY));
    void (async () => {
      try {
        const [p, r] = await Promise.all([
          fetch(`${CLIPPER_API}/projects`),
          fetch(`${REVIEW_API}/rubric`),
        ]);
        if (p.ok) setProjects((await p.json()) as ClipperProjectSummary[]);
        if (r.ok) setRubric((await r.json()) as ReviewRubric);
      } catch {
        toast.error("Nu am putut încărca proiectele sau rubrica.");
      }
    })();
  }, []);

  const loadNext = useCallback(async (id: string) => {
    try {
      const r = await fetch(`${REVIEW_API}/${id}/next`);
      if (!r.ok) {
        const body = await r.clone().json().catch(() => null);
        const old = body?.detail === "historical_review_read_only";
        setHistorical(old);
        setNext(null);
        const e = await readApiError(r, "Nu am putut încărca următorul clip");
        setSessionIssue(old
          ? "Sesiunea veche poate fi consultată, dar nu mai poate primi răspunsuri. Pornește una nouă pentru un review protejat."
          : "Sesiunea nu poate continua. Fișierul sau datele lui nu mai corespund verificării; vezi detaliile erorii.");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      setSessionIssue(null);
      setHistorical(false);
      setNext((await r.json()) as ReviewNext);
    } catch {
      setSessionIssue("Nu am putut contacta serverul. Poți reîncerca fără să pierzi răspunsurile salvate.");
      toast.error("Nu am putut încărca următorul clip.");
    }
  }, []);

  useEffect(() => {
    if (sessionId) void loadNext(sessionId);
  }, [sessionId, loadNext]);

  async function start() {
    setBusy(true);
    try {
      const r = await fetch(REVIEW_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_ids: picked }),
      });
      if (!r.ok) {
        const e = await readApiError(r, "Nu am putut porni sesiunea");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      const out = (await r.json()) as { session_id: string; total: number };
      window.localStorage.setItem(SESSION_KEY, out.session_id);
      setSessionId(out.session_id);
      setResult(null);
      toast.success(`Sesiune pornită: ${out.total} clipuri.`);
    } catch {
      toast.error("Nu am putut porni sesiunea.");
    } finally {
      setBusy(false);
    }
  }

  async function submit(
    answers: Answers, reasons: string[], note: string, watch: number | null,
  ) {
    if (!sessionId || !next || next.done) return;
    setBusy(true);
    try {
      const r = await fetch(`${REVIEW_API}/${sessionId}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          review_item_id: next.item.review_item_id,
          ...answers,
          reject_reasons: reasons,
          note,
          watch_fraction: watch,
        }),
      });
      if (!r.ok) {
        const e = await readApiError(r, "Răspunsul nu s-a salvat");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      await loadNext(sessionId);
    } catch {
      toast.error("Răspunsul nu s-a salvat.");
    } finally {
      setBusy(false);
    }
  }

  async function showResult() {
    if (!sessionId) return;
    try {
      const r = await fetch(`${REVIEW_API}/${sessionId}/result`);
      if (!r.ok) {
        const e = await readApiError(r, "Nu am putut citi rezultatul");
        toast.error(e.message, { description: errorDescription(e) });
        return;
      }
      setResult((await r.json()) as ReviewResult);
    } catch {
      toast.error("Nu am putut citi rezultatul.");
    }
  }

  function reset() {
    window.localStorage.removeItem(SESSION_KEY);
    setSessionId(null);
    setNext(null);
    setResult(null);
    setSessionIssue(null);
    setHistorical(false);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <header className="flex items-center gap-3">
        <Eye className="h-6 w-6" />
        <div>
          <h1 className="text-2xl font-semibold">Review orb</h1>
          <p className="text-sm text-muted-foreground">
            Clipuri amestecate din ambele board-uri. Care motor le-a ales nu
            ajunge la această pagină până nu termini toate clipurile.
            Răspunsurile salvate nu mai pot fi schimbate.
          </p>
        </div>
      </header>

      {!sessionId && (
        <section className="space-y-3 rounded-lg border p-4">
          <h2 className="font-medium">Alege proiectele</h2>
          {projects.length === 0 ? (
            <Skeleton className="h-10 w-full" />
          ) : (
            <div className="flex flex-wrap gap-2">
              {projects.map((p) => (
                <Button
                  key={p.id}
                  size="sm"
                  variant={picked.includes(p.id) ? "default" : "outline"}
                  onClick={() =>
                    setPicked((s) =>
                      s.includes(p.id) ? s.filter((x) => x !== p.id) : [...s, p.id],
                    )
                  }
                >
                  {p.title || p.id}
                </Button>
              ))}
            </div>
          )}
          <Button disabled={picked.length === 0 || busy} onClick={start}>
            Pornește sesiunea
          </Button>
        </section>
      )}

      {sessionId && sessionIssue && (
        <section className="space-y-3 rounded-lg border p-4">
          <p role="alert" className="text-sm">{sessionIssue}</p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => void loadNext(sessionId)}>
              Reîncearcă
            </Button>
            {historical && (
              <Button size="sm" variant="outline" onClick={showResult}>Rezultat istoric</Button>
            )}
            <Button size="sm" variant="ghost" onClick={reset}>Sesiune nouă</Button>
          </div>
        </section>
      )}

      {sessionId && next && rubric && (
        <section className="space-y-4 rounded-lg border p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">
              {next.answered} din {next.total} răspunse
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={showResult} disabled={!next.done}>
                {next.done ? "Rezultat" : "Rezultat după ultimul clip"}
              </Button>
              <Button size="sm" variant="ghost" onClick={reset}>
                Sesiune nouă
              </Button>
            </div>
          </div>

          {next.done ? (
            <p className="py-8 text-center">
              Gata — toate cele {next.total} clipuri au fost evaluate. Apasă
              „Rezultat".
            </p>
          ) : (
            <ReviewItemCard
              item={next.item}
              rubric={rubric}
              videoSrc={`${REVIEW_API}/${sessionId}/item/${next.item.review_item_id}/video`}
              submitting={busy}
              onSubmit={submit}
            />
          )}
        </section>
      )}

      {result && (
        <section className="space-y-3 rounded-lg border p-4">
          <h2 className="font-medium">Rezultat</h2>
          {result.historical && (
            <p className="text-sm text-muted-foreground">
              Sesiune istorică: protecția actuală a review-ului orb nu poate fi confirmată.
            </p>
          )}
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1">board</th>
                <th>văzute</th>
                <th>da</th>
                <th>nu</th>
                <th>nesigur</th>
                <th>precizie</th>
              </tr>
            </thead>
            <tbody>
              {(["legacy", "shadow"] as const).map((side) => {
                const t = result.tally[side];
                return (
                  <tr key={side} className="border-t">
                    <td className="py-1 font-medium">
                      {side === "legacy" ? "legacy (livrat)" : "v2 (shadow)"}
                    </td>
                    <td>{t.reviewed}</td>
                    <td>{t.yes}</td>
                    <td>{t.no}</td>
                    <td>{t.unsure}</td>
                    <td>{t.precision == null ? "—" : t.precision.toFixed(2)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-xs text-muted-foreground">
            Un clip pe care ambele board-uri l-au ales contează la amândouă:
            acordul nu e dovadă pentru niciunul. „Nesigur" nu contează nici ca da,
            nici ca nu.
          </p>
        </section>
      )}
    </div>
  );
}

"use client";

import Link from "next/link";
import { memo, useState, type FormEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowUpRight, Database, Loader2, ShieldAlert, Sparkles } from "lucide-react";
import { cx } from "@/components/ui";
import { EASE_OUT_EXPO, motionDuration, pressTap } from "@/components/motion/variants";
import type { HealthView } from "@/lib/council";

/*
  The idle-state hero of the Decision Room: one serif question, one big input,
  one button. Everything else (voice, steering, inspectors) lives in the rail.

  This component renders the strict-live gates *inline* instead of bouncing the
  operator to another tab: preflight blockers explain themselves, and a missing
  dataset offers a one-click "Load demo company" that runs the real backend
  reseed (live OpenAI embeddings + Redis writes — never mocked).
*/

export type DataGateView = {
  status: "checking" | "ready" | "empty" | "incomplete";
  loaded: number;
  required: number;
};

const EXAMPLE_DECISIONS = [
  "Should we sign the $240K/yr Datadog renewal, or consolidate observability on a cheaper stack?",
  "Can we afford two senior platform engineers in Q3 without breaching the board's runway floor?",
  "Approve a $180K one-time SOC 2 Type II audit this quarter?",
];

function ComposerBase({
  healthReady,
  health,
  running,
  dataGate,
  demoLoading,
  onLoadDemo,
  onSubmit,
}: {
  healthReady: boolean;
  health: HealthView;
  running: boolean;
  dataGate: DataGateView;
  demoLoading: boolean;
  onLoadDemo: () => Promise<void>;
  onSubmit: (text: string) => Promise<void>;
}) {
  const reduced = Boolean(useReducedMotion());
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const gateBlocked = dataGate.status === "empty" || dataGate.status === "incomplete";
  const disabled = !healthReady || running || pending || demoLoading;

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || disabled) return;
    setPending(true);
    setError(null);
    try {
      await onSubmit(content);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The council could not be convened.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="command-surface command-surface--feature relative overflow-hidden">
      {/* Static atmosphere — painted once, never animated. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 left-1/2 h-[420px] w-[720px] -translate-x-1/2"
        style={{
          background:
            "radial-gradient(closest-side, color-mix(in srgb, var(--gilt) 9%, transparent), transparent 72%)",
        }}
      />

      <div className="relative px-5 py-9 sm:px-8 md:px-12 md:py-12">
        <motion.div
          initial={reduced ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: motionDuration.reveal, ease: EASE_OUT_EXPO }}
        >
          <span className="kicker kicker--gilt">Atlas Council · Live deliberation</span>
          <h1 className="headline mt-3 max-w-[640px] text-[34px] font-medium text-foreground sm:text-[44px] md:text-[52px]">
            Put it to the council.
          </h1>
          <p className="lede mt-3 max-w-[560px]">
            Treasury, FP&amp;A, Risk and Procurement debate your decision against the company&apos;s real
            books. The CFO returns a quantified, board-ready ruling.
          </p>
        </motion.div>

        <motion.form
          onSubmit={handleSubmit}
          aria-label="Frame a decision for the Atlas council"
          className="mt-7 max-w-[720px]"
          initial={reduced ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: motionDuration.reveal, ease: EASE_OUT_EXPO, delay: reduced ? 0 : 0.08 }}
        >
          <div
            className={cx(
              "rounded-xl border bg-background p-2 shadow-sm transition-colors",
              "border-border-strong/70 focus-within:border-gilt/70",
            )}
          >
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void handleSubmit();
                }
              }}
              rows={3}
              disabled={running || pending}
              aria-label="Decision prompt"
              placeholder={
                healthReady
                  ? 'Frame the decision — "Should we sign the $240K Datadog renewal?"'
                  : "Locked until every live system reports green…"
              }
              className="min-h-[88px] w-full resize-none bg-transparent px-3 py-2.5 font-serif text-[16px] leading-relaxed text-foreground outline-none placeholder:text-subtle-foreground sm:text-[17px]"
            />
            <div className="flex items-center justify-between gap-3 border-t border-border/70 px-3 py-2">
              <span className="hidden font-mono text-[10px] uppercase tracking-[0.14em] text-subtle-foreground sm:block">
                ↵ convene · shift+↵ newline
              </span>
              <motion.button
                type="submit"
                disabled={disabled || !draft.trim()}
                whileTap={reduced || disabled ? undefined : pressTap}
                className={cx(
                  "command-send-button inline-flex h-10 items-center gap-2 rounded-lg border px-5 text-[13px] font-semibold transition-colors disabled:opacity-40",
                  "border-accent bg-accent text-accent-foreground hover:brightness-[1.04]",
                  pending && "command-send-button--pending",
                )}
              >
                {pending ? <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} /> : <Sparkles className="h-4 w-4" strokeWidth={2.25} />}
                {pending ? "Convening…" : "Convene the council"}
              </motion.button>
            </div>
          </div>
        </motion.form>

        <motion.div
          className="mt-4 flex max-w-[720px] flex-wrap gap-2"
          initial={reduced ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: motionDuration.reveal, ease: EASE_OUT_EXPO, delay: reduced ? 0 : 0.16 }}
        >
          {EXAMPLE_DECISIONS.map((example) => (
            <button
              key={example}
              type="button"
              disabled={running || pending}
              onClick={() => setDraft(example)}
              className="max-w-full truncate rounded-full border border-border bg-surface px-3 py-1.5 text-left text-[11.5px] font-medium text-muted-foreground transition-colors hover:border-border-strong hover:bg-surface-muted hover:text-foreground disabled:opacity-40"
              title={example}
            >
              {example}
            </button>
          ))}
        </motion.div>

        {/* Inline gates — explain, never bounce the operator to another tab. */}
        {error && (
          <p role="alert" className="mt-4 max-w-[720px] rounded-md border border-risk/25 bg-risk-bg px-3 py-2 text-[12px] leading-relaxed text-risk">
            {error}
          </p>
        )}

        {!healthReady && (
          <div className="mt-4 flex max-w-[720px] items-start gap-2.5 rounded-md border border-warning/25 bg-warning-bg px-3 py-2.5 text-warning">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2.25} />
            <p className="text-[12px] leading-relaxed">
              {health.status === "loading"
                ? "Strict-live preflight is checking OpenAI, W&B Weave, Redis and CopilotKit…"
                : "Strict-live preflight is blocked — submissions unlock when every sponsor system reports green."}
            </p>
          </div>
        )}

        {healthReady && gateBlocked && (
          <div className="mt-4 max-w-[720px] rounded-md border border-info/25 bg-info-bg px-3.5 py-3 text-info">
            <div className="flex items-start gap-2.5">
              <Database className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2.25} />
              <div className="min-w-0 flex-1">
                <p className="text-[12.5px] font-semibold leading-snug">
                  {dataGate.status === "empty"
                    ? "No company data is loaded yet."
                    : `Company data is incomplete — ${dataGate.loaded} of ${dataGate.required} required sources loaded.`}
                </p>
                <p className="mt-1 text-[12px] leading-relaxed opacity-85">
                  The council only argues from real records. Upload your own files, or load the demo
                  company (a full live reseed — ledger, vendors, policies, embeddings).
                </p>
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={demoLoading}
                    onClick={() => void onLoadDemo()}
                    className="inline-flex h-8 items-center gap-1.5 rounded-md border border-info/40 bg-info px-3 text-[12px] font-semibold text-accent-foreground transition-colors hover:brightness-105 disabled:opacity-50"
                  >
                    {demoLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2.25} /> : <Sparkles className="h-3.5 w-3.5" strokeWidth={2.25} />}
                    {demoLoading ? "Seeding live demo company…" : "Load demo company"}
                  </button>
                  <Link
                    href="/dashboard"
                    className="inline-flex h-8 items-center gap-1 rounded-md border border-info/30 bg-transparent px-3 text-[12px] font-semibold text-info transition-colors hover:bg-info-bg"
                  >
                    Upload my own data
                    <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2.25} />
                  </Link>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export const DecisionComposer = memo(ComposerBase);

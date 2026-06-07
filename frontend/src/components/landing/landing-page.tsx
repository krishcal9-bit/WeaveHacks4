"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import Link from "next/link";
import { motion, AnimatePresence, animate, useMotionValue, useTransform, useReducedMotion } from "motion/react";
import { ArrowRight, ChevronRight } from "lucide-react";
import { APP_NAME } from "@/lib/branding";
import { AtlasIcon, type AtlasIconName } from "@/components/atlas-icon";
import { MotionLink } from "@/components/motion/motion-link";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import { hoverLift, hoverLiftStrong, motionDuration, pressSubtle, springSnappy, transitionFade, transitionReveal } from "@/components/motion/variants";
import { cx } from "@/components/ui";
import { useMounted } from "@/lib/use-mounted";

const STEPS = [
  {
    n: "01",
    icon: "upload",
    title: "Drop your books",
    detail:
      "Ledger exports, AP aging, vendor contracts, pipeline — Atlas Finance reads the files you already have and reconciles them against company records.",
    accent: "border-accent/25 bg-accent/5",
  },
  {
    n: "02",
    icon: "council",
    title: "Committee argues",
    detail:
      "Treasury, FP&A, Risk, and Procurement take positions on the decision. Every number they cite comes from your uploaded data, not a guess.",
    accent: "border-positive/25 bg-positive-bg",
  },
  {
    n: "03",
    icon: "memo",
    title: "CFO signs off",
    detail:
      "You get a recommendation with runway impact calculated from live cash, supporting evidence, and a memo you can hand to the board.",
    accent: "border-info/25 bg-info-bg",
  },
] satisfies Array<{ n: string; icon: AtlasIconName; title: string; detail: string; accent: string }>;

const AGENTS = [
  {
    id: "treasury",
    label: "Treasury",
    icon: "runway",
    role: "Liquidity mechanics",
    blurb: "Stress-tests runway, payment timing, renewal cash dates, working capital, and late financing close risk.",
  },
  {
    id: "fpna",
    label: "FP&A",
    icon: "scenario",
    role: "Forecast and unit economics",
    blurb: "Tests ARR movement, pipeline probability, margin, CAC payback, scenario sensitivity, and plan-vs-actual deltas.",
  },
  {
    id: "risk",
    label: "Risk & Audit",
    icon: "risk",
    role: "Controls adversary",
    blurb: "Challenges policy violations, audit gaps, approvals, data quality, security evidence, and source provenance.",
  },
  {
    id: "procurement",
    label: "Procurement",
    icon: "evidence",
    role: "Vendor negotiation",
    blurb: "Builds leverage from renewal dates, auto-renewal terms, price benchmarks, switching cost, SLAs, and volume discounts.",
  },
] as const;

const PREVIEW_LINES = [
  {
    agent: "Treasury",
    tone: "bg-positive-bg text-positive",
    text: "Cash runway holds at 14.2 months if we defer the robotics line capex to Q4.",
  },
  {
    agent: "FP&A",
    tone: "bg-info-bg text-info",
    text: "The case is forecastable only if proposal conversion holds above 34% and CAC payback stays under 9 months.",
  },
  {
    agent: "Risk",
    tone: "bg-warning-bg text-warning",
    text: "Condition this until AUD-21 is closed, the approval route is signed, and source provenance matches the forecast.",
  },
  {
    agent: "Procurement",
    tone: "bg-surface-muted text-muted-foreground",
    text: "Ask for a 14% renewal cap, SLA credits, and month-to-month termination before the 45-day notice window closes.",
  },
] as const;

const STATS = [
  { value: 4, suffix: "", label: "Committee seats", decimals: 0 },
  { value: 14.2, suffix: " mo", label: "Demo runway", decimals: 1 },
  { value: 100, suffix: "%", label: "Runway from live cash", decimals: 0 },
] as const;

function useInView(ref: RefObject<HTMLElement | null>) {
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        setInView(true);
        obs.disconnect();
      }
    }, { threshold: 0.35 });
    obs.observe(el);
    return () => obs.disconnect();
  }, [ref]);
  return inView;
}

function AnimatedStat({
  value,
  suffix,
  decimals,
  label,
  active,
}: {
  value: number;
  suffix: string;
  decimals: number;
  label: string;
  active: boolean;
}) {
  const mounted = useMounted();
  const reduced = Boolean(useReducedMotion());
  const count = useMotionValue(0);
  const display = useTransform(count, (v) =>
    decimals > 0 ? v.toFixed(decimals) : String(Math.round(v)),
  );

  useEffect(() => {
    if (!active) return;
    if (reduced) {
      count.set(value);
      return;
    }
    const controls = animate(count, value, { duration: motionDuration.count, ease: [0, 0, 0.2, 1] });
    return () => controls.stop();
  }, [active, value, count, reduced]);

  const fallback =
    decimals > 0 ? value.toFixed(decimals) : String(Math.round(value));

  const inner = (
    <>
      <dt className="font-mono text-[22px] font-semibold tabular-nums text-foreground sm:text-[26px]">
        {mounted ? <motion.span>{display}</motion.span> : fallback}
        {suffix}
      </dt>
      <dd className="mt-1 text-[11px] font-medium uppercase tracking-[0.1em] text-subtle-foreground">
        {label}
      </dd>
    </>
  );

  if (!mounted) return <div>{inner}</div>;

  return (
    <motion.div whileHover={reduced ? undefined : hoverLiftStrong} transition={springSnappy}>
      {inner}
    </motion.div>
  );
}

export function LandingPage() {
  const mounted = useMounted();
  const reduced = Boolean(useReducedMotion());
  const [activeStep, setActiveStep] = useState(0);
  const [selectedAgent, setSelectedAgent] = useState<string>("treasury");
  const [previewIdx, setPreviewIdx] = useState(0);
  const statsRef = useRef<HTMLDListElement>(null);
  const statsInView = useInView(statsRef);

  const cyclePreview = useCallback(() => {
    setPreviewIdx((i) => (i + 1) % PREVIEW_LINES.length);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(cyclePreview, 4200);
    return () => window.clearInterval(timer);
  }, [cyclePreview]);

  const selected = AGENTS.find((a) => a.id === selectedAgent) ?? AGENTS[0];

  return (
    <div className="landing-root min-h-dvh overflow-x-clip bg-background text-foreground">
      <div className="landing-grain pointer-events-none fixed inset-0 z-50" aria-hidden />

      <header className="landing-nav sticky top-0 z-40 border-b border-border/80 bg-surface/95 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-5 sm:px-8">
          <Link href="/" className="group flex items-center gap-2.5 transition-opacity hover:opacity-80">
            <AtlasLogo />
            <span className="font-display text-[22px] font-medium leading-none tracking-[-0.02em] text-foreground">
              {APP_NAME}
            </span>
          </Link>

          <nav className="ml-auto hidden items-center gap-8 font-sans text-[13px] font-medium text-muted-foreground md:flex">
            <a href="#how" className="transition-colors hover:text-foreground">
              How it works
            </a>
            <a href="#council" className="transition-colors hover:text-foreground">
              The committee
            </a>
            <a href="#product" className="transition-colors hover:text-foreground">
              Preview
            </a>
          </nav>

          <div className="ml-auto flex items-center gap-2 md:ml-0">
            <MotionLink
              href="/dashboard"
              variant="landing-cta"
              className="landing-cta inline-flex h-10 items-center gap-2 rounded-full border border-border-strong bg-surface px-5 font-sans text-[13px] font-semibold text-foreground shadow-sm"
            >
              Dashboard
              <ArrowRight className="h-4 w-4" strokeWidth={2.25} />
            </MotionLink>
          </div>
        </div>
      </header>

      <main>
        <section className="relative mx-auto max-w-6xl px-5 pb-20 pt-14 sm:px-8 sm:pt-20 lg:pb-28">
          <div className="landing-glow landing-glow--hero pointer-events-none absolute -right-24 top-0 h-[420px] w-[420px] rounded-full" />
          <div className="landing-glow landing-glow--green pointer-events-none absolute -left-16 bottom-0 h-[320px] w-[320px] rounded-full" />

          <div className="grid items-center gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-10">
            <Stagger className="relative z-10">
              <StaggerItem className="mb-6">
                <span className="kicker text-[11px]">Runway decisions</span>
              </StaggerItem>

              <StaggerItem>
                <h1 className="headline font-display text-[clamp(2.35rem,5.5vw,3.85rem)] font-medium text-foreground">
                  The room where your
                  <span className="block text-positive">books get debated.</span>
                </h1>
              </StaggerItem>

              <StaggerItem>
                <p className="lede mt-6 max-w-xl text-[17px]">
                  Drop in ledger and vendor files. Four finance specialists argue the numbers in the
                  open. You walk out with a signed recommendation and a memo the board can read.
                </p>
              </StaggerItem>

              <StaggerItem className="mt-9 flex flex-wrap items-center gap-3">
                <MotionLink
                  href="/dashboard"
                  variant="landing-cta"
                  className="landing-cta inline-flex h-12 items-center gap-2.5 rounded-full bg-accent px-6 font-sans text-[14px] font-semibold text-accent-foreground shadow-[0_8px_28px_color-mix(in_srgb,var(--accent)_28%,transparent)]"
                >
                  Open dashboard
                  <ArrowRight className="h-4 w-4" strokeWidth={2.25} />
                </MotionLink>
                <MotionLink
                  href="/decisions"
                  variant="landing-ghost"
                  className="inline-flex h-12 items-center gap-2 rounded-full border border-border bg-surface px-6 font-sans text-[14px] font-semibold text-muted-foreground transition-colors hover:border-border-strong hover:bg-surface-muted hover:text-foreground"
                >
                  Run a live debate
                </MotionLink>
              </StaggerItem>

              <StaggerItem>
                <dl
                  ref={statsRef}
                  className="mt-12 grid grid-cols-3 gap-4 border-t border-border pt-8"
                >
                  {STATS.map((stat) => (
                    <AnimatedStat
                      key={stat.label}
                      value={stat.value}
                      suffix={stat.suffix}
                      decimals={stat.decimals}
                      label={stat.label}
                      active={statsInView}
                    />
                  ))}
                </dl>
              </StaggerItem>
            </Stagger>

            <div id="product" className="landing-fade-up landing-delay-3 relative z-10">
              <ProductPreview
                activeIdx={previewIdx}
                onSelect={setPreviewIdx}
                mounted={mounted}
              />
            </div>
          </div>
        </section>

        <section id="how" className="border-t border-border bg-surface-quiet py-20 sm:py-24">
          <div className="mx-auto max-w-6xl px-5 sm:px-8">
            <div className="max-w-2xl">
              <p className="kicker text-[11px]">Workflow</p>
              <h2 className="mt-3 font-display text-[clamp(1.65rem,3.5vw,2.5rem)] font-medium tracking-[-0.02em] text-foreground">
                From file drop to board memo
              </h2>
              <p className="lede mt-4 max-w-lg text-[16px]">
                Three steps. No prompt engineering — just the files your team already maintains.
              </p>
            </div>

            <div className="mt-14 grid gap-5 md:grid-cols-3">
              {STEPS.map((step, i) => {
                const isActive = activeStep === i;
                return (
                  <motion.button
                    key={step.n}
                    type="button"
                    onClick={() => setActiveStep(i)}
                    className={cx(
                      "landing-card group relative overflow-hidden rounded-xl border p-6 text-left transition-all",
                      isActive
                        ? "border-positive/40 bg-surface shadow-[0_12px_32px_color-mix(in_srgb,var(--positive)_12%,transparent)]"
                        : "border-border bg-surface hover:border-border-strong hover:bg-surface-muted/60",
                    )}
                    whileHover={reduced ? undefined : hoverLift}
                    whileTap={reduced ? undefined : pressSubtle}
                    transition={springSnappy}
                    aria-pressed={isActive}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="folio text-[12px]">{step.n}</span>
                      <AtlasIcon name={step.icon} size="md" />
                    </div>
                    <h3 className="mt-4 font-display text-[20px] font-medium text-foreground">
                      {step.title}
                    </h3>
                    <AnimatePresence mode="wait">
                      {isActive ? (
                        <motion.p
                          key="detail"
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          exit={{ opacity: 0 }}
                          transition={transitionFade}
                          className="mt-3 font-serif text-[14px] leading-relaxed text-muted-foreground"
                        >
                          {step.detail}
                        </motion.p>
                      ) : (
                        <motion.p
                          key="teaser"
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          className="mt-3 font-sans text-[13px] text-subtle-foreground"
                        >
                          Click to read more
                        </motion.p>
                      )}
                    </AnimatePresence>
                    <div
                      className={cx(
                        "pointer-events-none absolute -bottom-10 -right-10 h-28 w-28 rounded-full transition-opacity",
                        step.accent,
                        isActive ? "opacity-100" : "opacity-0 group-hover:opacity-60",
                      )}
                    />
                  </motion.button>
                );
              })}
            </div>
          </div>
        </section>

        <section id="council" className="relative py-20 sm:py-24">
          <div className="landing-glow landing-glow--council pointer-events-none absolute left-1/2 top-1/2 h-[500px] w-[500px] -translate-x-1/2 -translate-y-1/2 rounded-full" />
          <div className="relative mx-auto max-w-6xl px-5 sm:px-8">
            <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-xl">
                <p className="kicker text-[11px] text-positive">The committee</p>
                <h2 className="mt-3 font-display text-[clamp(1.65rem,3.5vw,2.5rem)] font-medium tracking-[-0.02em] text-foreground">
                  Four specialists, one recommendation
                </h2>
                <p className="lede mt-4 text-[16px]">
                  Each seat owns a lane — liquidity, planning, controls, spend. They push back on
                  each other before the CFO writes the final call.
                </p>
              </div>
              <Link
                href="/department"
                className="inline-flex h-11 shrink-0 items-center gap-2 self-start rounded-full border border-border bg-surface px-5 font-sans text-[13px] font-semibold text-muted-foreground transition-colors hover:border-positive/30 hover:bg-positive-bg hover:text-positive lg:self-auto"
              >
                See the full roster
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>

            <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {AGENTS.map((agent) => {
                const isSelected = selectedAgent === agent.id;
                return (
                  <motion.button
                    key={agent.id}
                    type="button"
                    onClick={() => setSelectedAgent(agent.id)}
                    className={cx(
                      "landing-card rounded-xl border p-5 text-left transition-all",
                      isSelected
                        ? "border-positive/45 bg-positive-bg shadow-sm"
                        : "border-border bg-surface hover:border-border-strong",
                    )}
                    whileHover={reduced ? undefined : hoverLiftStrong}
                    whileTap={reduced ? undefined : pressSubtle}
                    transition={springSnappy}
                    aria-pressed={isSelected}
                  >
                    <AtlasIcon name={agent.icon} size="sm" className="mb-4" />
                    <div className="font-display text-[17px] font-medium text-foreground">
                      {agent.label}
                    </div>
                    <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-subtle-foreground">
                      {agent.role}
                    </div>
                    <AnimatePresence>
                      {isSelected && (
                        <motion.p
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          exit={{ opacity: 0 }}
                          transition={transitionFade}
                          className="mt-3 font-serif text-[13px] leading-relaxed text-muted-foreground"
                        >
                          {agent.blurb}
                        </motion.p>
                      )}
                    </AnimatePresence>
                  </motion.button>
                );
              })}
            </div>

            <motion.div
              key={selected.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="mt-6 flex items-start gap-3 rounded-xl border border-positive/20 bg-positive-bg/60 px-4 py-3"
            >
              <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-positive" strokeWidth={2.5} />
              <p className="font-serif text-[14px] leading-relaxed text-muted-foreground">
                <span className="font-sans font-semibold text-positive">{selected.label}</span>
                {" — "}
                {selected.blurb}
              </p>
            </motion.div>
          </div>
        </section>

        <section className="border-t border-border bg-surface-quiet py-20">
          <div className="mx-auto max-w-6xl px-5 text-center sm:px-8">
            <h2 className="font-display text-[clamp(1.85rem,4.5vw,2.85rem)] font-medium tracking-[-0.02em] text-foreground">
              When the board asks why you said yes,
              <span className="block text-muted-foreground">you&apos;ll have the memo.</span>
            </h2>
            <p className="lede mx-auto mt-5 max-w-lg text-[16px]">
              Load a demo company, run a committee debate, and export the recommendation with the
              numbers attached.
            </p>
            <MotionLink
              href="/dashboard"
              variant="landing-cta"
              className="landing-cta mt-10 inline-flex h-12 items-center gap-2.5 rounded-full border border-border-strong bg-surface px-8 font-sans text-[15px] font-semibold text-foreground shadow-sm"
            >
              Enter the dashboard
              <ArrowRight className="h-4 w-4" strokeWidth={2.25} />
            </MotionLink>
          </div>
        </section>
      </main>

      <footer className="border-t border-border py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-5 font-sans text-[12px] text-subtle-foreground sm:flex-row sm:px-8">
          <div className="flex items-center gap-2">
            <AtlasLogo className="h-5 w-5 opacity-80" />
            <span>{APP_NAME} · WeaveHacks 4</span>
          </div>
          <div className="flex gap-6">
            <Link href="/dashboard" className="transition-colors hover:text-foreground">
              Dashboard
            </Link>
            <Link href="/decisions" className="transition-colors hover:text-foreground">
              Council
            </Link>
            <Link href="/activity" className="transition-colors hover:text-foreground">
              Activity
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

function AtlasLogo({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M4.6 27.6 15.3 3.4c.35-.78 1.45-.78 1.79 0l10.31 23.9c.35.82-.52 1.62-1.49 1.08l-6.47-3.57H11.9l-6.03 3.53c-.91.53-1.63-.48-1.27-1.14Zm8.18-7.62h5.59l-2.76-8.04-2.83 8.04Z"
        fill="var(--accent)"
      />
    </svg>
  );
}

function ProductPreview({
  activeIdx,
  onSelect,
  mounted,
}: {
  activeIdx: number;
  onSelect: (idx: number) => void;
  mounted: boolean;
}) {
  const reduced = Boolean(useReducedMotion());
  const line = PREVIEW_LINES[activeIdx];

  return (
    <div className="landing-float relative mx-auto w-full max-w-[520px]">
      <div className="absolute -left-6 top-8 z-0 h-[88%] w-[88%] rounded-2xl border border-positive/20 bg-positive-bg/40" />
      <div className="command-surface relative overflow-hidden rounded-xl shadow-[var(--shadow-soft)]">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-accent/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-warning/70" />
            <span className="h-2.5 w-2.5 rounded-full bg-positive/80" />
          </div>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-subtle-foreground">
            Council · live
          </span>
        </div>

        <div className="flex gap-1 border-b border-border bg-surface-quiet px-3 py-2">
          {PREVIEW_LINES.map((row, i) => (
            <button
              key={row.agent}
              type="button"
              onClick={() => onSelect(i)}
              className={cx(
                "rounded-md px-2 py-1 font-mono text-[9px] font-medium uppercase tracking-[0.08em] transition-colors",
                i === activeIdx
                  ? "bg-positive text-surface"
                  : "text-subtle-foreground hover:bg-surface-muted hover:text-foreground",
              )}
            >
              {row.agent.split(" ")[0]}
            </button>
          ))}
        </div>

        <div className="space-y-3 p-4">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeIdx}
              initial={reduced ? { opacity: 0 } : { opacity: 0, x: 12 }}
              animate={reduced ? { opacity: 1 } : { opacity: 1, x: 0 }}
              exit={reduced ? { opacity: 0 } : { opacity: 0, x: -8 }}
              transition={transitionReveal}
              className="rounded-lg border border-border bg-surface-quiet p-3"
            >
              <span
                className={cx(
                  "inline-block rounded-md px-2 py-0.5 font-mono text-[10px] font-semibold",
                  line.tone,
                )}
              >
                {line.agent}
              </span>
              <p className="mt-2 font-serif text-[13px] leading-relaxed text-muted-foreground">
                {line.text}
              </p>
            </motion.div>
          </AnimatePresence>

          <div className="landing-shimmer rounded-lg border border-positive/25 bg-gradient-to-br from-positive-bg to-surface p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-accent">
                CFO recommendation
              </span>
              {mounted ? (
                <span className="landing-pulse rounded-full bg-positive-bg px-2 py-0.5 font-mono text-[10px] font-medium text-positive">
                  +2.1 mo runway
                </span>
              ) : (
                <span className="rounded-full bg-positive-bg px-2 py-0.5 font-mono text-[10px] text-positive">
                  +2.1 mo runway
                </span>
              )}
            </div>
            <p className="mt-2 font-display text-[15px] leading-snug text-foreground">
              Approve deferred capex with a renegotiated sensor contract. Board memo is ready.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-px border-t border-border bg-border">
          {[
            { label: "Runway", value: "14.2mo", tone: "text-positive" },
            { label: "Evidence", value: "6 cites", tone: "text-foreground" },
            { label: "Status", value: "live", tone: "text-info" },
          ].map((m) => (
            <div key={m.label} className="bg-surface px-3 py-3 text-center">
              <div className={cx("font-mono text-[13px] font-semibold tabular-nums", m.tone)}>
                {m.value}
              </div>
              <div className="mt-0.5 text-[9px] font-medium uppercase tracking-[0.12em] text-subtle-foreground">
                {m.label}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

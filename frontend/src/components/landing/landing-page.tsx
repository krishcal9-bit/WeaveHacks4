"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, BarChart3, Scale, Shield, Sparkles, Wallet } from "lucide-react";
import { MotionLink } from "@/components/motion/motion-link";
import { Stagger, StaggerItem } from "@/components/motion/stagger";
import { springSnappy } from "@/components/motion/variants";
import { ThemeToggle } from "@/components/theme-toggle";
import { useMounted } from "@/lib/use-mounted";

const STEPS = [
  {
    n: "01",
    title: "Drop your books",
    detail: "Ledger, invoices, vendors, pipeline — Atlas ingests live files and reconciles them in seconds.",
  },
  {
    n: "02",
    title: "Council debates",
    detail: "Treasury, FP&A, Risk, and Procurement argue grounded positions. Every claim ties back to your data.",
  },
  {
    n: "03",
    title: "CFO decides",
    detail: "A quantified recommendation lands with runway impact, evidence, and a board-ready memo.",
  },
];

const AGENTS = [
  { id: "treasury", label: "Treasury", icon: Wallet, tone: "text-[#7ec8a8]" },
  { id: "fpna", label: "FP&A", icon: BarChart3, tone: "text-[#8eb5ff]" },
  { id: "risk", label: "Risk & Audit", icon: Shield, tone: "text-[#f0b27a]" },
  { id: "procurement", label: "Procurement", icon: Scale, tone: "text-[#e8a0bf]" },
];

export function LandingPage() {
  const mounted = useMounted();

  return (
    <div className="landing-root min-h-dvh overflow-x-clip">
      <div className="landing-grain pointer-events-none fixed inset-0 z-50 opacity-[0.35]" aria-hidden />

      <header className="landing-nav sticky top-0 z-40 border-b border-white/[0.06] bg-[#0e0d0b]/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-5 sm:px-8">
          <Link href="/" className="group flex items-center gap-2.5">
            <AtlasLogo />
            <span className="font-display text-[17px] font-medium tracking-tight text-[#f6f0e6]">Atlas</span>
          </Link>

          <nav className="ml-auto hidden items-center gap-8 text-[13px] font-medium text-[#a39e94] md:flex">
            <a href="#how" className="transition-colors hover:text-[#f6f0e6]">
              How it works
            </a>
            <a href="#council" className="transition-colors hover:text-[#f6f0e6]">
              The council
            </a>
            <a href="#product" className="transition-colors hover:text-[#f6f0e6]">
              Product
            </a>
          </nav>

          <div className="ml-auto flex items-center gap-2 md:ml-0">
            <ThemeToggle variant="landing" />
            <MotionLink
              href="/dashboard"
              variant="landing-cta"
              className="landing-cta inline-flex h-10 items-center gap-2 rounded-full bg-[#f6f0e6] px-5 text-[13px] font-semibold text-[#12100e]"
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

          <div className="grid items-center gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-10">
            <Stagger className="relative z-10">
              <StaggerItem className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[12px] font-medium text-[#c9c2b6]">
                {mounted ? (
                  <motion.span animate={{ rotate: [0, 8, -8, 0] }} transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}>
                    <Sparkles className="h-3.5 w-3.5 text-[#e0633a]" strokeWidth={2} />
                  </motion.span>
                ) : (
                  <span>
                    <Sparkles className="h-3.5 w-3.5 text-[#e0633a]" strokeWidth={2} />
                  </span>
                )}
                Autonomous finance operations
              </StaggerItem>

              <StaggerItem>
                <h1 className="font-display text-[clamp(2.5rem,6vw,4.25rem)] font-medium leading-[1.02] tracking-[-0.03em] text-[#f6f0e6]">
                  The finance department
                  <span className="block text-[#e0633a]">that never sleeps.</span>
                </h1>
              </StaggerItem>

              <StaggerItem>
                <p className="mt-6 max-w-xl text-[16px] leading-relaxed text-[#9f978a] sm:text-[17px]">
                  Atlas is an AI council for runway-critical decisions. Upload your books, watch four specialists
                  debate live, and walk away with a CFO-grade recommendation — traced, grounded, board-ready.
                </p>
              </StaggerItem>

              <StaggerItem className="mt-9 flex flex-wrap items-center gap-3">
                <MotionLink
                  href="/dashboard"
                  variant="landing-cta"
                  className="landing-cta inline-flex h-12 items-center gap-2.5 rounded-full bg-[#e0633a] px-6 text-[14px] font-semibold text-[#fff8f4] shadow-[0_0_40px_rgba(224,99,58,0.35)]"
                >
                  Open dashboard
                  <ArrowRight className="h-4 w-4" strokeWidth={2.25} />
                </MotionLink>
                <MotionLink
                  href="/decisions"
                  variant="landing-ghost"
                  className="inline-flex h-12 items-center gap-2 rounded-full border border-white/12 bg-white/[0.03] px-6 text-[14px] font-semibold text-[#d8d0c4] transition-colors hover:border-white/20 hover:bg-white/[0.06]"
                >
                  Jump to council room
                </MotionLink>
              </StaggerItem>

              <StaggerItem className="mt-12 grid grid-cols-3 gap-4 border-t border-white/[0.08] pt-8">
                {[
                  { k: "4", v: "Specialist agents" },
                  { k: "Live", v: "Weave-traced runs" },
                  { k: "0", v: "Hallucinated runway" },
                ].map((stat) => {
                  const inner = (
                    <>
                      <dt className="font-mono text-[22px] font-medium tabular-nums text-[#f6f0e6] sm:text-[26px]">{stat.k}</dt>
                      <dd className="mt-1 text-[11px] uppercase tracking-[0.12em] text-[#7a7368]">{stat.v}</dd>
                    </>
                  );
                  return mounted ? (
                    <motion.div key={stat.v} whileHover={{ y: -3 }} transition={springSnappy}>
                      {inner}
                    </motion.div>
                  ) : (
                    <div key={stat.v}>{inner}</div>
                  );
                })}
              </StaggerItem>
            </Stagger>

            <div id="product" className="landing-fade-up landing-delay-3 relative z-10">
              <ProductPreview />
            </div>
          </div>
        </section>

        <section id="how" className="border-t border-white/[0.06] bg-[#0a0908] py-20 sm:py-24">
          <div className="mx-auto max-w-6xl px-5 sm:px-8">
            <div className="max-w-2xl">
              <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-[#e0633a]">Workflow</p>
              <h2 className="mt-3 font-display text-[clamp(1.75rem,4vw,2.75rem)] font-medium tracking-[-0.02em] text-[#f6f0e6]">
                From raw files to a signed-off decision
              </h2>
            </div>

            <div className="mt-14 grid gap-5 md:grid-cols-3">
              {STEPS.map((step, i) => (
                <article
                  key={step.n}
                  className="landing-card group relative overflow-hidden rounded-2xl border border-white/[0.08] bg-[#141210] p-6 transition-colors hover:border-white/[0.14]"
                  style={{ animationDelay: `${i * 120}ms` }}
                >
                  <div className="font-mono text-[12px] text-[#e0633a]">{step.n}</div>
                  <h3 className="mt-4 font-display text-[22px] font-medium text-[#f6f0e6]">{step.title}</h3>
                  <p className="mt-3 text-[14px] leading-relaxed text-[#8f877a]">{step.detail}</p>
                  <div className="pointer-events-none absolute -bottom-8 -right-8 h-32 w-32 rounded-full bg-[#e0633a]/10 blur-2xl transition-opacity group-hover:opacity-100" />
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="council" className="relative py-20 sm:py-24">
          <div className="landing-glow landing-glow--council pointer-events-none absolute left-1/2 top-1/2 h-[500px] w-[500px] -translate-x-1/2 -translate-y-1/2 rounded-full" />
          <div className="relative mx-auto max-w-6xl px-5 sm:px-8">
            <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-xl">
                <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-[#7ec8a8]">The committee</p>
                <h2 className="mt-3 font-display text-[clamp(1.75rem,4vw,2.75rem)] font-medium tracking-[-0.02em] text-[#f6f0e6]">
                  Four voices. One quantified verdict.
                </h2>
                <p className="mt-4 text-[15px] leading-relaxed text-[#8f877a]">
                  Each agent owns a lane — liquidity, planning, controls, spend. They cross-examine each other
                  before the CFO synthesizes a recommendation your board can actually use.
                </p>
              </div>
              <Link
                href="/department"
                className="inline-flex h-11 shrink-0 items-center gap-2 self-start rounded-full border border-white/10 px-5 text-[13px] font-semibold text-[#d8d0c4] transition-colors hover:bg-white/[0.04] lg:self-auto"
              >
                Meet the roster
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>

            <Stagger fast className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {AGENTS.map((agent) => {
                const Icon = agent.icon;
                return (
                  <StaggerItem
                    key={agent.id}
                    className="landing-card rounded-2xl border border-white/[0.08] bg-[#12100e]/80 p-5 backdrop-blur-sm"
                  >
                    <div className={`mb-4 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.04] ${agent.tone}`}>
                      <Icon className="h-5 w-5" strokeWidth={1.75} />
                    </div>
                    <div className="font-display text-[18px] font-medium text-[#f6f0e6]">{agent.label}</div>
                    <div className="mt-2 font-mono text-[11px] text-[#6d665c]">atlas://agent/{agent.id}</div>
                  </StaggerItem>
                );
              })}
            </Stagger>
          </div>
        </section>

        <section className="border-t border-white/[0.06] bg-[#0a0908] py-20">
          <div className="mx-auto max-w-6xl px-5 text-center sm:px-8">
            <h2 className="font-display text-[clamp(2rem,5vw,3.25rem)] font-medium tracking-[-0.03em] text-[#f6f0e6]">
              Ready when the board asks
              <span className="block text-[#9f978a]">why you said yes.</span>
            </h2>
            <p className="mx-auto mt-5 max-w-lg text-[15px] leading-relaxed text-[#8f877a]">
              Load any demo folder, run a live council debate, and export a trace-backed recommendation.
            </p>
            <MotionLink
              href="/dashboard"
              variant="landing-cta"
              className="landing-cta mt-10 inline-flex h-12 items-center gap-2.5 rounded-full bg-[#f6f0e6] px-8 text-[15px] font-semibold text-[#12100e]"
            >
              Enter the dashboard
              <ArrowRight className="h-4 w-4" strokeWidth={2.25} />
            </MotionLink>
          </div>
        </section>
      </main>

      <footer className="border-t border-white/[0.06] py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-5 text-[12px] text-[#6d665c] sm:flex-row sm:px-8">
          <div className="flex items-center gap-2">
            <AtlasLogo className="h-5 w-5 opacity-70" />
            <span>Atlas Finance OS · WeaveHacks 4</span>
          </div>
          <div className="flex gap-6">
            <Link href="/dashboard" className="transition-colors hover:text-[#c9c2b6]">
              Dashboard
            </Link>
            <Link href="/decisions" className="transition-colors hover:text-[#c9c2b6]">
              Council
            </Link>
            <Link href="/activity" className="transition-colors hover:text-[#c9c2b6]">
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
        fill="#e0633a"
      />
    </svg>
  );
}

function ProductPreview() {
  return (
    <div className="landing-float relative mx-auto w-full max-w-[520px]">
      <div className="absolute -left-6 top-8 z-0 h-[88%] w-[88%] rounded-3xl border border-[#e0633a]/20 bg-[#e0633a]/5 blur-sm" />
      <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-[#161412] shadow-[0_32px_80px_rgba(0,0,0,0.55)]">
        <div className="flex items-center justify-between border-b border-white/[0.08] px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-[#e0633a]/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#f0b27a]/60" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#7ec8a8]/60" />
          </div>
          <span className="font-mono text-[10px] text-[#6d665c]">atlas://council/live</span>
        </div>

        <div className="space-y-3 p-4">
          <PreviewRow agent="Treasury" tone="bg-[#7ec8a8]/15 text-[#7ec8a8]" delay="0ms">
            Cash runway holds at 14.2 months if we defer the robotics line capex to Q4.
          </PreviewRow>
          <PreviewRow agent="FP&A" tone="bg-[#8eb5ff]/15 text-[#8eb5ff]" delay="80ms">
            Scenario B preserves 8% headcount buffer while hitting the pipeline target.
          </PreviewRow>
          <PreviewRow agent="Risk" tone="bg-[#f0b27a]/15 text-[#f0b27a]" delay="160ms">
            Vendor concentration exceeds policy at 34% — need a mitigation clause.
          </PreviewRow>
          <PreviewRow agent="Procurement" tone="bg-[#e8a0bf]/15 text-[#e8a0bf]" delay="240ms">
            Renegotiating the sensor contract saves $420K without slipping delivery.
          </PreviewRow>

          <div className="landing-shimmer mt-4 rounded-xl border border-[#e0633a]/25 bg-gradient-to-br from-[#e0633a]/12 to-transparent p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-[#e0633a]">CFO synthesis</span>
              <span className="rounded-full bg-[#7ec8a8]/20 px-2 py-0.5 font-mono text-[10px] text-[#7ec8a8]">+2.1 mo runway</span>
            </div>
            <p className="mt-2 font-display text-[15px] leading-snug text-[#f6f0e6]">
              Approve deferred capex with procurement renegotiation. Board memo ready.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-px border-t border-white/[0.08] bg-white/[0.04]">
          {[
            { label: "Runway", value: "14.2mo" },
            { label: "Confidence", value: "94%" },
            { label: "Trace", value: "live" },
          ].map((m) => (
            <div key={m.label} className="bg-[#12100e] px-3 py-3 text-center">
              <div className="font-mono text-[13px] font-medium tabular-nums text-[#f6f0e6]">{m.value}</div>
              <div className="mt-0.5 text-[9px] uppercase tracking-[0.14em] text-[#6d665c]">{m.label}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function PreviewRow({
  agent,
  tone,
  delay,
  children,
}: {
  agent: string;
  tone: string;
  delay: string;
  children: ReactNode;
}) {
  return (
    <div
      className="landing-msg rounded-xl border border-white/[0.06] bg-[#1a1815] p-3"
      style={{ animationDelay: delay }}
    >
      <span className={`inline-block rounded-md px-2 py-0.5 font-mono text-[10px] font-medium ${tone}`}>{agent}</span>
      <p className="mt-2 text-[12.5px] leading-relaxed text-[#b8b0a4]">{children}</p>
    </div>
  );
}

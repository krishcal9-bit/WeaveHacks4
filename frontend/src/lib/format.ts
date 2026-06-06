// Formatting helpers — institutional, tabular-friendly.

export function fmtUSD(
  n: number | undefined | null,
  opts: { compact?: boolean } = {},
): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (opts.compact) {
    const abs = Math.abs(n);
    if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(abs >= 10_000_000 ? 1 : 2)}M`;
    if (abs >= 1_000) return `$${Math.round(n / 1_000)}K`;
    return `$${Math.round(n)}`;
  }
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function fmtMonths(n?: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)} mo`;
}

export function fmtPct(n?: number | null, digits = 0): string {
  return n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
}

export function fmtSignedMonths(n?: number | null): string {
  if (n == null) return "—";
  const s = n > 0 ? "+" : "";
  return `${s}${n.toFixed(1)} mo`;
}

export function fmtMonthLabel(m: string): string {
  // "2026-06" → "Jun ’26"
  const [y, mo] = m.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const idx = Number(mo) - 1;
  return `${names[idx] ?? mo} ’${y.slice(2)}`;
}

// Compact integer with thousands separators, or — when absent.
export function fmtInt(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  return Math.round(n).toLocaleString("en-US");
}

// Compact large counts: 1_234 → "1.2K", 1_200_000 → "1.2M".
export function fmtCompact(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`;
  return String(Math.round(n));
}

// Single-line truncation that never breaks layout.
export function truncate(value: string | undefined | null, max = 120): string {
  if (!value) return "";
  const trimmed = value.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1)}…` : trimmed;
}

// "outcome_accuracy" → "Outcome Accuracy"
export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .trim();
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function toDate(value: string | Date | null | undefined): Date | null {
  if (value == null) return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

// Fractional months between two dates. Accepts "2026-08", "2026-08-01", or a Date.
export function monthsBetween(
  from: string | Date | null | undefined,
  to: string | Date | null | undefined,
): number {
  const a = toDate(from);
  const b = toDate(to);
  if (!a || !b) return NaN;
  const avgMonthMs = (365.25 / 12) * 24 * 60 * 60 * 1000;
  return (b.getTime() - a.getTime()) / avgMonthMs;
}

// "2026-08-01" → "Aug 1, 2026"; "2026-08" → "Aug 2026".
export function fmtDate(value?: string | Date | null): string {
  const d = toDate(value);
  if (!d) return "—";
  const monthOnly = typeof value === "string" && /^\d{4}-\d{2}$/.test(value.trim());
  const month = MONTH_NAMES[d.getUTCMonth()];
  return monthOnly
    ? `${month} ${d.getUTCFullYear()}`
    : `${month} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

export function fmtMultiple(n?: number | null, digits = 1): string {
  return n == null || Number.isNaN(n) || !Number.isFinite(n) ? "—" : `${n.toFixed(digits)}×`;
}

// Relative months → "now", "in 1.9 mo", "2.4 mo ago".
export function fmtRelMonths(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (Math.abs(n) < 0.05) return "now";
  return n > 0 ? `in ${n.toFixed(1)} mo` : `${Math.abs(n).toFixed(1)} mo ago`;
}

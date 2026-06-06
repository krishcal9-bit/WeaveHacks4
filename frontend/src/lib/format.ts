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

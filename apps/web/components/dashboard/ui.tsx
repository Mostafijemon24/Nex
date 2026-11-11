import type { ReactNode } from "react";

export type Tone = "green" | "red" | "amber" | "gray" | "brand";

export function Badge({ tone, children, small }: { tone: Tone; children: ReactNode; small?: boolean }) {
  return <span className={`badge ${tone}${small ? " sm" : ""}`}>{children}</span>;
}

export function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

/** Map any backend status string to a badge colour and a readable label. */
export function statusTone(status: string): { tone: Tone; label: string } {
  const s = (status || "").toLowerCase();
  const label = s ? s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "Unknown";
  if (["connected", "completed", "verified", "passed", "accessible", "ready", "draft", "publish", "future", "ok"].includes(s)) return { tone: "green", label };
  if (["failed", "error", "blocked", "cancelled", "inaccessible"].includes(s)) return { tone: "red", label };
  if (["running", "queued", "retrying", "testing"].includes(s)) return { tone: "brand", label };
  if (["paused", "untested", "completed_with_warnings", "partially_verified", "needs_review", "warning", "skipped"].includes(s)) return { tone: "amber", label };
  return { tone: "gray", label };
}

export function StatusBadge({ status, small }: { status: string; small?: boolean }) {
  const { tone, label } = statusTone(status);
  return <Badge tone={tone} small={small}>{label}</Badge>;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function shortPath(url: string) {
  try {
    const parsed = new URL(url);
    return parsed.pathname + parsed.search || "/";
  } catch {
    return url;
  }
}

export function host(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

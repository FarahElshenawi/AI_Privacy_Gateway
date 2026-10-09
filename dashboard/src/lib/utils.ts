import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a number with thousands separators. */
export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

/**
 * Parse an ISO timestamp from the cloud backend as UTC. Older responses carry no zone
 * ("2026-10-09T10:00:00"), which JavaScript would read as LOCAL time and shift by the UTC offset.
 */
export function parseUtc(iso: string): Date {
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso);
  return new Date(hasZone ? iso : iso + "Z");
}

/** Format an ISO timestamp as HH:MM:SS. */
export function formatTime(iso: string): string {
  const d = parseUtc(iso);
  return d.toLocaleTimeString("en-US", { hour12: false });
}

/** Format an ISO timestamp as "5m ago" / "2h ago" / "3d ago". */
export function formatRelative(iso: string): string {
  const d = parseUtc(iso);
  const diffMs = Date.now() - d.getTime();
  const m = Math.floor(diffMs / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  return `${days}d ago`;
}

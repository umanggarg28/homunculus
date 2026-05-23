// Pure formatting helpers. No React, no global state.

export function formatTime(iso: string): string {
  return iso.slice(11, 19);
}

export function formatAge(seconds: number | null): string {
  if (seconds === null) return "?";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

export function truncate(s: string, n = 120): string {
  if (!s) return "";
  if (s.length <= n) return s;
  return s.slice(0, n) + "…";
}

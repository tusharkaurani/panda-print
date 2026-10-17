// Formatting helpers. Timestamps from the API are epoch seconds; shown in the viewer's timezone.
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function ago(ts, now = Date.now() / 1000) {
  if (ts == null) return '';
  const s = Math.max(0, now - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${plural(Math.floor(s / 60), 'min')} ago`;
  if (s < 86400) return `${plural(Math.floor(s / 3600), 'hour')} ago`;
  const d = Math.floor(s / 86400);
  return d < 60 ? `${plural(d, 'day')} ago` : `${plural(Math.floor(d / 30), 'month')} ago`;
}

export function abs(ts) {
  if (ts == null) return '';
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function day(ts) {
  return new Date(ts * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

// 2.6 -> "2.6 days", 0.4 -> "10 hours", 1 -> "1 day"
export function days(n) {
  if (n == null) return '–';
  if (n < 1 / 24) return 'under an hour';
  if (n < 1) return plural(Math.round(n * 24), 'hour');
  const r = Math.round(n * 10) / 10;
  return r === 1 ? '1 day' : `${Number.isInteger(r) ? r : r.toFixed(1)} days`;
}

export function countdown(seconds) {
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`;
  return `${seconds}s`;
}

export const num = (n) => (n == null ? '–' : n.toLocaleString());

// 1 -> "minute", 5 -> "5 minutes", 60 -> "hour", 90 -> "1.5 hours" (for "every ...")
export function minutes(n) {
  const trim = (x) => (Number.isInteger(x) ? x : Math.round(x * 10) / 10);
  if (n === 1) return 'minute';
  if (n < 60) return `${trim(n)} minutes`;
  const h = n / 60;
  return h === 1 ? 'hour' : `${trim(h)} hours`;
}

// "Sat 24 Oct, 10:00" (viewer's time zone)
export function when(ts) {
  return new Date(ts * 1000).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

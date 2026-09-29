/** Date formatting in the user's timezone (from the server settings). */

let tz = "Europe/Moscow";
export function setTimezone(zone: string | undefined) {
  if (zone) tz = zone;
}
export function getTimezone() {
  return tz;
}

const cache = new Map<string, Intl.DateTimeFormat>();
function fmt(opts: Intl.DateTimeFormatOptions) {
  const key = JSON.stringify(opts) + tz;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("ru-RU", { timeZone: tz, ...opts });
    cache.set(key, f);
  }
  return f;
}

/** YYYY-MM-DD of a moment in the user's timezone. */
export function localDateKey(d: Date): string {
  const p = fmt({ year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function dayDiff(d: Date, now = new Date()): number {
  const a = Date.parse(localDateKey(d) + "T00:00:00Z");
  const b = Date.parse(localDateKey(now) + "T00:00:00Z");
  return Math.round((a - b) / 86_400_000);
}

export function formatTime(iso: string): string {
  return fmt({ hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

export function formatDay(iso: string, withWeekday = false): string {
  const d = new Date(iso);
  const diff = dayDiff(d);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Завтра";
  if (diff === -1) return "Вчера";
  if (withWeekday && diff > 1 && diff < 7) {
    const w = fmt({ weekday: "long" }).format(d);
    return w.charAt(0).toUpperCase() + w.slice(1);
  }
  const sameYear = localDateKey(d).slice(0, 4) === localDateKey(new Date()).slice(0, 4);
  return fmt(sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" })
    .format(d)
    .replace(".", "");
}

export function formatDateTime(iso: string): string {
  return `${formatDay(iso, true)}, ${formatTime(iso)}`;
}

export function formatDue(dueAt: string | null, hasTime: boolean): string | null {
  if (!dueAt) return null;
  return hasTime ? formatDateTime(dueAt) : formatDay(dueAt, true);
}

export function isOverdue(iso: string | null, dateOnly = false): boolean {
  if (!iso) return false;
  if (dateOnly) return dayDiff(new Date(iso)) < 0;
  return new Date(iso).getTime() < Date.now();
}

export function tzShort(): string {
  const part = fmt({ timeZoneName: "short" })
    .formatToParts(new Date())
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? tz;
}

/** Value for <input type="datetime-local"> -> ISO with the user's offset. */
export function localInputToIso(value: string): string {
  // The backend interprets an ISO value without offset in the user's timezone.
  return value.length === 16 ? `${value}:00` : value;
}

/** Default for datetime-local inputs: next full hour in the user's timezone. */
export function nextHourInput(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  const p = fmt({ year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hour12: false }).formatToParts(d);
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? "00";
  const hour = g("hour") === "24" ? "00" : g("hour");
  return `${g("year")}-${g("month")}-${g("day")}T${hour}:00`;
}

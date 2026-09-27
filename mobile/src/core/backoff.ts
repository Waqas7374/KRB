/**
 * How long to wait before an entry is sent again (docs/06 §5): 2 s, 5 s, 15 s, 60 s, 5 min,
 * 15 min, 1 h, then hourly, each with up to 20 % jitter so a site's phones do not all retry in
 * step. After 72 hours an entry stops retrying and waits for a person; it is never discarded.
 */
export const SCHEDULE_SECONDS = [2, 5, 15, 60, 5 * 60, 15 * 60, 60 * 60] as const;
export const DEAD_AFTER_HOURS = 72;

/** `attempts` is how many times it has already failed (0 for the first retry). */
export function delayMs(attempts: number, random: () => number = Math.random): number {
  const base = SCHEDULE_SECONDS[Math.min(attempts, SCHEDULE_SECONDS.length - 1)]! * 1000;
  const jitter = 1 + (random() * 0.4 - 0.2); // 0.8 .. 1.2
  return Math.round(base * jitter);
}

export function nextAttemptAt(
  now: Date,
  attempts: number,
  random: () => number = Math.random,
): string {
  return new Date(now.getTime() + delayMs(attempts, random)).toISOString();
}

export function isTooOld(createdAt: string, now: Date): boolean {
  return now.getTime() - new Date(createdAt).getTime() > DEAD_AFTER_HOURS * 3600 * 1000;
}

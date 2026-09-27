/**
 * UUIDv7 generated on the device: time-ordered, so records created offline sort the way they
 * were made, and unique without asking the server (docs/06 §3).
 *
 * The random part comes from `crypto.getRandomValues` where the runtime has one. Hermes on
 * React Native 0.74 does not, so there is a `Math.random` fallback: these ids are idempotency
 * keys, not secrets, and 74 random bits plus a millisecond clock is ample for them.
 */

type RandomBytes = (n: number) => Uint8Array;

const defaultRandom: RandomBytes = (n) => {
  const bytes = new Uint8Array(n);
  const c = (globalThis as { crypto?: { getRandomValues?: (a: Uint8Array) => Uint8Array } }).crypto;
  if (c?.getRandomValues) return c.getRandomValues(bytes);
  for (let i = 0; i < n; i++) bytes[i] = Math.floor(Math.random() * 256);
  return bytes;
};

export function uuidv7(now: number = Date.now(), random: RandomBytes = defaultRandom): string {
  const bytes = random(16);
  // 48-bit millisecond timestamp, big-endian.
  let ms = now;
  for (let i = 5; i >= 0; i--) {
    bytes[i] = ms % 256;
    ms = Math.floor(ms / 256);
  }
  bytes[6] = (bytes[6]! & 0x0f) | 0x70; // version 7
  bytes[8] = (bytes[8]! & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

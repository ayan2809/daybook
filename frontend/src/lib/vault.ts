// The recovery key (DB-32): 160 random bits made here, in the browser, shown once, and kept on this
// device so it unlocks itself. Daybook's servers never store it.
import { getItem, KEYS, removeItem, setItem } from "./storage";

const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export function newRecoveryKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(20));
  let bits = 0;
  let value = 0;
  let out = "";
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += B32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  return out.match(/.{4}/g)!.join("-");
}

export const deviceKey = {
  get: () => getItem(KEYS.recoveryKey),
  set: (key: string) => setItem(KEYS.recoveryKey, key),
  forget: () => removeItem(KEYS.recoveryKey),
};

export function downloadKey(key: string, email: string | null): void {
  const text = [
    "Daybook recovery key",
    "",
    key,
    "",
    `Account: ${email ?? "your Google account"}`,
    "Your reflections are encrypted with this key. Daybook doesn't keep a copy, so it can't be reset.",
    "Keep it in your password manager. You'll need it to open Daybook on a new device.",
    "",
  ].join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "daybook-recovery-key.txt";
  a.click();
  URL.revokeObjectURL(url);
}

// One reused <audio> element. iOS only lets audio play after a user gesture, so the first
// "Start reflection" tap plays a tiny silent clip through this element (unlockAudio), and every
// later reply plays through the same element (plan §9.3).
import { api } from "./api";

export type PlayOutcome =
  | "played" // played to the end (or was stopped)
  | "none" // 204: no audio for this reply (voice off / out of budget)
  | "unavailable" // 503: TTS failed → show "voice unavailable"
  | "blocked" // play() was rejected → show "Tap to hear"
  | "cancelled"; // superseded by another play or stopAudio()

type Listener = (speaking: boolean) => void;

let el: HTMLAudioElement | null = null;
let unlocked = false;
let speaking = false;
let token = 0;
let currentUrl: string | null = null;
let fetchAbort: AbortController | null = null;
let finishCurrent: ((o: PlayOutcome) => void) | null = null;
const listeners = new Set<Listener>();
/** Audio of replies whose play() was rejected, kept so "Tap to hear" can replay them. */
const blocked = new Map<string, Blob>();

function element(): HTMLAudioElement {
  if (!el) {
    el = new Audio();
    el.preload = "auto";
    el.setAttribute("playsinline", "");
  }
  return el;
}

function setSpeaking(v: boolean) {
  if (speaking === v) return;
  speaking = v;
  listeners.forEach((fn) => fn(v));
}

export function onSpeakingChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function isSpeaking(): boolean {
  return speaking;
}

export function isUnlocked(): boolean {
  return unlocked;
}

/** A 0.05 s silent 8 kHz 8-bit mono WAV, built in code so no asset is needed. */
function silentWavUrl(): string {
  const samples = 400;
  const buf = new ArrayBuffer(44 + samples);
  const v = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + samples, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, 8000, true);
  v.setUint32(28, 8000, true);
  v.setUint16(32, 1, true);
  v.setUint16(34, 8, true);
  str(36, "data");
  v.setUint32(40, samples, true);
  for (let i = 0; i < samples; i++) v.setUint8(44 + i, 128);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

/** Call synchronously inside a tap handler (e.g. "Start reflection"). Safe to call repeatedly. */
export function unlockAudio(): void {
  if (unlocked) return;
  const a = element();
  const url = silentWavUrl();
  a.src = url;
  a.muted = false;
  const p = a.play();
  unlocked = true;
  p.then(
    () => URL.revokeObjectURL(url),
    () => {
      unlocked = false;
      URL.revokeObjectURL(url);
    },
  );
}

function releaseUrl() {
  if (currentUrl) {
    URL.revokeObjectURL(currentUrl);
    currentUrl = null;
  }
}

/** Stop whatever is playing or loading. Call on navigation away from a voice screen. */
export function stopAudio(): void {
  token++;
  fetchAbort?.abort();
  fetchAbort = null;
  const a = el;
  if (a) {
    a.pause();
    a.removeAttribute("src");
    a.load();
  }
  releaseUrl();
  setSpeaking(false);
  const done = finishCurrent;
  finishCurrent = null;
  done?.("cancelled");
}

/** Play a source through the shared element; resolves when playback ends. */
function playSrc(src: string, myToken: number, ownedUrl: boolean): Promise<PlayOutcome> {
  const a = element();
  return new Promise<PlayOutcome>((resolve) => {
    let settled = false;
    const finish = (o: PlayOutcome) => {
      if (settled) return;
      settled = true;
      a.removeEventListener("ended", onEnd);
      a.removeEventListener("error", onError);
      if (finishCurrent === finish) finishCurrent = null;
      if (myToken === token) {
        setSpeaking(false);
        if (ownedUrl) releaseUrl();
      }
      resolve(o);
    };
    const onEnd = () => finish("played");
    const onError = () => finish("unavailable");
    finishCurrent = finish;
    a.addEventListener("ended", onEnd);
    a.addEventListener("error", onError);
    a.src = src;
    a.play().then(
      () => {
        if (myToken === token) setSpeaking(true);
      },
      (err: unknown) => {
        const name = err instanceof DOMException ? err.name : "";
        finish(name === "NotAllowedError" ? "blocked" : name === "AbortError" ? "cancelled" : "unavailable");
      },
    );
  });
}

/**
 * Fetch and play the audio for an assistant turn. Resolves when playback finishes (or fails).
 * On "blocked" the clip is kept; call `playBlocked(turnId)` from a tap to hear it.
 */
export async function playTurn(turnId: string): Promise<PlayOutcome> {
  stopAudio();
  const myToken = token;
  const ctrl = new AbortController();
  fetchAbort = ctrl;
  let blob: Blob | null;
  try {
    blob = await api.turnAudio(turnId, ctrl.signal);
  } catch (err) {
    if (myToken !== token) return "cancelled";
    if (err instanceof DOMException && err.name === "AbortError") return "cancelled";
    // 503 (TTS failed) or any other error: the reply stays as text with "voice unavailable".
    return "unavailable";
  } finally {
    if (fetchAbort === ctrl) fetchAbort = null;
  }
  if (myToken !== token) return "cancelled";
  if (!blob) return "none";
  currentUrl = URL.createObjectURL(blob);
  const outcome = await playSrc(currentUrl, myToken, true);
  if (outcome === "blocked") blocked.set(turnId, blob);
  return outcome;
}

/** Replay a clip whose autoplay was blocked. Must be called from a tap handler. */
export async function playBlocked(turnId: string): Promise<PlayOutcome> {
  const blob = blocked.get(turnId);
  if (!blob) return playTurn(turnId);
  stopAudio();
  unlocked = true;
  blocked.delete(turnId);
  const myToken = token;
  currentUrl = URL.createObjectURL(blob);
  return playSrc(currentUrl, myToken, true);
}

/** Play a plain URL (demo replay's pre-recorded `audio_url`). */
export async function playUrl(url: string): Promise<PlayOutcome> {
  stopAudio();
  const myToken = token;
  return playSrc(url, myToken, false);
}

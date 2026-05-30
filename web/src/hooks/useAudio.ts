import { useEffect, useRef, useState, useCallback } from "react";

/**
 * useAudio — generated CRT/UI sound, no assets.
 *
 * Web Audio is gated behind a user gesture (browser autoplay policy), so
 * call `init()` from inside a click/keydown handler (the boot "INITIALIZE"
 * tap is the natural place). Ships MUTED by default; persists the user's
 * on/off choice in localStorage.
 *
 *   const audio = useAudio();
 *   <button onClick={() => { audio.init(); audio.powerOn(); }}>boot</button>
 *   audio.tick(); audio.chime(); audio.setEnabled(true);
 */

const LS_KEY = "homunculus_sound_v1";

export interface AudioApi {
  ready: boolean;
  enabled: boolean;
  init: () => void;
  setEnabled: (on: boolean) => void;
  tick: () => void;
  key: () => void;
  blip: (freq?: number, dur?: number, type?: OscillatorType, vol?: number) => void;
  powerOn: () => void;
  chime: () => void;
}

export function useAudio(): AudioApi {
  const ctxRef = useRef<AudioContext | null>(null);
  const masterRef = useRef<GainNode | null>(null);
  const [ready, setReady] = useState(false);
  const [enabled, setEnabledState] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try { return localStorage.getItem(LS_KEY) === "1"; } catch { return false; }
  });
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const init = useCallback(() => {
    if (ctxRef.current) return;
    try {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const master = ctx.createGain();
      master.gain.value = 0.0001;
      master.connect(ctx.destination);
      master.gain.exponentialRampToValueAtTime(enabledRef.current ? 0.5 : 0.0001, ctx.currentTime + 1.2);
      ctxRef.current = ctx;
      masterRef.current = master;

      // ambient CRT hum
      const humGain = ctx.createGain();
      humGain.gain.value = 0.0001;
      humGain.gain.exponentialRampToValueAtTime(0.06, ctx.currentTime + 2.5);
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass"; lp.frequency.value = 220; lp.Q.value = 4;
      const o1 = ctx.createOscillator(); o1.type = "triangle"; o1.frequency.value = 58;
      const o2 = ctx.createOscillator(); o2.type = "sine"; o2.frequency.value = 116.5;
      const o3 = ctx.createOscillator(); o3.type = "sine"; o3.frequency.value = 41;
      const lfo = ctx.createOscillator(); lfo.type = "sine"; lfo.frequency.value = 0.13;
      const lfoG = ctx.createGain(); lfoG.gain.value = 60;
      lfo.connect(lfoG); lfoG.connect(lp.frequency);
      o1.connect(lp); o2.connect(lp); o3.connect(lp);
      lp.connect(humGain); humGain.connect(master);
      [o1, o2, o3, lfo].forEach((o) => o.start());

      setReady(true);
    } catch { /* audio unsupported — fail silent */ }
  }, []);

  const setEnabled = useCallback((on: boolean) => {
    setEnabledState(on);
    try { localStorage.setItem(LS_KEY, on ? "1" : "0"); } catch { /* ignore */ }
    const ctx = ctxRef.current, master = masterRef.current;
    if (ctx && master) master.gain.exponentialRampToValueAtTime(on ? 0.5 : 0.0001, ctx.currentTime + 0.3);
  }, []);

  const blip = useCallback((freq = 1400, dur = 0.03, type: OscillatorType = "square", vol = 0.08) => {
    const ctx = ctxRef.current, master = masterRef.current;
    if (!ctx || !master || !enabledRef.current) return;
    const t = ctx.currentTime;
    const o = ctx.createOscillator(); o.type = type; o.frequency.value = freq;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + 0.004);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.02);
  }, []);

  const noise = useCallback((dur = 0.05, vol = 0.05, freq = 1800) => {
    const ctx = ctxRef.current, master = masterRef.current;
    if (!ctx || !master || !enabledRef.current) return;
    const t = ctx.currentTime;
    const n = Math.floor(ctx.sampleRate * dur);
    const buf = ctx.createBuffer(1, n, ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const src = ctx.createBufferSource(); src.buffer = buf;
    const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = freq; bp.Q.value = 0.8;
    const g = ctx.createGain(); g.gain.value = vol;
    src.connect(bp); bp.connect(g); g.connect(master); src.start(t);
  }, []);

  const tick = useCallback(() => blip(1100 + Math.random() * 700, 0.012, "square", 0.045), [blip]);
  const key = useCallback(() => noise(0.025, 0.04, 2200 + Math.random() * 600), [noise]);

  const powerOn = useCallback(() => {
    const ctx = ctxRef.current, master = masterRef.current;
    if (!ctx || !master || !enabledRef.current) return;
    const t = ctx.currentTime;
    const o = ctx.createOscillator(); o.type = "sawtooth";
    o.frequency.setValueAtTime(70, t);
    o.frequency.exponentialRampToValueAtTime(520, t + 0.45);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.12, t + 0.06);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.6);
    const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 1400;
    o.connect(lp); lp.connect(g); g.connect(master); o.start(t); o.stop(t + 0.7);
    noise(0.4, 0.08, 900);
  }, [noise]);

  const chime = useCallback(() => {
    [0, 0.09, 0.18, 0.30].forEach((d, i) => {
      const f = [523.25, 659.25, 783.99, 1046.5][i];
      setTimeout(() => blip(f, 0.5, "sine", 0.09), d * 1000);
    });
  }, [blip]);

  // resume context if the tab was backgrounded
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === "visible") ctxRef.current?.resume?.(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  return { ready, enabled, init, setEnabled, tick, key, blip, powerOn, chime };
}

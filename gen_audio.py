#!/usr/bin/env python3
"""Generate a royalty-free ambient audio bed for showdown videos (pure python, WAV out).

Usage: python3 gen_audio.py <out.wav> [--style zen|arcade] [--seconds 32] [--seed 7]
zen    — soft pad + pentatonic bells (Monument Valley-ish)
arcade — square-wave chiptune arpeggio + blips (retro game-ish)
"""
import argparse
import math
import random
import struct
import wave

SR = 44100


def note(freq, t):
    return math.sin(2 * math.pi * freq * t)


def square(freq, t):
    return 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0


def build(style, seconds, seed):
    rng = random.Random(seed)
    n = int(SR * seconds)
    buf = [0.0] * n

    # C major pentatonic, two octaves
    penta = [261.63, 293.66, 329.63, 392.00, 440.00,
             523.25, 587.33, 659.25, 783.99, 880.00]

    if style == "zen":
        # pad: slow detuned chord (C, G, E), gentle LFO
        for f in (130.81, 196.00, 164.81):
            for i in range(n):
                t = i / SR
                lfo = 0.5 + 0.5 * math.sin(2 * math.pi * 0.05 * t + f)
                env = min(t / 4.0, 1.0) * min((seconds - t) / 3.0, 1.0)
                buf[i] += 0.045 * env * lfo * (note(f, t) + 0.6 * note(f * 1.003, t))
        # bells: sparse pentatonic hits with exponential decay
        t_cursor = 1.0
        while t_cursor < seconds - 3:
            f = rng.choice(penta)
            start = int(t_cursor * SR)
            dur = int(3.5 * SR)
            for j in range(min(dur, n - start)):
                t = j / SR
                env = math.exp(-t * 1.8)
                buf[start + j] += 0.14 * env * (note(f, t) + 0.4 * note(f * 2, t) + 0.15 * note(f * 3.01, t))
            t_cursor += rng.uniform(1.2, 2.8)
    else:  # arcade
        bpm = 132
        step = 60 / bpm / 2  # 8th notes
        arp = [261.63, 329.63, 392.00, 523.25]
        i_step = 0
        t_cursor = 0.0
        while t_cursor < seconds - 1:
            f = arp[i_step % len(arp)] * (2 if (i_step // 8) % 2 else 1)
            start = int(t_cursor * SR)
            dur = int(step * 0.85 * SR)
            for j in range(min(dur, n - start)):
                t = j / SR
                env = min(t / 0.005, 1.0) * math.exp(-t * 6)
                buf[start + j] += 0.05 * env * square(f, t)
            # bass every 4 steps
            if i_step % 4 == 0:
                fb = 65.41 if (i_step // 16) % 2 == 0 else 98.0
                for j in range(min(int(step * 1.8 * SR), n - start)):
                    t = j / SR
                    buf[start + j] += 0.06 * math.exp(-t * 3) * square(fb, t)
            # sparkle blip occasionally
            if rng.random() < 0.12:
                fs = rng.choice(penta) * 2
                for j in range(min(int(0.12 * SR), n - start)):
                    t = j / SR
                    buf[start + j] += 0.05 * math.exp(-t * 20) * note(fs, t)
            t_cursor += step
            i_step += 1

    # soft clip + master fade in/out
    out = []
    for i, v in enumerate(buf):
        t = i / SR
        fade = min(t / 0.5, 1.0, (seconds - t) / 2.0)
        v = math.tanh(v * 1.2) * fade
        out.append(v)
    # peak-normalize to -2 dBFS — un-normalized synth levels land around
    # -16 dBFS peak, which reads as near-silence on phone speakers
    peak = max(abs(v) for v in out) or 1.0
    gain = 0.79 / peak
    return [v * gain for v in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--style", default="zen", choices=["zen", "arcade"])
    ap.add_argument("--seconds", type=float, default=32)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    samples = build(a.style, a.seconds, a.seed)
    with wave.open(a.out, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        for v in samples:
            s = int(max(-1, min(1, v)) * 32767)
            frames += struct.pack("<hh", s, s)
        w.writeframes(bytes(frames))
    print(f"wrote {a.out} ({a.seconds}s, {a.style})")


if __name__ == "__main__":
    main()

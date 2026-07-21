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


def organ(freq, t):
    # drawbar-organ-ish additive tone (fundamental + a few harmonics) — the
    # sustained-pipe-organ color that reads as "cinematic space", à la a certain
    # Nolan film. Original timbre/motif, not a transcription of any score.
    return (note(freq, t) + 0.5 * note(2 * freq, t)
            + 0.33 * note(3 * freq, t) + 0.22 * note(4 * freq, t))


def build(style, seconds, seed):
    rng = random.Random(seed)
    n = int(SR * seconds)
    buf = [0.0] * n

    # C major pentatonic, two octaves
    penta = [261.63, 293.66, 329.63, 392.00, 440.00,
             523.25, 587.33, 659.25, 783.99, 880.00]

    if style == "ethereal":
        # 比 zen 更空灵：气声高音 pad(慢) + 高频 drone + 极稀疏高八度铃音(长衰减、带泛音闪烁)。
        # 太空/黑洞氛围，飘、留白多、几乎无节奏。
        # 高位挂留和弦(C E G B D) — airy
        for f in (261.63, 329.63, 392.00, 493.88, 587.33):
            for i in range(n):
                t = i / SR
                lfo = 0.55 + 0.45 * math.sin(2 * math.pi * 0.035 * t + f * 0.01)
                env = min(t / 6.0, 1.0) * min((seconds - t) / 5.0, 1.0)
                buf[i] += 0.016 * env * lfo * (note(f, t) + 0.5 * note(f * 1.005, t))
        # 高频气声 drone(极轻)，制造持续的"飘"
        for i in range(n):
            t = i / SR
            env = min(t / 8.0, 1.0) * min((seconds - t) / 6.0, 1.0)
            buf[i] += 0.010 * env * (note(783.99, t) + 0.4 * note(1174.66, t) * (0.5 + 0.5 * math.sin(2 * math.pi * 0.07 * t)))
        # 稀疏高八度铃音闪烁：长衰减 + 高泛音
        t_cursor = 2.0
        while t_cursor < seconds - 4:
            f = rng.choice(penta[4:]) * 2  # 高八度
            start = int(t_cursor * SR)
            dur = int(5.0 * SR)
            for j in range(min(dur, n - start)):
                t = j / SR
                env = math.exp(-t * 1.1)
                buf[start + j] += 0.06 * env * (note(f, t) + 0.5 * note(f * 2, t) + 0.25 * note(f * 3.01, t) + 0.12 * note(f * 4.02, t))
            t_cursor += rng.uniform(2.6, 4.8)
    elif style == "zen":
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
    elif style == "cinematic":
        # 原创"星际穿越风"电影氛围乐：管风琴铺底走一个和缓的和弦进行 +
        # 极简上行分解和弦动机 + 低频 drone + 缓慢 swell。抓那种宇宙/悬浮的电影感，
        # 不复制任何具体旋律（版权安全）。
        # 和弦进行 Am–F–C–G（三和弦，舒适音区），每格 chord_dur 秒，循环
        prog = [
            (220.00, [220.00, 261.63, 329.63]),   # Am : A C E
            (174.61, [174.61, 220.00, 261.63]),   # F  : F A C
            (130.81, [130.81, 164.81, 196.00]),   # C  : C E G
            (196.00, [196.00, 246.94, 293.66]),   # G  : G B D
        ]
        chord_dur = 4.0
        for i in range(n):
            t = i / SR
            slot = int(t / chord_dur)
            root, triad = prog[slot % len(prog)]
            lt = t - slot * chord_dur                       # 本格内相对时间
            # 每格一个 swell：慢涨（1.8s）再微落，接全曲总包络
            swell = min(lt / 1.8, 1.0) * min((chord_dur - lt) / 1.0, 1.0)
            glob = min(t / 3.0, 1.0) * min((seconds - t) / 4.0, 1.0)
            env = swell * glob
            # 管风琴铺底（三和弦，轻微失谐加宽）
            pad = 0.0
            for f in triad:
                pad += organ(f, t) + 0.5 * organ(f * 1.004, t)
            buf[i] += 0.020 * env * pad
            # 低频 drone（根音下八度），稳住宇宙感
            buf[i] += 0.045 * glob * note(root * 0.5, t)
        # 极简上行分解和弦动机（quarter notes ~66bpm），管风琴音色
        beat = 60 / 66
        t_cursor = 2.0
        step_i = 0
        while t_cursor < seconds - 1:
            slot = int(t_cursor / chord_dur)
            _, triad = prog[slot % len(prog)]
            seq = [triad[0], triad[1], triad[2], triad[0] * 2, triad[2], triad[1]]
            f = seq[step_i % len(seq)]
            start = int(t_cursor * SR)
            dur = int(beat * 0.95 * SR)
            for j in range(min(dur, n - start)):
                t = j / SR
                aenv = min(t / 0.02, 1.0) * math.exp(-t * 1.1)
                buf[start + j] += 0.10 * aenv * (note(f, t) + 0.4 * note(2 * f, t))
            t_cursor += beat
            step_i += 1
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
    ap.add_argument("--style", default="zen", choices=["zen", "arcade", "cinematic", "ethereal"])
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

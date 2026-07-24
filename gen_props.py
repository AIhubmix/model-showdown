#!/usr/bin/env python3
"""Merge episode metrics + recording reports into Remotion input props.

Usage: python3 gen_props.py <episode_dir> <title> <subtitle> > video/props.json
Verdict strings default to auto heuristics; hand-edit the JSON before render if needed.
"""
import glob
import json
import os
import sys

ACCENTS = ["#38bdf8", "#f97316", "#a78bfa", "#22c55e"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_cfg_path = os.path.join(SCRIPT_DIR, "showdown.config.json")
_brand = {}
if os.path.exists(_cfg_path):
    with open(_cfg_path) as f:
        _brand = json.load(f).get("brand", {})
TAGLINE = _brand.get("tagline", "AIHubMix: Unified API, 800+ models")
WATERMARK = _brand.get("watermark", "")

argv = [a for a in sys.argv[1:] if a != "--vertical"]
layout = "vertical" if "--vertical" in sys.argv else "horizontal"
ep_dir = argv[0].rstrip("/")
title = argv[1] if len(argv) > 1 else "Model Showdown"
subtitle = argv[2] if len(argv) > 2 else ""
ep = os.path.basename(ep_dir)

# 上下排版：ORDER 决定堆叠顺序（靠前=靠上）
ORDER = ["qwen3.8-max-preview", "claude-fable-5",
         "kimi-k3", "coding-kimi-k3", "claude-opus-4-8-think", "claude-opus-4-8", "gpt-5.6-sol"]
# short names for the narrow panel header; full names stay in title/subtitle
SHORT = {"Claude Opus 4.8": "Opus 4.8", "GPT-5.6 Sol": "GPT-5.6 Sol"}

results = []
for path in sorted(glob.glob(f"{ep_dir}/metrics*.json")):
    with open(path) as f:
        for r in json.load(f)["results"]:
            if r.get("code_extracted"):
                results.append(r)
results.sort(key=lambda r: ORDER.index(r["requested"]) if r["requested"] in ORDER else 99)

models = []
for i, r in enumerate(results):
    model = r["requested"]
    report_path = f"{ep_dir}/recordings/report_{model}.json"
    verdict = "SHIPPED"
    if os.path.exists(report_path):
        with open(report_path) as f:
            rep = json.load(f)
        if rep.get("frozen"):
            verdict = "FROZE"
        elif rep.get("consoleErrors"):
            verdict = "RAN W/ ERRORS"
    models.append({
        "name": SHORT.get(r["display"], r["display"]),
        "cost": f"${r['cost_usd']:.2f}",
        "timeS": r["latency_s"],
        "video": f"{ep}/{model}.webm",
        "verdict": verdict,
        "accent": ACCENTS[i % len(ACCENTS)],
    })

audio = f"{ep}/audio.wav" if os.path.exists(f"video/public/{ep}/audio.wav") else None

print(json.dumps({
    "title": title,
    "subtitle": subtitle,
    "tagline": TAGLINE,
    "watermark": WATERMARK,
    "introFrames": 0,
    "playFrames": 600,
    "outroFrames": 130,
    "audio": audio,
    "layout": layout,
    "models": models,
}, indent=1))

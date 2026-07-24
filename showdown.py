#!/usr/bin/env python3
"""One command from prompt to publish-ready share pack.

    python3 showdown.py --task my-prompt.md [--ref img.png] [--models a,b,c]

Chains the whole pipeline that previously took five manual steps:
  run_showdown.py (generate + record) -> gen_audio.py -> copy assets ->
  gen_props.py -> remotion render (wide + square) -> episodes/epNN/dist/
with the final dist/ holding the mp4s, posters, metrics and a ready-to-edit
post.md for X / Reddit. Re-run post-production alone on an existing episode
with --skip-gen.
"""
import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(SCRIPT_DIR, "video")

CONFIG = {}
_cfg_path = os.path.join(SCRIPT_DIR, "showdown.config.json")
if os.path.exists(_cfg_path):
    with open(_cfg_path) as f:
        CONFIG = json.load(f)
BRAND = CONFIG.get("brand", {})
WATERMARK = BRAND.get("watermark", "Made with model-showdown · aihubmix.com")
GALLERY = BRAND.get("gallery", "https://aihubmix.github.io/model-showdown/")

# composition id -> output suffix; both use the horizontal layout props
FORMATS = {"wide": "ShowdownWide", "square": "ShowdownSquare"}


def sh(cmd, cwd=None, capture=False):
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=cwd, text=True,
                       capture_output=capture)
    if r.returncode != 0:
        sys.exit(f"step failed ({r.returncode}): {' '.join(cmd)}")
    return r.stdout if capture else None


def next_episode_dir():
    base = os.path.join(SCRIPT_DIR, "episodes")
    nums = [int(m.group(1)) for d in glob.glob(f"{base}/ep*")
            if (m := re.match(r"ep(\d+)$", os.path.basename(d)))]
    return os.path.join(base, f"ep{(max(nums) if nums else 0) + 1:02d}")


def load_results(ep_dir):
    """All code_extracted results across metrics*.json (partial reruns split files)."""
    results = []
    for path in sorted(glob.glob(f"{ep_dir}/metrics*.json")):
        with open(path) as f:
            for r in json.load(f)["results"]:
                if r.get("code_extracted"):
                    results.append(r)
    return results


def write_post_md(ep_dir, title, results, models_arg):
    ep = os.path.basename(ep_dir)
    by_cost = sorted(results, key=lambda r: r["cost_usd"])
    names = [r["display"] for r in results]
    bill = " · ".join(f"{r['display']} ${r['cost_usd']:.2f}/{r['latency_s']:.0f}s"
                      for r in results)
    table = "\n".join(f"| {r['display']} | ${r['cost_usd']:.2f} | {r['latency_s']:.0f}s "
                      f"| {r['usage'].get('completion_tokens', 0)} |"
                      for r in results)
    repro = f"python3 showdown.py --task task.md --models {models_arg}"
    cheapest = by_cost[0]["display"] if by_cost else "?"
    n = len(results)
    n_models = f"{n} model{'s' if n > 1 else ''}"
    post = f"""# {ep} 发布素材（发布前人工过一遍数字与措辞）

## X

Same prompt. {n_models}. One shot each. No cherry-picking.

{title}

The bill: {bill}

Cheapest run: {cheapest}. Full prompt + metrics in the thread.

{WATERMARK} — arena: {GALLERY}

## Reddit（r/LocalLLaMA 风格：数据 + 方法论 + 可复现，不做产品推销）

**Title**: I gave the same prompt to {n_models} ({", ".join(names)}) — results + the actual bill

**Body**:

One prompt, sent to every model through one OpenAI-compatible gateway,
one shot each, artifact recorded as-is in a headless browser.

| Model | Cost | Time | Output tokens |
|---|---|---|---|
{table}

Prompt and per-model metrics are attached; reproduce with:

```
{repro}
```

Blind-vote version of this round (no model names until you vote): {GALLERY}

## 复现命令

```
{repro}
```
"""
    with open(f"{ep_dir}/dist/post.md", "w") as f:
        f.write(post)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("episode_dir", nargs="?", default=None)
    ap.add_argument("--task", metavar="FILE")
    ap.add_argument("--ref", action="append", default=[], metavar="IMAGE")
    ap.add_argument("--models", default=None, help="comma list; default = lineup")
    ap.add_argument("--seconds", type=int, default=26, help="per-artifact record seconds")
    ap.add_argument("--rec-size", default="720x960", metavar="WxH")
    ap.add_argument("--title", default=None, help="video title; default = task file name")
    ap.add_argument("--subtitle", default=None, help="default = 'A vs B vs C'")
    ap.add_argument("--style", default="zen",
                    choices=["zen", "arcade", "cinematic", "ethereal"])
    ap.add_argument("--formats", default="wide,square",
                    help=f"comma list of {'/'.join(FORMATS)}")
    ap.add_argument("--skip-gen", action="store_true",
                    help="episode already generated+recorded; post-production only")
    args = ap.parse_args()

    formats = [x.strip() for x in args.formats.split(",") if x.strip()]
    bad = [x for x in formats if x not in FORMATS]
    if bad:
        sys.exit(f"unknown format(s) {bad}; pick from {list(FORMATS)}")

    ep_dir = os.path.abspath(args.episode_dir) if args.episode_dir else None

    # 1. generate + record (unless the episode already exists)
    if not args.skip_gen:
        if not args.task:
            sys.exit("--task FILE is required (or use --skip-gen on an existing episode)")
        if ep_dir is None:
            ep_dir = next_episode_dir()
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, "run_showdown.py"), ep_dir,
               "--task", args.task, "--record", str(args.seconds),
               "--rec-size", args.rec_size]
        for r in args.ref:
            cmd += ["--ref", r]
        if args.models:
            cmd += ["--models", args.models]
        sh(cmd)
    elif ep_dir is None:
        sys.exit("--skip-gen requires an explicit episode_dir")

    ep = os.path.basename(ep_dir)
    results = load_results(ep_dir)
    if not results:
        sys.exit(f"no successful results in {ep_dir}/metrics*.json")
    models_arg = args.models or ",".join(r["requested"] for r in results)
    title = args.title or (os.path.splitext(os.path.basename(args.task))[0]
                           .replace("-", " ").replace("_", " ").title()
                           if args.task else "Model Showdown")
    subtitle = args.subtitle or " vs ".join(r["display"] for r in results)

    # 2. assets into video/public/epNN/ (webm + procedural audio bed)
    pub = os.path.join(VIDEO_DIR, "public", ep)
    os.makedirs(pub, exist_ok=True)
    webms = [f"{ep_dir}/recordings/{r['requested']}.webm" for r in results]
    webms = [w for w in webms if os.path.exists(w)]
    if not webms:
        sys.exit(f"no recordings in {ep_dir}/recordings/ — rerun with --seconds > 0")
    for w in webms:
        shutil.copy2(w, pub)
    play_frames = max(300, (args.seconds - 6) * 30)  # 26s recording -> 600f play
    audio_seconds = math.ceil(play_frames / 30 + 130 / 30 + 1)  # play + outro + pad
    sh([sys.executable, os.path.join(SCRIPT_DIR, "gen_audio.py"),
        os.path.join(pub, "audio.wav"), "--style", args.style,
        "--seconds", str(audio_seconds)])

    # 3. props (gen_props defaults playFrames=600; scale to the recorded length)
    props_json = sh([sys.executable, os.path.join(SCRIPT_DIR, "gen_props.py"),
                     ep_dir, title, subtitle], capture=True)
    props = json.loads(props_json)
    props["playFrames"] = play_frames
    props_path = os.path.join(VIDEO_DIR, "props.json")
    with open(props_path, "w") as f:
        json.dump(props, f, ensure_ascii=False, indent=1)

    # 4. render every requested format
    dist = os.path.join(ep_dir, "dist")
    os.makedirs(dist, exist_ok=True)
    for fmt in formats:
        out = f"out/{ep}-{fmt}.mp4"
        sh(["npx", "remotion", "render", FORMATS[fmt], out, "--props=props.json"],
           cwd=VIDEO_DIR)
        shutil.copy2(os.path.join(VIDEO_DIR, out), dist)

    # 5. share pack: posters + metrics + post.md
    for p in glob.glob(f"{ep_dir}/recordings/poster_*.png"):
        shutil.copy2(p, dist)
    if os.path.exists(f"{ep_dir}/metrics.json"):
        shutil.copy2(f"{ep_dir}/metrics.json", dist)
    write_post_md(ep_dir, title, results, models_arg)

    print(f"\nshare pack ready: {dist}")
    for name in sorted(os.listdir(dist)):
        print(f"  {name}")
    print("\nnext: review dist/post.md (verdict 标签如需更有区分度请改 video/props.json 后重渲)")


if __name__ == "__main__":
    main()

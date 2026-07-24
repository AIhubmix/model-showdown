#!/usr/bin/env python3
"""Feed an episode back into the arena gallery as a new blind round.

    python3 submit_round.py episodes/epNN --title "Round title" [--summary "..."]

Copies each successful model's artifact into docs/games/roundN/<letter>.html,
its poster into docs/media/posters/roundN-<letter>.png (falls back to a frame
grabbed from the recording when no poster exists), and appends a round entry
to docs/rounds.js. Slot letters are shuffled so position never leaks identity.

Notes are auto-filled from metrics ($cost · time · lines) with a TODO marker —
write the editorial one-liners, then commit & push to publish. External
contributors: run this, then open a PR with the docs/ diff.
"""
import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCENTS = ["#38bdf8", "#f97316", "#a78bfa", "#22c55e", "#34d399", "#f472b6"]
FFMPEG = ["npx", "--prefix", os.path.join(SCRIPT_DIR, "video"), "remotion", "ffmpeg"]


def load_results(ep_dir):
    results = []
    for path in sorted(glob.glob(f"{ep_dir}/metrics*.json")):
        with open(path) as f:
            for r in json.load(f)["results"]:
                if r.get("code_extracted"):
                    results.append(r)
    return results


def humanize(latency_s, code_lines):
    t = f"{latency_s:.0f}s" if latency_s < 90 else f"~{latency_s / 60:.0f} min"
    return f"{t} · {code_lines} lines" if code_lines else t


def js_str(s):
    """Backtick-template literal, escaping backticks and ${ interpolation."""
    return "`" + s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${") + "`"


def grab_poster_from_webm(webm, out_png):
    r = subprocess.run(FFMPEG + ["-y", "-loglevel", "error", "-ss", "3",
                                 "-i", webm, "-frames:v", "1", out_png],
                       capture_output=True)
    return r.returncode == 0 and os.path.exists(out_png)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("episode_dir")
    ap.add_argument("--title", required=True, help='round title, e.g. "Fruit Ninja"')
    ap.add_argument("--summary", default=None,
                    help="editorial task summary; default = first lines of task.md")
    ap.add_argument("--cols", type=int, default=None, help="grid columns override")
    ap.add_argument("--aspect", default=None, help='poster aspect override, e.g. "16/9"')
    ap.add_argument("--docs", default=os.path.join(SCRIPT_DIR, "docs"),
                    help="gallery root (override for testing)")
    ap.add_argument("--seed", type=int, default=None, help="shuffle seed (default: random)")
    ap.add_argument("--dry-run", action="store_true", help="print the entry, write nothing")
    args = ap.parse_args()

    ep_dir = os.path.abspath(args.episode_dir.rstrip("/"))
    docs = os.path.abspath(args.docs)
    rounds_js = os.path.join(docs, "rounds.js")
    if not os.path.exists(rounds_js):
        sys.exit(f"{rounds_js} not found — gallery must be the rounds.js-driven version")

    results = load_results(ep_dir)
    if len(results) < 2:
        sys.exit(f"need >= 2 successful results for a round, got {len(results)}")

    with open(rounds_js) as f:
        js = f.read()
    ids = [int(m) for m in re.findall(r"\bid:\s*(\d+)", js)]
    round_id = (max(ids) if ids else 0) + 1
    episode = os.path.basename(ep_dir)
    for m in re.findall(r'episode:\s*"([^"]+)"', js):
        if m == episode:
            sys.exit(f"episode {episode} is already in the gallery — refusing to duplicate")

    summary = args.summary
    if not summary and os.path.exists(f"{ep_dir}/task.md"):
        with open(f"{ep_dir}/task.md") as f:
            summary = " ".join(f.read().split())[:280] + " …"
    summary = summary or "TODO: describe the task."

    rng = random.Random(args.seed)
    rng.shuffle(results)  # 盲投：slot 顺序不泄露身份

    entries = []
    copies = []  # (src, dst)
    for i, r in enumerate(results):
        letter = "abcdefgh"[i]
        model = r["requested"]
        game_src = f"{ep_dir}/work_{model}/index.html"
        if not os.path.exists(game_src):
            sys.exit(f"missing artifact: {game_src}")
        poster_src = f"{ep_dir}/recordings/poster_{model}.png"
        poster_dst = os.path.join(docs, "media", "posters", f"round{round_id}-{letter}.png")
        if not os.path.exists(poster_src):
            webm = f"{ep_dir}/recordings/{model}.webm"
            if not os.path.exists(webm):
                sys.exit(f"no poster and no recording for {model} — record the episode first")
            poster_src = f"{ep_dir}/recordings/poster_{model}.png"
            if not args.dry_run:
                if not grab_poster_from_webm(webm, poster_src):
                    sys.exit(f"could not grab a poster frame from {webm}")
        copies.append((game_src, os.path.join(docs, "games", f"round{round_id}", f"{letter}.html")))
        copies.append((poster_src, poster_dst))
        note = (f"TODO editorial note. Auto: {r['display']} shipped "
                f"{humanize(r['latency_s'], r.get('code_lines', 0))} "
                f"for ${r['cost_usd']:.2f}.")
        entries.append(
            f'        {{ letter: "{letter}", name: {js_str(r["display"])}, '
            f'accent: "{ACCENTS[i % len(ACCENTS)]}", cost: "${r["cost_usd"]:.2f}", '
            f'time: "{humanize(r["latency_s"], r.get("code_lines", 0))}",\n'
            f"          note: {js_str(note)},\n"
            f'          poster: "media/posters/round{round_id}-{letter}.png", '
            f'game: "games/round{round_id}/{letter}.html" }},'
        )

    extra = ""
    if args.cols:
        extra += f" cols: {args.cols},"
    if args.aspect:
        extra += f' aspect: "{args.aspect}",'
    entry = (
        f"    {{\n"
        f"      id: {round_id}, title: {js_str(f'Round {round_id} · {args.title}')}, "
        f'episode: "{episode}",{extra}\n'
        f"      summary: {js_str(summary)},\n"
        f"      models: [\n" + "\n".join(entries) + "\n"
        f"      ],\n"
        f"    }},\n"
    )

    if args.dry_run:
        print(entry)
        print(f"[dry-run] would copy {len(copies)} files and append round {round_id}")
        return

    for src, dst in copies:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    close = js.rstrip().rfind("];")
    if close < 0:
        sys.exit("could not find the closing ]; of ROUNDS in rounds.js")
    with open(rounds_js, "w") as f:
        f.write(js[:close] + entry + js[close:])
    if subprocess.run(["node", "--check", rounds_js], capture_output=True).returncode != 0:
        sys.exit("rounds.js failed syntax check after append — inspect and git checkout if needed")

    print(f"round {round_id} ({episode}) appended to the gallery:")
    for _, dst in copies:
        print(f"  {os.path.relpath(dst, docs)}")
    print("\nnext: edit the TODO notes + summary in docs/rounds.js, review locally, "
          "then commit & push to publish (external contributors: open a PR)")


if __name__ == "__main__":
    main()

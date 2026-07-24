# model-showdown

**🏟️ Live gallery: [aihubmix.github.io/model-showdown](https://aihubmix.github.io/model-showdown/)**

Turn "same prompt, N models, one shot" benchmarks into publish-ready comparison
videos — automatically.

Give it a prompt (optionally with reference images). It fans the prompt out to
multiple models through one OpenAI-compatible gateway, extracts the returned
single-file HTML artifact, records each artifact running in a headless browser,
and composes a side-by-side comparison video with cost badges, a results
scoreboard, and an ambient audio bed.

```
                       ┌───────────────────────────────────────────────┐
                       │        run_showdown.py  (Python, no deps)     │
 prompt.md ──────────▶ │  • fan-out to N models (SSE streaming,        │
 ref.png (optional)    │    heartbeat, retries, per-model max_tokens)  │
                       │  • extract ```html``` artifact per model      │
                       │  • incremental metrics.json (tokens, cost,    │
                       │    latency, reasoning share)                  │
                       │  • --record: pipeline into recording the      │
                       │    moment each model finishes                 │
                       └──────────────┬────────────────────────────────┘
                                      ▼
                       ┌───────────────────────────────────────────────┐
                       │        record.mjs  (Playwright)               │
                       │  • all models recorded in parallel            │
                       │  • webm + poster shots + console-error and    │
                       │    frozen-screen detection                    │
                       └──────────────┬────────────────────────────────┘
                                      ▼
                       ┌───────────────────────────────────────────────┐
                       │  gen_props.py → gen_audio.py → Remotion       │
                       │  • metrics → composition props (cost badges)  │
                       │  • procedural ambient/chiptune audio (WAV,    │
                       │    royalty-free by construction)              │
                       │  • 3-panel layout + "The bill" scoreboard,    │
                       │    square (1080²) and wide (1920×1080)        │
                       └──────────────┬────────────────────────────────┘
                                      ▼
                            episodes/epNN/epNN-wide.mp4
```

## Quickstart

Requirements: Node ≥ 20, pnpm, Python ≥ 3.9 (stdlib only), and an
OpenAI-compatible gateway key in `AIHUBMIX_API_KEY` (or point `GATEWAY` in
`run_showdown.py` at your own).

```bash
pnpm install && npx playwright install chromium   # recorder
cd video && pnpm install && cd ..                 # renderer

# one command, prompt -> publish-ready share pack
python3 showdown.py --task my-prompt.md --ref reference.png
```

That runs the whole chain (generate + record + audio + render, wide 1920×1080
and square 1080² by default) and drops everything you need to post into
`episodes/epNN/dist/`: the mp4s, poster shots, `metrics.json`, and a
pre-filled `post.md` with X / Reddit drafts whose numbers come straight from
the run's real bill. Useful flags: `--models a,b,c`, `--seconds 40`,
`--formats wide`, `--title` / `--subtitle`, `--style arcade`, and
`--skip-gen episodes/epNN` to re-run post-production on an existing episode.

Gateway, brand tagline/watermark, and model/pricing overrides live in
`showdown.config.json` — point it at any OpenAI-compatible gateway and put
your key in the env var it names (`AIHUBMIX_API_KEY` by default).

Each stage is still runnable on its own:

```bash
# generation + recording only
python3 run_showdown.py --task my-prompt.md --ref reference.png --record 26

# audio bed + props + render, step by step
python3 gen_audio.py video/public/epNN/audio.wav --style zen --seconds 26
cp episodes/epNN/recordings/*.webm video/public/epNN/
python3 gen_props.py episodes/epNN "Video title" "Model A vs B vs C" > video/props.json
cd video && npx remotion render ShowdownWide out/epNN-wide.mp4 --props=props.json
```

## Specifying models and prompts

**Prompt** — write it in a file (any language) and pass `--task`:

```bash
python3 run_showdown.py --task my-prompt.md --record 26              # text only
python3 run_showdown.py --task my-prompt.md --ref shot.png --record 26   # + reference image(s)
```

`--ref` can be repeated; images are sent to every model as multimodal
`image_url` parts. Episodes auto-number (`episodes/ep01`, `ep02`, …); pass an
explicit directory as the first argument to override.

**Models** — the default lineup is every key in the `MODELS` dict at the top of
`run_showdown.py`. Pick a subset per run with `--models`:

```bash
python3 run_showdown.py --task my-prompt.md --models kimi-k3,gpt-5.6-sol --record 26
```

To add a contestant, add one entry to `MODELS` (display name, plus optional
`max_tokens` / `retries` overrides — give max-effort reasoning models ≥100k) and
its list price to `PRICING` (USD per 1M tokens, used for the on-screen cost
badge). Any model id served by your OpenAI-compatible gateway works:

```python
MODELS = {
    # default lineup — every model at its max reasoning mode
    "kimi-k3": {"display": "Kimi K3", "max_tokens": 100000, "retries": 12},
    "claude-opus-4-8-think": {"display": "Claude Opus 4.8", "max_tokens": 100000},
    "gpt-5.6-sol": {"display": "GPT-5.6 Sol", "max_tokens": 100000,
                    "params": {"reasoning_effort": "max"}},   # merged into the payload
    # configured but excluded from the default lineup; pick via --models
    "claude-opus-4-8": {"display": "Claude Opus 4.8", "lineup": False},
}
PRICING = {"kimi-k3": (3.0, 15.0), "claude-opus-4-8-think": (5.0, 25.0), "gpt-5.6-sol": (5.0, 30.0)}
```

Before trusting a reasoning knob, verify it actually works on your gateway: run the
same nontrivial question with and without the parameter and compare
`usage.completion_tokens_details.reasoning_tokens` — some gateways silently accept
unknown parameters, so acceptance alone proves nothing.

Brand logo and tagline are composition props (`video/src/Root.tsx` defaults) —
swap in your own.

## Claude Code skill (optional autopilot)

The repo ships a [Claude Code](https://claude.com/claude-code) skill at
`.claude/skills/showdown/` that drives the whole pipeline conversationally —
parse a one-line request ("pit kimi-k3 against gpt-5.6 on a pinball sim"),
confirm the prompt, run generation + recording, QA the posters, render, and
draft posts.

- **Project-scoped (zero install):** open Claude Code anywhere inside this
  repo and type `/showdown <task idea>` — project skills are auto-discovered.
- **Global (use from any directory):**

  ```bash
  cp -r .claude/skills/showdown ~/.claude/skills/
  ```

One-line usage examples — name models and reference images right in the request,
or name none to run the full default lineup:

```
/showdown pit kimi-k3 against gpt-5.6 on a 3D pinball sim
/showdown recreate this screenshot as a playable game, ref: ~/shots/level.png
/showdown weekly episode: particle fluid simulation, all models
```

## Full episode workflow

The quickstart above covers the automated 80%. A full episode has a few human
touchpoints (quality gate, verdict labels, publishing copy):

| Step | What | Who |
|---|---|---|
| 1 | Write the prompt (follow the three rules below) | human |
| 2 | `run_showdown.py --task … --record 26` — generate + record, pipelined | auto |
| 3 | QA: check `recordings/poster_*.png` and `report_*.json` (console errors / frozen screen) | human |
| 4 | `gen_audio.py` + `gen_props.py` → edit `verdict` labels in props.json → `remotion render` (preview one frame with `remotion still` first) | auto + human |
| 5 | Write posts from `metrics.json` numbers (keep every number traceable) | human |
| 6 | Retro: log timings, cost, and new pitfalls | human |

Step-by-step commands with timings and known pitfalls:
[`docs/本地安装与使用指南.md`](docs/本地安装与使用指南.md) (Chinese).

## Prompt rules that make the pipeline fully automatic

1. Single self-contained HTML file, no CDN, no external assets (offline recording).
2. **Require an auto-demo mode** — the artifact must start playing itself ~1s
   after load. This is what makes unattended recording possible.
3. End with: ``Return ONLY the complete HTML file in a single ```html code block.``

## Hard-won operational notes

- **Stream everything.** Long-thinking models (e.g. Kimi K3) produce zero bytes
  for 10+ minutes on non-streamed requests, and upstream proxies kill the idle
  connection. SSE keeps it alive; usage (incl. `reasoning_tokens`) arrives in
  the final chunk.
- **Budget for reasoning.** Max-effort reasoning models can burn a 32k
  completion budget entirely on chain-of-thought and emit zero code. Give them
  100k.
- Benchmark the real model id. Discounted/proxy channel variants bill
  differently and misrepresent identity.
- A model's failure to produce a working artifact is a result — record the
  failure card, don't retry it away silently.

## Licensing notes

- This project: MIT (see `LICENSE`).
- **Remotion is source-available, not OSS** — companies above the free-tier
  threshold need a paid Remotion license to render. Check
  [remotion.dev/license](https://www.remotion.dev/license).
- Reference images you feed via `--ref` (e.g. game screenshots) are typically
  copyrighted — `episodes/` is gitignored by default; don't republish refs.

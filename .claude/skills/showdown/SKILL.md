---
name: showdown
description: Produce a multi-model comparison video episode end to end — same prompt one-shot to N models via one gateway, auto-record the returned HTML artifacts, compose a side-by-side video with cost badges, and draft publishing copy. Usage:/showdown <task idea, optionally naming models and reference images>
---

# Model Showdown — episode pipeline

All commands run from the repository root. The tools are already here — do not
rewrite them. Read `README.md` for architecture and `docs/` for the detailed
step-by-step guide.

For a standard episode (default lineup, no special camera work), prefer the
one-command orchestrator — it chains steps 2-5 below and drops mp4s + posters +
metrics + a pre-filled `post.md` into `episodes/epNN/dist/`:

```bash
python3 showdown.py --task prompt.md [--ref img.png] [--models a,b,c] [--seconds 26]
# post-production only, on an existing episode:
python3 showdown.py episodes/epNN --skip-gen --title "..."
```

Fall back to the manual steps when you need per-model verdict edits, orbit
re-recording, or non-default compositions. Gateway/brand/model overrides live
in `showdown.config.json`.

## 0. Parse the request

From the user's message extract:
- **Task idea** — what the models should build
- **Models** — if named, map to gateway model ids (`MODELS` dict keys in
  `run_showdown.py`); verify unknown ids against the gateway's `/v1/models`.
  If a named model isn't configured yet, add it to `MODELS` + `PRICING`
  (look up the official list price) before running. If none named, use the
  default lineup.
- **Reference images** — pass with `--ref` (sent to all models as multimodal
  `image_url` parts)

## 1. Write the prompt (confirm with the user before running)

Three rules that keep the pipeline fully automatic:
1. Single self-contained HTML file, no CDN, no external assets
2. Require an auto-demo mode: the artifact starts playing itself ~1s after load,
   any user input takes over
3. End with: `Return ONLY the complete HTML file in a single ```html code block.`

Pick tasks whose output is visually alive (games, simulations, animated scenes).
Show the prompt and model lineup to the user for confirmation, then save it and
run.

## 2. Generate + record (one command, pipelined)

```bash
python3 run_showdown.py --task /tmp/prompt.md [--ref img.png] [--models a,b,c] --record 26
```

Run in the background and watch the log — episodes auto-number and the first
log line prints the episode directory. Expect fast models in 2-3 min;
max-effort reasoning models (e.g. Kimi K3) in 10-35 min. Each artifact is
recorded automatically the moment its generation finishes.

## 3. QA the recordings

Read `episodes/epNN/recordings/poster_*.png` (visual check) and
`report_*.json` (`consoleErrors`, `frozen`). A broken or frozen artifact is a
legitimate benchmark result — keep it in the video with a failure label, don't
silently rerun.

## 4. Compose + render

```bash
python3 gen_audio.py video/public/epNN/audio.wav --style zen --seconds 26   # or arcade
cp episodes/epNN/recordings/*.webm video/public/epNN/
python3 gen_props.py episodes/epNN "<Title>" "<A vs B vs C>" > video/props-epNN.json
# edit verdict labels in the props to something discriminating (BEST LOOKING / SLOWEST / FROZE)
cd video && npx remotion still ShowdownWide out/check.png --frame=300 --props=props-epNN.json
npx remotion render ShowdownWide out/epNN-wide.mp4 --props=props-epNN.json
```

Check the still frame before the full render (long model names can overflow the
panel header — add short names to `SHORT` in `gen_props.py`). Copy finished
videos back into the episode directory.

## 5. Publishing copy (English)

Three variants into `episodes/epNN/post.md`: X (short, punchline first),
Reddit (methodology up front, no ad tone), HN (Show HN + first comment with
methodology and limitations). Every number must trace back to `metrics.json`.

## 6. Retro

Write `episodes/epNN/retro.md` (timings, cost, incidents, improvements) and
fold new pitfalls back into this file.

## Known pitfalls

- Max-effort reasoning models need `max_tokens` ≥ 100k — a 32k budget can be
  consumed entirely by chain-of-thought with zero code emitted
- Long generations must stream (the runner always streams): non-streamed
  connections are killed by upstream proxies after ~10-15 min of silence
- Benchmark the real model id — discounted proxy channels bill differently and
  misrepresent identity; substitute channels are configured with
  `lineup: False` and must be picked explicitly via `--models`
- Upstream overload (`engine_overloaded`, 429 storms) is time-of-day dependent;
  retries are built in, and off-peak scheduled runs are the reliable fallback
- The runner requires only Python stdlib; keep it free of `X | None` unions
  (must run on Python 3.9)
- Superlative adjectives get over-executed (ep10): "overexposed / burn / maximum
  contrast" made BOTH contestants render a pure-white frame. Visual prompts must
  state an upper bound too — an exposure-discipline clause (what may clip, what
  must stay visible / dominant)
- All-white or all-black recordings: rule out the environment before blaming the
  model — screenshot the same HTML with default headless (SwiftShader) vs
  `--use-angle=metal`; only if both match is it the artifact's own look
- CDN-heavy artifacts (Three.js importmap) burn ~5-6s of page load plus any
  artistic fade-in at the head of the recording (ep10: first usable frame at
  ~9-10s). Record with headroom and set the panel's `startFrom` to skip it —
  verify with extracted frames, not just the 15s poster
- Cinematic/wide subject matter: ShowdownWide + `layout: "vertical"` (props) +
  `--rec-size 1280x720` gives stacked 32:9 letterbox panels — the center-crop
  reads as anamorphic widescreen, great for space/film-look tasks
- One-shot shader tasks discriminate hard: a single GLSL overload typo
  (`fbm(vec3)` vs `fbm(vec2)`, ep10 Qwen) voids the whole render — unlike JS,
  one compile error = black screen. Good task genre for separating models
- record.mjs launches Chromium with GPU (`--use-angle=metal`) since ep10 —
  software rendering made heavy-shader recordings choppy and coarse. Record at
  1920x1080 for final-cut material; GPU also loads pages faster (first visible
  frame ~5s vs ~9-10s)
- Shader-heavy prompts should include a GLSL self-check clause from the start
  ("every call site must exactly match a defined overload; one compile error =
  black screen = failure") — adding it got Qwen from black screen to shipping
  in one retry (ep10 v3)
- `layout: "fullbleed"` (props) — recordings tiled edge-to-edge over the full
  frame, floating name/cost pills, brand bar only in the outro. ~3x more
  content area than the card layouts; best default for cinematic subjects
- `orbit-record.mjs` re-records an existing artifact while driving its
  interactive camera (in-page synthetic PointerEvent/WheelEvent via rAF — the
  Playwright mouse API is ~10x too slow on heavy-shader pages, one protocol
  roundtrip per move). Wait for the intro cinematic to fully land before
  injecting (page-time, not recording-time), never run two GPU headless
  instances in parallel (they crash each other), and equalize subject size by
  dollying the smaller-subject artifact IN rather than zooming the other out —
  disk brightness can fall off a cliff with distance (ep10 Qwen invisible at 66 Rs)

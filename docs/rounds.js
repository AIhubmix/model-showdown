// The Model Arena — round data (generated/appended by submit_round.py, hand-edit notes freely).
// Loaded as a classic script before the gallery logic in index.html; do not rename ROUNDS.
  const ROUNDS = [
    {
      id: 1, title: `Round 1 · Gargantua Redux`, episode: "ep11",
      summary: `Interstellar's black hole rendered in a single self-contained HTML file — a
      fullscreen ray-marched shader bending light around a Schwarzschild metric, procedural
      accretion disk, twin lensed rings, animated cinematic camera. Vote for the render you think
      looks best — model names, cost, and time all reveal the moment you vote.`,
      models: [
        { letter: "a", name: `Claude Fable 5`, accent: "#f97316", cost: "$0.91", time: "~4 min",
          note: `First try, no compile errors. Clean tone mapping keeps the disk's inner edge burning white while the shadow stays pure black.`,
          poster: "media/posters/round1-a.png", game: "games/round1/a.html" },
        { letter: "b", name: `GPT-5.6 Sol`, accent: "#a78bfa", cost: "$1.28", time: "~14 min",
          note: `Heaviest reasoning spend of the round. Dense filament detail on the accretion disk.`,
          poster: "media/posters/round1-b.png", game: "games/round1/b.html" },
        { letter: "c", name: `Kimi K3`, accent: "#38bdf8", cost: "$0.50", time: "~18 min",
          note: `Cheapest of the four. Simpler lensing rings but a solid, legible render.`,
          poster: "media/posters/round1-c.png", game: "games/round1/c.html" },
        { letter: "d", name: `Qwen3.8 Max`, accent: "#22c55e", cost: "~$0.18", time: "~35 min",
          note: `The prompt's exposure-discipline clause exists because of a run exactly like this one — the whole frame blew out white.`,
          poster: "media/posters/round1-d.png", game: "games/round1/d.html" },
      ],
    },
    {
      id: 2, title: `Round 2 · Fruit Ninja`, episode: "ep07",
      summary: `Build a Fruit Ninja–style slicer as one self-contained HTML file — itemized art
      checklist (wood-grain dojo, juice stains, glowing blade), reference screenshot attached,
      auto-demo required.`,
      models: [
        { letter: "a", name: `Kimi K3`, accent: "#38bdf8", cost: "$1.13", time: "42 min · 1275 lines",
          note: `Max-effort reasoning (always on). Thought for 35 minutes before writing a single line — 73% of its 75k output tokens were reasoning — then delivered the longest, juiciest build.`,
          poster: "media/posters/round2-a.png", game: "games/round2/a.html" },
        { letter: "b", name: `Claude Opus 4.8 · think`, accent: "#f97316", cost: "$0.55", time: "3.8 min · 1122 lines",
          note: `Extended thinking cost just $0.07 and 43 seconds more than the default run — and visibly upgraded the fruit cross-sections and combo feedback.`,
          poster: "media/posters/round2-b.png", game: "games/round2/b.html" },
        { letter: "c", name: `GPT-5.6 Sol`, accent: "#a78bfa", cost: "$0.39", time: "12 min · 946 lines",
          note: `Default mode. Cheapest run of the three and the most detailed juice physics — sliced pineapples keep their crowns.`,
          poster: "media/posters/round2-c.png", game: "games/round2/c.html" },
      ],
    },
    {
      id: 3, title: `Round 3 · Crossy Road`, episode: "ep01",
      summary: `Build a Crossy Road–style endless hopper as one self-contained HTML file —
      procedurally generated lanes, cars, logs, trains — with an AI auto-play mode. First round
      we ever ran; all three shipped a working game on the first shot.`,
      models: [
        { letter: "a", name: `Kimi K3`, accent: "#38bdf8", cost: "$0.74", time: "9 min · 517 lines",
          note: `Plainest visuals of the three, but everything works. Its first attempt burned an entire 32k-token budget on pure reasoning and wrote zero code — we had to raise the cap to 100k.`,
          poster: "media/posters/round3-a.png", game: "games/round3/a.html" },
        { letter: "b", name: `Claude Opus 4.8`, accent: "#f97316", cost: "$0.24", time: "2.5 min · 597 lines",
          note: `Cheapest run of the round with clean pixel art — though its self-play AI kept walking into traffic.`,
          poster: "media/posters/round3-b.png", game: "games/round3/b.html" },
        { letter: "c", name: `GPT-5.6 Sol`, accent: "#a78bfa", cost: "$0.32", time: "2 min · 873 lines",
          note: `Fastest and best-looking of the round — its auto-play chicken learned to dodge its way past 40 rows.`,
          poster: "media/posters/round3-c.png", game: "games/round3/c.html" },
      ],
    },
    {
      id: 4, title: `Round 4 · Monument Valley`, episode: "ep04",
      summary: `Recreate a Monument Valley screenshot — isometric impossible architecture, rotating
      crank mechanic, pastel palette. Reference image attached, auto-demo required.`,
      models: [
        { letter: "a", name: `Claude Fable 5`, accent: "#38bdf8", cost: "$3.22", time: "15 min · 554 lines",
          note: `Priciest run of the round by far — spent it on a faithfully placed hexagonal loop, onion domes and a working crank puzzle, all traced back to the reference screenshot.`,
          poster: "media/posters/round4-a.png", game: "games/round4/a.html" },
        { letter: "b", name: `GPT-5.6 Sol`, accent: "#22c55e", cost: "$0.31", time: "10 min · 1004 lines",
          note: `Cheapest, fastest, and most lines of the round. "The Silent Path" — a complete Penrose loop with the princess waiting at the pressure plate.`,
          poster: "media/posters/round4-b.png", game: "games/round4/b.html" },
        { letter: "c", name: `Kimi K3`, accent: "#f97316", cost: "$1.50", time: "52 min · 534 lines",
          note: `Slowest run we've recorded — 86k reasoning tokens before a line of code, after several earlier attempts died to rate limits and timeouts. Got there in the end: "Chapter Complete — A Quiet Monument."`,
          poster: "media/posters/round4-c.png", game: "games/round4/c.html" },
      ],
    },
    {
      id: 5, title: `Round 5 · HexGL Racer`, episode: "ep08",
      summary: `Build a HexGL-style anti-gravity racer as one self-contained HTML file — sculpted
      metal track over a hazy dusk sky, cyan glow trim, orange boost strips, UnrealBloomPass, HUD
      lap timer and speed gauge. Screenshot-as-wallpaper visual bar, auto-demo required.`,
      models: [
        { letter: "a", name: `Claude Fable 5`, accent: "#f97316", cost: "$2.36", time: "10 min · 807 lines",
          note: `Shipped byte-for-byte identical to its raw output — ran clean first try. Loop-de-loop track geometry, exposure-disciplined bloom, full HUD (lap timer, speed gauge, hex vignette).`,
          poster: "media/posters/round5-a.png", game: "games/round5/a.html" },
        { letter: "b", name: `Qwen3.8 Max`, accent: "#38bdf8", cost: "$0.15", time: "14 min · 650 lines",
          note: `16× cheaper — but its raw output called a Three.js method that doesn't exist (<code>Vector3.addScaledVectors</code>, plural) in the track-spline generator. One-line manual patch to two chained <code>addScaledVector()</code> calls and it ran.`,
          poster: "media/posters/round5-b.png", game: "games/round5/b.html" },
      ],
    },
    {
      id: 6, title: `Round 6 · Global View`, episode: "ep13", cols: 2, aspect: "16/9",
      summary: `Build a live-data 3D flight-tracking dashboard as one self-contained HTML file —
      dark-navy hero globe with a real day/night terminator, dual-layer atmosphere bloom, glowing
      flight arcs, glassmorphic stat cards, a drag-to-orbit + scroll-to-zoom camera. Strict exposure
      and glassmorphism spec attached, auto-demo required.`,
      models: [
        { letter: "a", name: `Kimi K3`, accent: "#38bdf8", cost: "$0.52", time: "16 min · 846 lines",
          note: `Cheapest of the four, and the only one with zero interaction gaps — drag, wheel-zoom, and its own zoom buttons all just work. Clean terminator, if a touch flatter than the pricier builds.`,
          poster: "media/posters/round6-a.png", game: "games/round6/a.html" },
        { letter: "b", name: `GPT-5.6 Sol`, accent: "#a78bfa", cost: "$1.81", time: "14 min · 3887 lines",
          note: `Priciest and longest by far — almost 3,900 lines chasing every checklist item, down to animated plane markers riding bezier flight paths. Never wired up scroll-wheel zoom, only its +/- buttons — our recording script had to route around that gap.`,
          poster: "media/posters/round6-b.png", game: "games/round6/b.html" },
        { letter: "c", name: `Claude Fable 5`, accent: "#f97316", cost: "$1.51", time: "5 min · 839 lines",
          note: `Fastest of the paid builds — a fully dressed dashboard in 5 minutes, drag/wheel/buttons all functional out of the box. The globe overruns its allotted breathing room and pokes past the frame edge.`,
          poster: "media/posters/round6-c.png", game: "games/round6/c.html" },
        { letter: "d", name: `Gemini 3.6 Flash`, accent: "#34d399", cost: "$0.12", time: "1.3 min · 1314 lines",
          note: `Absurdly cheap and fast — 75 seconds, 12 cents, still a legible glass-card layout. But its own 2D toggle and zoom buttons silently no-op: OrbitControls recomputes the camera every frame and overwrites whatever those handlers just set.`,
          poster: "media/posters/round6-d.png", game: "games/round6/d.html" },
      ],
    },
  ];

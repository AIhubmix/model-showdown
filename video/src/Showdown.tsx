import React from 'react';
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type ModelResult = {
  name: string;
  cost: string; // "$1.63"
  timeS: number;
  video: string; // public/ relative, e.g. "ep01/kimi-k3.webm"
  verdict: string; // short English verdict chip
  accent: string;
  startFrom?: number; // 视频起播帧偏移：让首帧落在画面最丰富/对比最强的一刻
};

export type ShowdownProps = {
  title: string;
  subtitle: string;
  models: ModelResult[];
  tagline: string;
  introFrames: number;
  playFrames: number;
  outroFrames: number;
  audio?: string; // public/ relative path to an audio bed, e.g. "ep02/audio.wav"
  layout?: 'horizontal' | 'vertical'; // 面板并排(默认) 或 上下堆叠
};

const BG = '#0b0e14';
const CARD = '#151a24';
const TEXT = '#f2f5fa';
const MUTED = '#8b95a7';
const MONEY = '#22c55e';

const font =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
const mono = '"SF Mono", "JetBrains Mono", Menlo, monospace';

const TitleCard: React.FC<{ title: string; subtitle: string }> = ({ title, subtitle }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const in1 = spring({ frame, fps, config: { damping: 200 } });
  const s = width / 1080;
  return (
    <AbsoluteFill
      style={{ background: BG, alignItems: 'center', justifyContent: 'center', fontFamily: font }}
    >
      <div
        style={{
          transform: `translateY(${(1 - in1) * 40}px)`,
          opacity: in1,
          textAlign: 'center',
          padding: 40 * s,
        }}
      >
        <div style={{ color: MUTED, fontSize: 34 * s, letterSpacing: 4, fontFamily: mono }}>
          SAME PROMPT · ONE SHOT · 3 MODELS
        </div>
        <div
          style={{
            color: TEXT,
            fontSize: 84 * s,
            fontWeight: 800,
            marginTop: 24 * s,
            lineHeight: 1.1,
          }}
        >
          {title}
        </div>
        <div style={{ color: MUTED, fontSize: 38 * s, marginTop: 24 * s }}>{subtitle}</div>
      </div>
    </AbsoluteFill>
  );
};

const CostBadge: React.FC<{ cost: string; scale: number }> = ({ cost, scale }) => (
  <div
    style={{
      background: 'rgba(34,197,94,0.12)',
      border: `${2 * scale}px solid rgba(34,197,94,0.4)`,
      color: MONEY,
      fontFamily: mono,
      fontWeight: 700,
      fontSize: 26 * scale,
      padding: `${5 * scale}px ${12 * scale}px`,
      borderRadius: 10 * scale,
    }}
  >
    {cost}
  </div>
);

const Panel: React.FC<{
  m: ModelResult;
  w: number;
  h: number;
  scale: number;
  delay: number;
  noEnter?: boolean;
}> = ({ m, w, h, scale, delay, noEnter }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // noEnter：跳过淡入/位移，首帧即满显内容（避免第 0 帧是深色空背景）
  const enter = noEnter ? 1 : spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const headerH = 72 * scale;
  return (
    <div
      style={{
        width: w,
        transform: `translateY(${(1 - enter) * 60}px)`,
        opacity: enter,
        background: CARD,
        borderRadius: 20 * scale,
        overflow: 'hidden',
        border: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      <div
        style={{
          height: headerH,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: `0 ${18 * scale}px`,
          fontFamily: font,
        }}
      >
        <div
          style={{
            color: TEXT,
            fontSize: 26 * scale,
            fontWeight: 800,
            display: 'flex',
            alignItems: 'center',
            gap: 8 * scale,
            whiteSpace: 'nowrap',
          }}
        >
          <div
            style={{
              width: 16 * scale,
              height: 16 * scale,
              borderRadius: '50%',
              background: m.accent,
            }}
          />
          {m.name}
        </div>
        <CostBadge cost={m.cost} scale={scale} />
      </div>
      <div style={{ width: w, height: h - headerH, background: '#000' }}>
        <OffthreadVideo
          src={staticFile(m.video)}
          muted
          startFrom={m.startFrom ?? 0}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      </div>
    </div>
  );
};

const Panels: React.FC<ShowdownProps> = ({ models, tagline, layout = 'horizontal' }) => {
  const { width, height } = useVideoConfig();
  const s = width / 1080;
  const brandH = tagline ? 110 * s : 0;
  const gap = 24 * s;
  const pad = 32 * s;
  const n = models.length;

  if (layout === 'vertical') {
    // 上下堆叠：每块占满宽度；视频区按 16:9 精确排布，横屏游戏画面完整不裁，整体竖向居中
    const w = width - pad * 2;
    const headerH = 72 * s;
    const avail = height - brandH - pad * 2 - gap * (n - 1);
    const idealH = (w * 9) / 16 + headerH;
    const h = Math.min(idealH, avail / n); // 放不下时回退到等分
    const stackH = h * n + gap * (n - 1);
    return (
      <AbsoluteFill style={{ background: BG }}>
        <div
          style={{
            position: 'absolute',
            top: (height - brandH - stackH) / 2,
            left: pad,
            display: 'flex',
            flexDirection: 'column',
            gap,
          }}
        >
          {models.map((m, i) => (
            <Panel key={m.name} m={m} w={w} h={h} scale={s} delay={i * 5} noEnter />
          ))}
        </div>
      </AbsoluteFill>
    );
  }

  const w = (width - pad * 2 - gap * (n - 1)) / n;
  const h = Math.min(height - brandH - pad * 2, (w * 4) / 3 + 72 * s);
  return (
    <AbsoluteFill style={{ background: BG }}>
      <div
        style={{
          position: 'absolute',
          top: (height - brandH - h) / 2,
          left: pad,
          display: 'flex',
          gap,
        }}
      >
        {models.map((m, i) => (
          <Panel key={m.name} m={m} w={w} h={h} scale={s} delay={i * 5} />
        ))}
      </div>
    </AbsoluteFill>
  );
};

const Scoreboard: React.FC<ShowdownProps> = ({ models }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const s = width / 1080;
  const maxCost = Math.max(...models.map((m) => parseFloat(m.cost.replace('$', ''))));
  const maxTime = Math.max(...models.map((m) => m.timeS));
  return (
    <AbsoluteFill
      style={{
        background: BG,
        fontFamily: font,
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{ width: width * 0.82 }}>
        <div
          style={{
            color: TEXT,
            fontSize: 54 * s,
            fontWeight: 800,
            marginBottom: 40 * s,
            textAlign: 'center',
          }}
        >
          The bill
        </div>
        {models.map((m, i) => {
          const cost = parseFloat(m.cost.replace('$', ''));
          const grow = spring({ frame: frame - i * 6, fps, config: { damping: 200 } });
          return (
            <div key={m.name} style={{ marginBottom: 34 * s }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  color: TEXT,
                  fontSize: 30 * s,
                  fontWeight: 700,
                  marginBottom: 10 * s,
                }}
              >
                <span>{m.name}</span>
                <span style={{ fontFamily: mono, color: MUTED }}>
                  {m.cost} · {Math.round(m.timeS)}s ·{' '}
                  <span style={{ color: m.accent }}>{m.verdict}</span>
                </span>
              </div>
              <div
                style={{
                  height: 22 * s,
                  background: 'rgba(255,255,255,0.06)',
                  borderRadius: 11 * s,
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${(cost / maxCost) * 100 * grow}%`,
                    background: m.accent,
                    borderRadius: 11 * s,
                  }}
                />
              </div>
            </div>
          );
        })}
        <div
          style={{
            color: MUTED,
            fontSize: 26 * s,
            textAlign: 'center',
            marginTop: 20 * s,
            fontFamily: mono,
          }}
        >
          bar = API cost · same prompt, one shot, via one gateway
        </div>
      </div>
    </AbsoluteFill>
  );
};

const BrandBar: React.FC<{ tagline: string }> = ({ tagline }) => {
  const { width, height } = useVideoConfig();
  const s = width / 1080;
  const h = 110 * s;
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: height - h,
        height: h,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 20 * s,
        background: 'rgba(255,255,255,0.03)',
        borderTop: '1px solid rgba(255,255,255,0.07)',
        fontFamily: font,
      }}
    >
      <img src={staticFile('logo.png')} style={{ height: 56 * s, borderRadius: 12 * s }} />
      <div style={{ color: TEXT, fontSize: 36 * s, fontWeight: 800, letterSpacing: 1 }}>
        {tagline}
      </div>
    </div>
  );
};

export const Showdown: React.FC<ShowdownProps> = (props) => {
  const { introFrames, playFrames, outroFrames } = props;
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const fadeOut = interpolate(frame, [durationInFrames - 15, durationInFrames], [1, 0], {
    extrapolateLeft: 'clamp',
  });
  const audioVolume = (f: number) =>
    interpolate(f, [0, 15, durationInFrames - 45, durationInFrames], [0, 1, 1, 0], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  return (
    <AbsoluteFill style={{ background: BG, opacity: fadeOut }}>
      {props.audio ? <Audio src={staticFile(props.audio)} volume={audioVolume} /> : null}
      {introFrames > 0 ? (
        <Sequence durationInFrames={introFrames}>
          <TitleCard title={props.title} subtitle={props.subtitle} />
        </Sequence>
      ) : null}
      <Sequence from={introFrames} durationInFrames={playFrames}>
        <Panels {...props} />
      </Sequence>
      <Sequence from={introFrames + playFrames} durationInFrames={outroFrames}>
        <Scoreboard {...props} />
      </Sequence>
      {props.tagline ? <BrandBar tagline={props.tagline} /> : null}
    </AbsoluteFill>
  );
};

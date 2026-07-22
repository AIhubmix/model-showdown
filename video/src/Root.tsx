import React from 'react';
import { Composition } from 'remotion';
import { Showdown, ShowdownProps } from './Showdown';

const FPS = 30;

const defaultProps: ShowdownProps = {
  title: 'Build Crossy Road in one prompt',
  subtitle: 'Kimi K3 vs Claude Opus 4.8 vs GPT-5.6',
  tagline: 'AIHubMix: Unified API, 800+ models',
  introFrames: 60,
  playFrames: 600,
  outroFrames: 130,
  models: [
    {
      name: 'Kimi K3',
      cost: '$0.00',
      timeS: 0,
      video: 'ep01/kimi-k3.webm',
      verdict: 'TBD',
      accent: '#38bdf8',
    },
    {
      name: 'Claude Opus 4.8',
      cost: '$0.00',
      timeS: 0,
      video: 'ep01/claude-opus-4-8.webm',
      verdict: 'TBD',
      accent: '#f97316',
    },
    {
      name: 'GPT-5.6 Sol',
      cost: '$0.00',
      timeS: 0,
      video: 'ep01/gpt-5.6-sol.webm',
      verdict: 'TBD',
      accent: '#a78bfa',
    },
  ],
};

const ep13GridProps: ShowdownProps = {
  title: 'Global View: build a live-data 3D dashboard in one prompt',
  subtitle: 'Kimi K3 vs GPT-5.6 Sol vs Claude Fable 5 vs Gemini 3.6 Flash',
  tagline: 'AIHubMix: Unified API, 800+ models',
  introFrames: 0,
  playFrames: 780,
  outroFrames: 130,
  audio: 'ep13/audio.wav',
  layout: 'fullbleed',
  fullbleedDir: 'grid2x2',
  models: [
    {
      name: 'Kimi K3',
      cost: '$0.52',
      timeS: 968,
      video: 'ep13/orbit-wide-kimi-k3.mp4',
      verdict: 'TBD',
      accent: '#38bdf8',
    },
    {
      name: 'GPT-5.6 Sol',
      cost: '$1.81',
      timeS: 833,
      video: 'ep13/orbit-wide-gpt-5.6-sol.mp4',
      verdict: 'TBD',
      accent: '#a78bfa',
    },
    {
      name: 'Claude Fable 5',
      cost: '$1.51',
      timeS: 310,
      video: 'ep13/orbit-wide-claude-fable-5.mp4',
      verdict: 'TBD',
      accent: '#f97316',
    },
    {
      name: 'Gemini 3.6 Flash',
      cost: '$0.12',
      timeS: 75,
      video: 'ep13/orbit-wide-gemini-3.6-flash.mp4',
      verdict: 'TBD',
      accent: '#34d399',
    },
  ],
};

const total = (p: ShowdownProps) => p.introFrames + p.playFrames + p.outroFrames;

export const Root: React.FC = () => (
  <>
    <Composition
      id="ShowdownSquare"
      component={Showdown}
      width={1080}
      height={1080}
      fps={FPS}
      durationInFrames={total(defaultProps)}
      defaultProps={defaultProps}
      calculateMetadata={({ props }) => ({ durationInFrames: total(props) })}
    />
    <Composition
      id="ShowdownWide"
      component={Showdown}
      width={1920}
      height={1080}
      fps={FPS}
      durationInFrames={total(defaultProps)}
      defaultProps={defaultProps}
      calculateMetadata={({ props }) => ({ durationInFrames: total(props) })}
    />
    <Composition
      id="ShowdownVertical"
      component={Showdown}
      width={1080}
      height={1920}
      fps={FPS}
      durationInFrames={total(defaultProps)}
      defaultProps={{ ...defaultProps, layout: 'vertical' }}
      calculateMetadata={({ props }) => ({ durationInFrames: total(props) })}
    />
    <Composition
      id="ShowdownStack3"
      component={Showdown}
      width={1080}
      height={1824}
      fps={FPS}
      durationInFrames={total(defaultProps)}
      defaultProps={{ ...defaultProps, layout: 'fullbleed', fullbleedDir: 'row' }}
      calculateMetadata={({ props }) => ({ durationInFrames: total(props) })}
    />
    <Composition
      id="ShowdownGrid2x2"
      component={Showdown}
      width={1920}
      height={1080}
      fps={FPS}
      durationInFrames={total(ep13GridProps)}
      defaultProps={ep13GridProps}
      calculateMetadata={({ props }) => ({ durationInFrames: total(props) })}
    />
  </>
);

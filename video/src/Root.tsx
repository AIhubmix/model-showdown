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
  </>
);

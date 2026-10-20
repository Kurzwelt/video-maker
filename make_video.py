"""
make_video.py (v4 kinetic) - topic do, har baar NAYA format + NAYA look ke sath vertical video (out.mp4) pao.

Usage:  python make_video.py "history of coffee"

Pipeline:
  Gemini (2 steps) -> research brief (Google Search grounding) -> script in a random FORMAT
  Wikipedia        -> real photos (free, no key); Pollinations AI picture / emoji as fallback
  Edge TTS         -> voice + word timings
  Remotion         -> 5 random looks, 8 scene types, always-moving graphics, whoosh sounds -> out.mp4

Env: GEMINI_API_KEY (required), VOICE, LANGUAGE, GEMINI_MODEL (optional),
     FORMAT (optional: mystery|countdown|myth_vs_fact|journey|numbers|cause_chain|how_it_works),
     STYLE (optional: 0-4), NO_IMAGES=1 (skip pictures).
"""
import asyncio
import random
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

FPS = 30
ROOT = Path(__file__).parent.resolve()
VIDEO = ROOT / "video"
PUBLIC = VIDEO / "public"
VOICE = os.environ.get("VOICE") or "en-US-AriaNeural"
LANGUAGE = os.environ.get("LANGUAGE") or "English"
MODELS = [os.environ.get("GEMINI_MODEL"), "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
LAYOUTS = ["title", "cutout", "map", "timeline", "stat", "bigtext", "versus", "list"]
PIC_LAYOUTS = ("title", "cutout", "stat")
RTL_LANGS = ("urdu", "arabic", "persian", "farsi", "hebrew")
THEME_NAMES = ["neon", "daylight", "brutal", "blueprint", "sunset"]
UA = {"User-Agent": "video-maker/1.0 (https://github.com/Kurzwelt/video-maker; personal educational project)"}
IMAGE_STYLE = "flat vector illustration, bold simple shapes, vivid colors, clean background, single subject, no text, no letters"

FORMATS = {
    "mystery": "A MYSTERY / INVESTIGATION. Open with a baffling paradox or question. Reveal clues one by one, build tension, deliver a twist, then the answer. Layout mix: bigtext, cutout, stat, map (only if places matter), timeline (optional).",
    "countdown": "A COUNTDOWN. 'The N things about the topic' ordered from least to most shocking (4-5 items). Each item gets its own scene (kicker 'NUMBER 4', 'NUMBER 3'...), then ONE final scene with layout list that recaps the ranking (3-5 items). Layout mix: bigtext hook, cutout/stat per item, list recap.",
    "myth_vs_fact": "MYTH vs FACT. Start from a popular belief people are sure about. Use the versus layout 2-3 times (side a = Myth, side b = Fact) and back them up with stat, cutout or timeline scenes. End with what to believe now. Layout mix: bigtext, versus, versus, stat, cutout.",
    "journey": "A JOURNEY THROUGH PLACES AND TIME. Follow the topic from origin to today. Layout mix: cutout, map, timeline, map, stat, bigtext. Use real places and dates.",
    "numbers": "A NUMBERS SHOCK. Build around 3-4 astonishing, certain numbers (stat layout), compare things with versus, and explain each number right after it. Layout mix: bigtext, stat, cutout, versus, stat, list.",
    "cause_chain": "A DOMINO CHAIN of cause and effect: 'because of this -> that -> that'. Each scene is one link and ends with a hook into the next link. End with the consequence we live with today. Layout mix: bigtext, cutout, timeline, cutout, versus, bigtext.",
    "how_it_works": "A HOW-IT-WORKS EXPLAINER in simple words with everyday analogies. Show the steps with a list layout, compare with versus, show real examples with cutout, one stat for scale. Layout mix: bigtext, cutout, list, versus, stat, cutout.",
}
TONES = [
    "gripping documentary narrator: calm, serious, cinematic",
    "witty smart friend: playful, a little sarcastic, very clear",
    "urgent breaking-news energy: fast, punchy, high stakes",
    "curious storyteller: warm, wondering, building suspense",
]

# ---------------------------------------------------------------- Remotion project files
PACKAGE_JSON = r"""{
  "name": "documentary",
  "private": true,
  "dependencies": {
    "@remotion/cli": "4.0.250",
    "remotion": "4.0.250",
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "topojson-client": "3.1.0",
    "world-atlas": "2.0.2"
  },
  "devDependencies": {
    "@types/react": "18.3.3",
    "typescript": "5.5.4"
  }
}
"""

INDEX_TS = r"""import {registerRoot} from 'remotion';
import {Root} from './Root';

registerRoot(Root);
"""

ROOT_TSX = r"""import React from 'react';
import {Composition} from 'remotion';
import {Documentary, Props} from './Video';

export const Root: React.FC = () => {
  return (
    <Composition
      id="Documentary"
      component={Documentary}
      durationInFrames={300}
      fps={30}
      width={720}
      height={1280}
      defaultProps={{title: 'Demo', rtl: false, theme: 0, scenes: []} as Props}
      calculateMetadata={({props}) => ({
        durationInFrames: Math.max(
          1,
          props.scenes.reduce((a: number, s: any) => a + s.frames, 0)
        ),
      })}
    />
  );
};
"""

LAND_TS = r"""// @ts-nocheck
import {feature, mesh} from 'topojson-client';
import world from 'world-atlas/countries-50m.json';

// Square Web-Mercator world, WM x WM "map pixels".
export const WM = 3600;

export const project = (lon: number, lat: number): [number, number] => {
  const la = Math.max(-82, Math.min(83, lat));
  const x = ((lon + 180) / 360) * WM;
  const y = (0.5 - Math.log(Math.tan(Math.PI / 4 + (la * Math.PI) / 360)) / (2 * Math.PI)) * WM;
  return [x, y];
};

const r1 = (n: number) => Math.round(n * 10) / 10;

// drop points closer than ~0.8 map px: much lighter path, same look
const toPath = (pts: number[][], close: boolean) => {
  let d = '';
  let lx = -1e9;
  let ly = -1e9;
  for (let i = 0; i < pts.length; i++) {
    const [x, y] = project(pts[i][0], pts[i][1]);
    const last = i === pts.length - 1;
    if (i > 0 && !last && Math.abs(x - lx) < 0.8 && Math.abs(y - ly) < 0.8) continue;
    d += (d ? 'L' : 'M') + r1(x) + ' ' + r1(y);
    lx = x;
    ly = y;
  }
  return d + (close ? 'Z' : '');
};

const maxLat = (ring: number[][]) => {
  let m = -90;
  for (const p of ring) if (p[1] > m) m = p[1];
  return m;
};

const build = () => {
  const objects = (world as any).objects;
  const f: any = feature(world as any, objects.land);
  const geoms = f.type === 'FeatureCollection' ? f.features.map((x: any) => x.geometry) : [f.geometry];
  let land = '';
  for (const g of geoms) {
    const polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
    for (const poly of polys) {
      if (maxLat(poly[0]) < -60) continue; // skip Antarctica
      for (const ring of poly) land += toPath(ring, true);
    }
  }
  let borders = '';
  const m: any = mesh(world as any, objects.countries, (a: any, b: any) => a !== b);
  for (const line of m.coordinates) {
    if (maxLat(line) < -60) continue;
    borders += toPath(line, false);
  }
  return {land, borders};
};

const built = build();
export const LAND: string = built.land;
export const BORDERS: string = built.borders;
"""

VIDEO_TSX = r"""import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {LAND, BORDERS, project, WM} from './Land';

export type Word = {text: string; frame: number};
export type Loc = {name: string; note: string; lat: number; lon: number};
export type Ev = {year: string; text: string};
export type Stat = {value: number; prefix: string; suffix: string; label: string};
export type Item = {rank: string; label: string; emoji: string; note: string};
export type Side = {name: string; emoji: string; line: string};
export type Vs = {a: Side; b: Side};
export type Scene = {
  layout: 'title' | 'cutout' | 'map' | 'timeline' | 'stat' | 'bigtext' | 'versus' | 'list';
  kicker: string;
  heading: string;
  sub: string;
  emoji: string;
  image: string;
  imgKind: string; // 'photo' | 'ai' | ''
  tags: string[];
  locations: Loc[];
  events: Ev[];
  stat: Stat | null;
  items: Item[];
  vs: Vs | null;
  audio: string;
  words: Word[];
  frames: number;
};
export type Props = {title: string; rtl: boolean; theme: number; scenes: Scene[]};

// ------------------------------------------------------------- themes (a new look per video)
type Theme = {
  name: string;
  bg: [string, string];
  text: string;
  sub: string;
  accents: string[];
  onAccent: string;
  panel: string;
  panelText: string;
  border: string;
  shadow: string;
  kind: 'blobs' | 'stripes' | 'halftone' | 'grid';
  blobs: [string, string];
  anim: 'pop' | 'rise' | 'flip';
  trans: 'slide' | 'iris' | 'diag';
  ocean: string;
  land: string;
  landLine: string;
  grat: string;
  mapShade: string;
};

const THEMES: Theme[] = [
  {
    name: 'neon',
    bg: ['#080d2a', '#1b1458'],
    text: '#ffffff',
    sub: 'rgba(255,255,255,0.82)',
    accents: ['#ffd23f', '#ff5d8f', '#35e0c8', '#ff9f43'],
    onAccent: '#0a0f2c',
    panel: 'rgba(255,255,255,0.1)',
    panelText: '#ffffff',
    border: 'rgba(255,255,255,0.28)',
    shadow: '0 14px 30px rgba(0,0,0,0.35)',
    kind: 'blobs',
    blobs: ['#5b4bff', '#ff3d81'],
    anim: 'pop',
    trans: 'slide',
    ocean: '#0c1a46',
    land: '#2a3a80',
    landLine: '#6a7df0',
    grat: 'rgba(120,150,255,0.16)',
    mapShade: 'rgba(5,8,30,0.7)',
  },
  {
    name: 'daylight',
    bg: ['#fff7e6', '#ffe3c2'],
    text: '#14142b',
    sub: 'rgba(20,20,43,0.78)',
    accents: ['#ff4d2e', '#2b59ff', '#00a878', '#ff9f1c'],
    onAccent: '#ffffff',
    panel: '#ffffff',
    panelText: '#14142b',
    border: '#14142b',
    shadow: '7px 7px 0 #14142b',
    kind: 'stripes',
    blobs: ['#ff4d2e', '#2b59ff'],
    anim: 'rise',
    trans: 'iris',
    ocean: '#bfe3ff',
    land: '#fff3d6',
    landLine: '#14142b',
    grat: 'rgba(20,20,43,0.12)',
    mapShade: 'rgba(60,30,0,0.28)',
  },
  {
    name: 'brutal',
    bg: ['#ffe14d', '#ffd21f'],
    text: '#111111',
    sub: 'rgba(17,17,17,0.82)',
    accents: ['#e11d48', '#1d4ed8', '#111111', '#059669'],
    onAccent: '#ffffff',
    panel: '#ffffff',
    panelText: '#111111',
    border: '#111111',
    shadow: '8px 8px 0 #111111',
    kind: 'halftone',
    blobs: ['#e11d48', '#1d4ed8'],
    anim: 'flip',
    trans: 'diag',
    ocean: '#7cc4ff',
    land: '#fff6c9',
    landLine: '#111111',
    grat: 'rgba(0,0,0,0.12)',
    mapShade: 'rgba(60,40,0,0.3)',
  },
  {
    name: 'blueprint',
    bg: ['#0b3d91', '#072a66'],
    text: '#ffffff',
    sub: 'rgba(255,255,255,0.85)',
    accents: ['#ffe14d', '#7df9ff', '#ff8fab', '#b6ff6b'],
    onAccent: '#06214f',
    panel: 'rgba(255,255,255,0.1)',
    panelText: '#ffffff',
    border: 'rgba(255,255,255,0.65)',
    shadow: '0 14px 30px rgba(0,0,30,0.35)',
    kind: 'grid',
    blobs: ['#2b7bff', '#00c2ff'],
    anim: 'rise',
    trans: 'slide',
    ocean: '#072a66',
    land: '#0f4bb5',
    landLine: '#8fb8ff',
    grat: 'rgba(160,200,255,0.2)',
    mapShade: 'rgba(0,10,50,0.7)',
  },
  {
    name: 'sunset',
    bg: ['#ff6a3d', '#7b2ff7'],
    text: '#ffffff',
    sub: 'rgba(255,255,255,0.88)',
    accents: ['#fff176', '#00e5ff', '#ffd1dc', '#b9ff66'],
    onAccent: '#2a0a4a',
    panel: 'rgba(255,255,255,0.16)',
    panelText: '#ffffff',
    border: 'rgba(255,255,255,0.55)',
    shadow: '0 14px 30px rgba(40,0,70,0.35)',
    kind: 'blobs',
    blobs: ['#ffb300', '#ff2d95'],
    anim: 'flip',
    trans: 'iris',
    ocean: '#3b1a7a',
    land: '#8e44e8',
    landLine: '#ffd1ff',
    grat: 'rgba(255,255,255,0.15)',
    mapShade: 'rgba(30,0,60,0.6)',
  },
];

const ThemeCtx = React.createContext<Theme>(THEMES[0]);
const useT = () => React.useContext(ThemeCtx);

const BRIGHT = ['#ffd23f', '#ff5d8f', '#35e0c8', '#ff9f43'];
const DARK = '#0a0f2c';
const SANS = "'Noto Sans','Noto Sans Arabic','Noto Naskh Arabic','Noto Nastaliq Urdu','DejaVu Sans',Arial,sans-serif";
const EMOJI = "'Noto Color Emoji','Apple Color Emoji','Segoe UI Emoji',sans-serif";

const OV = 16; // frames the next scene takes to come in

const clamp01 = (n: number) => Math.max(0, Math.min(1, n));
const clamp = (n: number, a: number, b: number) => Math.max(a, Math.min(b, n));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const ease = (t: number) => Easing.inOut(Easing.cubic)(clamp01(t));
const easeOut = (t: number) => Easing.out(Easing.cubic)(clamp01(t));

const sp = (frame: number, fps: number, delay = 0, damping = 12, stiffness = 120) =>
  spring({frame: frame - delay, fps, config: {damping, stiffness}});

const Emoji: React.FC<{c: string; s: number}> = ({c, s}) => (
  <span style={{fontFamily: EMOJI, fontSize: s, lineHeight: 1}}>{c}</span>
);

// ------------------------------------------------------------- always-moving background
const Bg: React.FC<{index: number}> = ({index}) => {
  const frame = useCurrentFrame();
  const T = useT();
  const a = T.accents[index % T.accents.length];
  const ang = 150 + Math.sin(frame / 90) * 25 + index * 40;
  const base = `linear-gradient(${ang}deg, ${T.bg[0]}, ${T.bg[1]})`;

  if (T.kind === 'halftone') {
    const dots = [];
    for (let j = 0; j < 22; j++) {
      for (let i = 0; i < 12; i++) {
        const r = 3 + 9 * (0.5 + 0.5 * Math.sin((i + j) * 0.65 - frame / 9));
        dots.push(<circle key={i + '_' + j} cx={i * 64 + (j % 2) * 32} cy={j * 64} r={r} fill="rgba(0,0,0,0.13)" />);
      }
    }
    return (
      <AbsoluteFill style={{background: base}}>
        <svg width="100%" height="100%" style={{position: 'absolute', left: 0, top: 0}}>
          {dots}
        </svg>
      </AbsoluteFill>
    );
  }

  if (T.kind === 'grid') {
    const rot = frame * 0.6;
    return (
      <AbsoluteFill style={{background: base}}>
        <svg width="100%" height="100%" style={{position: 'absolute', left: 0, top: 0}}>
          <defs>
            <pattern
              id={`grid${index}`}
              width={80}
              height={80}
              patternUnits="userSpaceOnUse"
              patternTransform={`translate(${frame * 0.8} ${frame * 0.6})`}
            >
              <path d="M80 0H0V80" fill="none" stroke="rgba(255,255,255,0.15)" strokeWidth={2} />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill={`url(#grid${index})`} />
          <g transform={`rotate(${rot} 360 640)`}>
            <circle cx={360} cy={640} r={300} fill="none" stroke={a} strokeOpacity={0.35} strokeWidth={3} strokeDasharray="14 18" />
          </g>
          <g transform={`rotate(${-rot * 0.7} 360 640)`}>
            <circle cx={360} cy={640} r={470} fill="none" stroke="#fff" strokeOpacity={0.2} strokeWidth={2} strokeDasharray="4 22" />
          </g>
          <path d="M360 0V1280M0 640H720" stroke="rgba(255,255,255,0.12)" strokeWidth={2} />
        </svg>
      </AbsoluteFill>
    );
  }

  if (T.kind === 'stripes') {
    const circles = [0, 1, 2, 3].map((i) => ({
      x: 360 + Math.sin(frame / 80 + i * 1.9) * 300,
      y: 200 + i * 330 + Math.cos(frame / 70 + i * 2.3) * 120,
      c: T.accents[(index + i) % T.accents.length],
    }));
    return (
      <AbsoluteFill style={{background: base}}>
        {circles.map((c, i) => (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: c.x - 200,
              top: c.y - 200,
              width: 400,
              height: 400,
              borderRadius: '50%',
              background: c.c,
              opacity: 0.16,
            }}
          />
        ))}
        <svg width="100%" height="100%" style={{position: 'absolute', left: 0, top: 0}}>
          <defs>
            <pattern
              id={`str${index}`}
              width={70}
              height={70}
              patternUnits="userSpaceOnUse"
              patternTransform={`rotate(45) translate(${frame * 1.1} 0)`}
            >
              <rect width={35} height={70} fill="rgba(20,20,43,0.05)" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill={`url(#str${index})`} />
        </svg>
      </AbsoluteFill>
    );
  }

  // blobs (neon / sunset)
  const blobs = [0, 1, 2].map((i) => {
    const x = 360 + Math.sin(frame / 70 + i * 2.1 + index) * 300;
    const y = 220 + i * 420 + Math.cos(frame / 80 + i * 1.7) * 170;
    return {x, y, c: i === 1 ? a : T.blobs[i === 0 ? 0 : 1]};
  });
  const parts = [];
  for (let i = 0; i < 14; i++) {
    const speed = 0.5 + ((i * 37) % 10) / 8;
    const y = 1360 - ((frame * speed * 1.6 + i * 131) % 1480);
    const x = ((i * 97) % 700) + Math.sin(frame / 40 + i) * 24;
    parts.push(<circle key={i} cx={x} cy={y} r={3 + (i % 4) * 2} fill="#fff" opacity={0.12 + (i % 3) * 0.05} />);
  }
  return (
    <AbsoluteFill style={{background: base}}>
      {blobs.map((b, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: b.x - 380,
            top: b.y - 380,
            width: 760,
            height: 760,
            background: `radial-gradient(circle, ${b.c}55 0%, ${b.c}00 62%)`,
          }}
        />
      ))}
      <svg width="100%" height="100%" style={{position: 'absolute', left: 0, top: 0}}>
        <defs>
          <pattern
            id={`dots${index}`}
            width={46}
            height={46}
            patternUnits="userSpaceOnUse"
            patternTransform={`translate(${frame * 0.7} ${frame * 0.5})`}
          >
            <circle cx={3} cy={3} r={2} fill="rgba(255,255,255,0.13)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#dots${index})`} />
        {parts}
      </svg>
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------- text
const Kinetic: React.FC<{text: string; size: number; accent: string; color: string; delay?: number}> = ({
  text,
  size,
  accent,
  color,
  delay = 3,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const T = useT();
  const words: {t: string; hl: boolean}[] = [];
  text.split('*').forEach((seg, i) =>
    seg
      .split(/\s+/)
      .filter(Boolean)
      .forEach((w) => words.push({t: w, hl: i % 2 === 1})),
  );
  const shadow = color === '#ffffff' ? '0 6px 0 rgba(0,0,0,0.38)' : '0 4px 0 rgba(0,0,0,0.10)';
  return (
    <div
      style={{
        textAlign: 'center',
        fontFamily: SANS,
        fontWeight: 900,
        fontSize: size,
        lineHeight: 1.1,
        color,
        transform: `translateY(${Math.sin(frame / 22) * 5}px)`,
      }}
    >
      {words.map((w, i) => {
        const p = sp(frame, fps, delay + i * 4, 10, 170);
        const pulse = w.hl ? 1 + 0.045 * Math.sin(frame / 7) : 1;
        let tr = '';
        if (T.anim === 'pop') {
          tr = `translateY(${(1 - p) * 70}px) scale(${(0.45 + 0.55 * p) * pulse}) rotate(${(1 - p) * -9}deg)`;
        } else if (T.anim === 'rise') {
          tr = `translateY(${(1 - clamp01(p)) * 110}px) scale(${pulse})`;
        } else {
          tr = `perspective(500px) rotateX(${(1 - clamp01(p)) * 95}deg) scale(${pulse})`;
        }
        return (
          <span
            key={i}
            style={{
              display: 'inline-block',
              margin: '0 0.12em',
              color: w.hl ? accent : color,
              opacity: clamp01(p * 2),
              transform: tr,
              textShadow: shadow,
            }}
          >
            {w.t}
          </span>
        );
      })}
    </div>
  );
};

const headSize = (h: string, big = 0) => {
  const n = h.replace(/\*/g, '').length;
  const s = n <= 18 ? 88 : n <= 30 ? 74 : n <= 42 ? 62 : 54;
  return big ? Math.min(big, s + 14) : s;
};

const TextBlock: React.FC<{
  kicker: string;
  heading: string;
  sub?: string;
  top: number;
  accent: string;
  size?: number;
  dark?: boolean; // sits on a photo / dark backdrop
}> = ({kicker, heading, sub, top, accent, size, dark}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const T = useT();
  const kp = sp(frame, fps, 0, 12, 170);
  const sp2 = sp(frame, fps, 22, 16, 90);
  const words = heading.replace(/\*/g, '').split(/\s+/).length;
  const color = dark ? '#ffffff' : T.text;
  const chipText = dark ? DARK : T.onAccent;
  return (
    <div style={{position: 'absolute', top, left: 36, right: 36, textAlign: 'center'}}>
      {kicker ? (
        <div
          style={{
            display: 'inline-block',
            fontFamily: SANS,
            fontWeight: 900,
            fontSize: 24,
            letterSpacing: 5,
            textTransform: 'uppercase',
            color: chipText,
            background: accent,
            padding: '6px 20px',
            borderRadius: 40,
            marginBottom: 16,
            transform: `scale(${clamp01(kp)}) rotate(${(1 - clamp01(kp)) * -6}deg)`,
          }}
        >
          {kicker}
        </div>
      ) : null}
      <Kinetic text={heading} size={size || headSize(heading)} accent={accent} color={color} delay={4 + (words > 5 ? 0 : 2)} />
      {sub ? (
        <div
          style={{
            fontFamily: SANS,
            fontWeight: 600,
            fontSize: 31,
            lineHeight: 1.3,
            color: dark ? 'rgba(255,255,255,0.85)' : T.sub,
            marginTop: 16,
            opacity: clamp01(sp2),
            transform: `translateY(${(1 - clamp01(sp2)) * 24 + Math.sin(frame / 26) * 3}px)`,
          }}
        >
          {sub}
        </div>
      ) : null}
    </div>
  );
};

// word-synced captions: the spoken word pops
const Captions: React.FC<{scene: Scene; accent: string}> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const CHUNK = 3;
  let active = 0;
  scene.words.forEach((w, i) => {
    if (frame >= w.frame) active = i;
  });
  const start = Math.floor(active / CHUNK) * CHUNK;
  const chunk = scene.words.slice(start, start + CHUNK);
  if (!chunk.length) return null;
  const shadow =
    '-4px -4px 0 #05081c, 4px -4px 0 #05081c, -4px 4px 0 #05081c, 4px 4px 0 #05081c, 0 8px 14px rgba(0,0,0,0.55)';
  return (
    <div style={{position: 'absolute', bottom: 120, left: 24, right: 24, textAlign: 'center'}}>
      {chunk.map((w, i) => {
        const idx = start + i;
        const on = idx === active;
        const p = sp(frame, fps, w.frame, 9, 240);
        const pop = on ? 1 + 0.2 * Math.min(p, 1.2) : 1;
        return (
          <span
            key={idx}
            style={{
              display: 'inline-block',
              fontFamily: SANS,
              fontWeight: 900,
              fontSize: 60,
              lineHeight: 1.2,
              margin: '0 10px',
              color: on ? accent : '#fff',
              textShadow: shadow,
              transform: `scale(${pop})`,
            }}
          >
            {w.text}
          </span>
        );
      })}
    </div>
  );
};

// ------------------------------------------------------------- picture pieces
const Shot: React.FC<{src: string; kind: string; frames: number; dir: number}> = ({src, kind, frames, dir}) => {
  const frame = useCurrentFrame();
  const t = clamp01(frame / Math.max(frames, 1));
  const s = 1.08 + 0.24 * t;
  const tx = (dir % 2 ? 1 : -1) * (t - 0.5) * 60;
  const ty = (dir % 3 === 0 ? -1 : 1) * (t - 0.5) * 50;
  return (
    <Img
      src={staticFile(src)}
      style={{
        position: 'absolute',
        left: 0,
        top: 0,
        width: '100%',
        height: kind === 'ai' ? '116%' : '100%', // pushes any corner watermark off screen
        objectFit: 'cover',
        transform: `translate(${tx}px, ${ty}px) scale(${s})`,
      }}
    />
  );
};

const Card: React.FC<{
  scene: Scene;
  index: number;
  size: number;
  rot: number;
  accent: string;
  delay?: number;
}> = ({scene, index, size, rot, accent, delay = 2}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const T = useT();
  const p = sp(frame, fps, delay, 11, 110);
  const sweep = ((frame * 9) % (size + 700)) - 350;
  return (
    <div
      style={{
        position: 'relative',
        width: size,
        height: size,
        opacity: clamp01(p * 2),
        transform: `translateY(${(1 - p) * 120 + Math.sin(frame / 24) * 9}px) rotate(${
          rot + Math.sin(frame / 38) * 1.8 - (1 - p) * 12
        }deg) scale(${0.6 + 0.4 * p})`,
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          right: 0,
          bottom: 0,
          borderRadius: 38,
          overflow: 'hidden',
          border: `7px solid ${accent}`,
          boxShadow: '0 30px 60px rgba(0,0,0,0.45)',
          background: `linear-gradient(135deg, ${T.bg[1]}, ${accent}55)`,
        }}
      >
        {scene.image ? (
          <Shot src={scene.image} kind={scene.imgKind} frames={scene.frames} dir={index} />
        ) : (
          <div style={{width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
            <div style={{transform: `scale(${1 + Math.sin(frame / 15) * 0.07}) rotate(${Math.sin(frame / 30) * 6}deg)`}}>
              <Emoji c={scene.emoji} s={size * 0.5} />
            </div>
          </div>
        )}
        <div
          style={{
            position: 'absolute',
            top: -100,
            left: sweep,
            width: 120,
            height: size + 300,
            transform: 'rotate(20deg)',
            background: 'linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,0.28), rgba(255,255,255,0))',
          }}
        />
      </div>
    </div>
  );
};

const Backdrop: React.FC<{scene: Scene; index: number}> = ({scene, index}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      {scene.image ? (
        <Shot src={scene.image} kind={scene.imgKind} frames={scene.frames} dir={index} />
      ) : (
        <div style={{position: 'absolute', left: 0, right: 0, top: 120, textAlign: 'center', opacity: 0.22}}>
          <div style={{transform: `translateY(${Math.sin(frame / 25) * 30}px) rotate(${Math.sin(frame / 40) * 8}deg)`}}>
            <Emoji c={scene.emoji} s={620} />
          </div>
        </div>
      )}
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to bottom, rgba(8,13,42,0.84), rgba(8,13,42,0.45) 45%, rgba(8,13,42,0.9))',
        }}
      />
    </AbsoluteFill>
  );
};

const Sticker: React.FC<{c: string; x: number; y: number; s: number; delay: number; rot: number}> = ({
  c,
  x,
  y,
  s,
  delay,
  rot,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = sp(frame, fps, delay, 8, 130);
  return (
    <div
      style={{
        position: 'absolute',
        left: x + Math.sin(frame / 30 + delay) * 14,
        top: y + Math.cos(frame / 24 + delay) * 18,
        transform: `scale(${clamp01(p)}) rotate(${rot + Math.sin(frame / 20 + delay) * 12}deg)`,
        filter: 'drop-shadow(0 10px 12px rgba(0,0,0,0.4))',
      }}
    >
      <Emoji c={c} s={s} />
    </div>
  );
};

// tags pop in one after another across the scene so something new keeps arriving
const Tags: React.FC<{
  tags: string[];
  frames: number;
  accent: string;
  on: string;
  spots: {x: number; y: number; r: number}[];
}> = ({tags, frames, accent, on, spots}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <>
      {tags.slice(0, spots.length).map((t, i) => {
        const at = Math.round(frames * (0.16 + i * 0.22));
        const p = sp(frame, fps, at, 8, 160);
        const fresh = clamp01(1 - (frame - at) / 14);
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: spots[i].x,
              top: spots[i].y + Math.sin(frame / 20 + i * 2) * 8,
              fontFamily: SANS,
              fontWeight: 900,
              fontSize: 31,
              color: i % 2 ? DARK : on,
              background: i % 2 ? '#fff' : accent,
              padding: '10px 24px',
              borderRadius: 14,
              boxShadow: `0 8px 0 rgba(0,0,0,0.35), 0 0 ${28 * fresh}px ${accent}`,
              transform: `rotate(${spots[i].r}deg) scale(${clamp01(p) * (1 + 0.12 * fresh)})`,
              opacity: clamp01(p * 2),
              whiteSpace: 'nowrap',
            }}
          >
            {t}
          </div>
        );
      })}
    </>
  );
};

// ------------------------------------------------------------- camera: never still
const Camera: React.FC<{frames: number; index: number; children: React.ReactNode}> = ({frames, index, children}) => {
  const frame = useCurrentFrame();
  const t = clamp01(frame / Math.max(frames, 1));
  const punch = 0.09 * (1 - easeOut(frame / 20));
  const dir = index % 3 === 0 ? -1 : 1;
  return (
    <AbsoluteFill
      style={{
        transform: `translate(${dir * t * 16}px, ${-t * 14}px) rotate(${Math.sin(frame / 70) * 0.5}deg) scale(${
          1 + 0.07 * t + punch
        })`,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------- layouts
const TitleScene: React.FC<{scene: Scene; index: number}> = ({scene, index}) => {
  const hi = BRIGHT[index % 4];
  return (
    <>
      <Backdrop scene={scene} index={index} />
      <Camera frames={scene.frames} index={index}>
        <TextBlock
          kicker={scene.kicker}
          heading={scene.heading}
          sub={scene.sub}
          top={280}
          accent={hi}
          dark
          size={headSize(scene.heading, 100)}
        />
        <Sticker c={scene.emoji} x={520} y={110} s={120} delay={14} rot={12} />
        <Sticker c={scene.emoji} x={30} y={900} s={90} delay={26} rot={-14} />
      </Camera>
    </>
  );
};

const CutoutScene: React.FC<{scene: Scene; index: number; accent: string}> = ({scene, index, accent}) => {
  const T = useT();
  const v = index % 3;
  const F = scene.frames;
  if (v === 1) {
    const hi = BRIGHT[index % 4];
    return (
      <>
        <Backdrop scene={scene} index={index} />
        <Camera frames={F} index={index}>
          <TextBlock kicker={scene.kicker} heading={scene.heading} sub={scene.sub} top={250} accent={hi} dark />
          <Tags
            tags={scene.tags}
            frames={F}
            accent={hi}
            on={DARK}
            spots={[
              {x: 40, y: 760, r: -5},
              {x: 390, y: 860, r: 4},
              {x: 70, y: 930, r: -2},
            ]}
          />
          <Sticker c={scene.emoji} x={540} y={90} s={110} delay={12} rot={10} />
        </Camera>
      </>
    );
  }
  if (v === 2) {
    return (
      <Camera frames={F} index={index}>
        <div style={{position: 'absolute', top: 70, left: 720 / 2 - 255}}>
          <Card scene={scene} index={index} size={510} rot={3} accent={accent} />
        </div>
        <Tags
          tags={scene.tags}
          frames={F}
          accent={accent}
          on={T.onAccent}
          spots={[
            {x: 24, y: 120, r: -6},
            {x: 470, y: 520, r: 5},
            {x: 30, y: 500, r: -3},
          ]}
        />
        <Sticker c={scene.emoji} x={560} y={60} s={100} delay={16} rot={14} />
        <TextBlock
          kicker={scene.kicker}
          heading={scene.heading}
          sub={scene.sub}
          top={640}
          accent={accent}
          size={Math.min(headSize(scene.heading), 66)}
        />
      </Camera>
    );
  }
  return (
    <Camera frames={F} index={index}>
      <TextBlock
        kicker={scene.kicker}
        heading={scene.heading}
        sub={scene.sub}
        top={70}
        accent={accent}
        size={Math.min(headSize(scene.heading), 70)}
      />
      <div style={{position: 'absolute', top: 520, left: 720 / 2 - 230}}>
        <Card scene={scene} index={index} size={460} rot={-3} accent={accent} delay={6} />
      </div>
      <Tags
        tags={scene.tags}
        frames={F}
        accent={accent}
        on={T.onAccent}
        spots={[
          {x: 16, y: 500, r: -6},
          {x: 470, y: 930, r: 5},
          {x: 26, y: 880, r: -3},
        ]}
      />
      <Sticker c={scene.emoji} x={570} y={470} s={100} delay={18} rot={12} />
    </Camera>
  );
};

// big kinetic statement with orbiting emoji
const BigText: React.FC<{scene: Scene; index: number; accent: string}> = ({scene, index, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const F = scene.frames;
  const orbit = [0, 1, 2, 3, 4, 5].map((k) => {
    const a = frame / 45 + (k * Math.PI * 2) / 6;
    const x = 360 + Math.cos(a) * 310;
    const y = 560 + Math.sin(a) * 390;
    const p = sp(frame, fps, 8 + k * 4, 10, 120);
    return (
      <div
        key={k}
        style={{
          position: 'absolute',
          left: x - 40,
          top: y - 40,
          opacity: 0.9 * clamp01(p),
          transform: `scale(${clamp01(p) * (0.8 + 0.25 * Math.sin(frame / 12 + k))}) rotate(${Math.sin(frame / 20 + k) * 25}deg)`,
          filter: 'drop-shadow(0 8px 10px rgba(0,0,0,0.3))',
        }}
      >
        <Emoji c={scene.emoji} s={80} />
      </div>
    );
  });
  const ring = (r: number, ph: number) => {
    const k = ((frame + ph) % 70) / 70;
    return (
      <circle
        cx={360}
        cy={560}
        r={r + k * 120}
        fill="none"
        stroke={accent}
        strokeWidth={6}
        opacity={(1 - k) * 0.55}
      />
    );
  };
  return (
    <Camera frames={F} index={index}>
      <svg width={720} height={1280} style={{position: 'absolute', left: 0, top: 0}}>
        {ring(120, 0)}
        {ring(120, 35)}
      </svg>
      {orbit}
      <TextBlock
        kicker={scene.kicker}
        heading={scene.heading}
        sub={scene.sub}
        top={350}
        accent={accent}
        size={headSize(scene.heading, 112)}
      />
    </Camera>
  );
};

const Versus: React.FC<{scene: Scene; index: number; accent: string}> = ({scene, index, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const T = useT();
  const F = scene.frames;
  const vs = scene.vs as Vs;
  const at = [Math.round(0.08 * F), Math.round(0.42 * F)];
  const sides = [vs.a, vs.b];
  const other = T.accents[(index + 1) % T.accents.length];
  const cols = [accent, other];
  const vsP = sp(frame, fps, Math.round(0.3 * F), 8, 140);
  const verdict = sp(frame, fps, Math.round(0.74 * F), 12, 110);
  return (
    <Camera frames={F} index={index}>
      <TextBlock kicker={scene.kicker} heading={scene.heading} top={70} accent={accent} size={54} />
      {sides.map((s, i) => {
        const p = sp(frame, fps, at[i], 12, 100);
        const y = 300 + i * 350;
        const glow = 0.5 + 0.5 * Math.sin(frame / 10 + i * 2);
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: 34,
              right: 34,
              top: y + Math.sin(frame / 25 + i) * 8,
              height: 280,
              background: T.panel,
              borderRadius: 32,
              border: `5px solid ${cols[i]}`,
              boxShadow: `${T.shadow}, 0 0 ${10 + 24 * glow}px ${cols[i]}66`,
              display: 'flex',
              flexDirection: 'row',
              alignItems: 'center',
              gap: 26,
              padding: '0 34px',
              transform: `translateX(${(1 - clamp01(p)) * (i ? 800 : -800)}px)`,
              opacity: clamp01(p * 3),
            }}
          >
            <div style={{transform: `scale(${1 + Math.sin(frame / 14 + i) * 0.08}) rotate(${Math.sin(frame / 24 + i) * 8}deg)`}}>
              <Emoji c={s.emoji} s={130} />
            </div>
            <div style={{flex: 1}}>
              <div style={{fontFamily: SANS, fontWeight: 900, fontSize: 44, lineHeight: 1.1, color: cols[i]}}>
                {s.name}
              </div>
              <div style={{fontFamily: SANS, fontWeight: 700, fontSize: 30, lineHeight: 1.25, color: T.panelText, marginTop: 10}}>
                {s.line}
              </div>
            </div>
          </div>
        );
      })}
      <div
        style={{
          position: 'absolute',
          left: 360 - 52,
          top: 586 - 0,
          width: 104,
          height: 104,
          borderRadius: '50%',
          background: accent,
          color: T.onAccent,
          fontFamily: SANS,
          fontWeight: 900,
          fontSize: 44,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          border: `6px solid ${T.panel === '#ffffff' ? T.border : '#fff'}`,
          transform: `scale(${clamp01(vsP) * (1 + 0.08 * Math.sin(frame / 7))}) rotate(${Math.sin(frame / 12) * 10}deg)`,
          zIndex: 5,
        }}
      >
        VS
      </div>
      {scene.sub ? (
        <div
          style={{
            position: 'absolute',
            left: 40,
            right: 40,
            top: 960,
            textAlign: 'center',
            opacity: clamp01(verdict),
            transform: `translateY(${(1 - clamp01(verdict)) * 40}px)`,
          }}
        >
          <span
            style={{
              display: 'inline-block',
              fontFamily: SANS,
              fontWeight: 900,
              fontSize: 32,
              color: T.onAccent,
              background: accent,
              padding: '8px 26px',
              borderRadius: 16,
            }}
          >
            {scene.sub}
          </span>
        </div>
      ) : null}
    </Camera>
  );
};

const ListScene: React.FC<{scene: Scene; index: number; accent: string}> = ({scene, index, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const T = useT();
  const F = scene.frames;
  const items = scene.items.slice(0, 5);
  const n = items.length;
  const top = 300;
  const rowH = Math.min(150, 650 / n);
  const span = 0.8 * F;
  const startAt = (i: number) => Math.round(0.08 * F + (i * span) / n);
  let activeI = 0;
  items.forEach((_, i) => {
    if (frame >= startAt(i)) activeI = i;
  });
  return (
    <Camera frames={F} index={index}>
      <TextBlock kicker={scene.kicker} heading={scene.heading} top={80} accent={accent} size={56} />
      {items.map((it, i) => {
        const p = sp(frame, fps, startAt(i), 11, 120);
        const isActive = i === activeI;
        const col = T.accents[(index + i) % T.accents.length];
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: 34,
              right: 34,
              top: top + i * rowH + Math.sin(frame / 22 + i) * 4,
              height: rowH - 16,
              background: T.panel,
              borderRadius: 26,
              border: `4px solid ${isActive ? col : T.border}`,
              boxShadow: isActive ? `${T.shadow}, 0 0 30px ${col}77` : T.shadow,
              display: 'flex',
              alignItems: 'center',
              gap: 18,
              padding: '0 22px',
              transform: `translateX(${(1 - clamp01(p)) * 820}px) scale(${isActive ? 1.03 + Math.sin(frame / 10) * 0.01 : 1})`,
              opacity: clamp01(p * 3) * (isActive ? 1 : 0.88),
            }}
          >
            <div
              style={{
                width: rowH - 44,
                height: rowH - 44,
                minWidth: rowH - 44,
                borderRadius: '50%',
                background: col,
                color: DARK,
                fontFamily: SANS,
                fontWeight: 900,
                fontSize: (rowH - 44) * 0.6,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                direction: 'ltr',
              }}
            >
              {it.rank || String(i + 1)}
            </div>
            <Emoji c={it.emoji || '⭐'} s={rowH * 0.42} />
            <div style={{flex: 1}}>
              <div style={{fontFamily: SANS, fontWeight: 900, fontSize: rowH > 110 ? 36 : 30, color: T.panelText, lineHeight: 1.1}}>
                {it.label}
              </div>
              {it.note ? (
                <div style={{fontFamily: SANS, fontWeight: 600, fontSize: rowH > 110 ? 25 : 22, color: col, marginTop: 4}}>
                  {it.note}
                </div>
              ) : null}
            </div>
          </div>
        );
      })}
    </Camera>
  );
};

const Timeline: React.FC<{scene: Scene; index: number; accent: string}> = ({scene, index, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const T = useT();
  const F = scene.frames;
  const evs = scene.events.slice(0, 4);
  const n = evs.length;
  const top = 330;
  const step = Math.min(190, 640 / n);
  const span = 0.78 * F;
  const startAt = (i: number) => Math.round(0.08 * F + (i * span) / n);
  let activeI = 0;
  evs.forEach((_, i) => {
    if (frame >= startAt(i)) activeI = i;
  });
  const lineH = activeI * step + clamp01((frame - startAt(activeI)) / 20) * step - 14;
  return (
    <Camera frames={F} index={index}>
      <TextBlock kicker={scene.kicker} heading={scene.heading} top={80} accent={accent} size={56} />
      <div
        style={{
          position: 'absolute',
          left: 56,
          top: top + 26,
          width: 8,
          height: Math.max(0, lineH),
          background: accent,
          borderRadius: 4,
          boxShadow: `0 0 24px ${accent}`,
        }}
      />
      {evs.map((e, i) => {
        const p = sp(frame, fps, startAt(i), 12, 110);
        const y = top + i * step;
        const small = e.year.length > 5;
        const isActive = i === activeI;
        const fromLeft = i % 2 === 0;
        return (
          <React.Fragment key={i}>
            <div
              style={{
                position: 'absolute',
                left: 42,
                top: y + 34,
                width: 36,
                height: 36,
                borderRadius: '50%',
                background: accent,
                border: `5px solid ${T.panel === '#ffffff' ? T.border : '#fff'}`,
                transform: `scale(${clamp01(p) * (isActive ? 1 + 0.18 * Math.sin(frame / 5) : 1)})`,
              }}
            />
            <div
              style={{
                position: 'absolute',
                left: 104,
                right: 34,
                top: y,
                height: step - 24,
                background: T.panel,
                borderRadius: 22,
                border: `3px solid ${isActive ? accent : T.border}`,
                boxShadow: isActive ? `${T.shadow}, 0 0 34px ${accent}88` : T.shadow,
                display: 'flex',
                alignItems: 'center',
                gap: 22,
                padding: '0 24px',
                transform: `translateX(${(1 - clamp01(p)) * (fromLeft ? -200 : 200)}px) scale(${
                  isActive ? 1.03 + Math.sin(frame / 12) * 0.008 : 1
                })`,
                opacity: clamp01(p * 2) * (isActive ? 1 : 0.85),
              }}
            >
              <div
                style={{
                  fontFamily: SANS,
                  fontWeight: 900,
                  fontSize: small ? 34 : 54,
                  color: accent,
                  minWidth: 150,
                  direction: 'ltr',
                }}
              >
                {e.year}
              </div>
              <div style={{fontFamily: SANS, fontWeight: 700, fontSize: 30, lineHeight: 1.2, color: T.panelText}}>
                {e.text}
              </div>
            </div>
          </React.Fragment>
        );
      })}
    </Camera>
  );
};

const StatScene: React.FC<{scene: Scene; index: number}> = ({scene, index}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const accent = BRIGHT[index % 4];
  const F = scene.frames;
  const stat = scene.stat as Stat;
  const t = interpolate(frame, [8, Math.max(60, 0.72 * F)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const v = stat.value * t;
  const dec = Math.min(2, (String(stat.value).split('.')[1] || '').length);
  const shown = dec ? v.toFixed(dec) : Math.round(v).toLocaleString('en-US');
  const txtLen = (stat.prefix + shown + stat.suffix).length;
  const isPct = stat.suffix.includes('%') && stat.value <= 100;
  const big = isPct ? 130 : txtLen > 11 ? 88 : txtLen > 8 ? 112 : txtLen > 5 ? 136 : 160;
  const lab = sp(frame, fps, 36, 14, 100);
  const breathe = 1 + Math.sin(frame / 9) * 0.018;
  const R = 190;
  const C = 2 * Math.PI * R;
  const ringP = (stat.value / 100) * t;
  const angle = -Math.PI / 2 + ringP * 2 * Math.PI;
  const bars = [0.35, 0.55, 0.42, 0.7, 0.6, 0.9, 0.78];
  return (
    <>
      <Backdrop scene={scene} index={index} />
      <Camera frames={F} index={index}>
        <TextBlock
          kicker={scene.kicker}
          heading={scene.heading}
          top={90}
          accent={accent}
          dark
          size={Math.min(headSize(scene.heading), 62)}
        />
        {isPct ? (
          <svg width={720} height={720} style={{position: 'absolute', top: 330, left: 0}}>
            <circle cx={360} cy={330} r={R} stroke="rgba(255,255,255,0.18)" strokeWidth={34} fill="none" />
            <circle
              cx={360}
              cy={330}
              r={R}
              stroke={accent}
              strokeWidth={34}
              strokeLinecap="round"
              fill="none"
              strokeDasharray={`${C * ringP} ${C}`}
              transform="rotate(-90 360 330)"
            />
            <circle
              cx={360 + R * Math.cos(angle)}
              cy={330 + R * Math.sin(angle)}
              r={22 + Math.sin(frame / 4) * 4}
              fill="#fff"
            />
          </svg>
        ) : (
          <div style={{position: 'absolute', top: 700, left: 60, right: 60, height: 230, display: 'flex', alignItems: 'flex-end', gap: 16}}>
            {bars.map((b, i) => {
              const grow = easeOut((frame - 6 - i * 5) / 30);
              const wob = 1 + Math.sin(frame / 14 + i * 1.3) * 0.06;
              return (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    height: 230 * b * grow * wob,
                    borderRadius: 12,
                    background: i === bars.length - 1 ? accent : 'rgba(255,255,255,0.3)',
                  }}
                />
              );
            })}
          </div>
        )}
        <div
          style={{
            position: 'absolute',
            top: isPct ? 520 : 400,
            left: 0,
            right: 0,
            textAlign: 'center',
            fontFamily: SANS,
            fontWeight: 900,
            fontSize: big,
            lineHeight: 1.1,
            color: '#fff',
            direction: 'ltr',
            textShadow: `0 8px 0 rgba(0,0,0,0.35), 0 0 40px ${accent}66`,
            transform: `scale(${breathe})`,
          }}
        >
          <span style={{color: accent}}>{stat.prefix}</span>
          {shown}
          <span style={{fontSize: big * 0.5, color: accent}}>{stat.suffix}</span>
        </div>
        <div
          style={{
            position: 'absolute',
            top: isPct ? 930 : 960,
            left: 40,
            right: 40,
            textAlign: 'center',
            fontFamily: SANS,
            fontWeight: 800,
            fontSize: 36,
            color: DARK,
            opacity: clamp01(lab),
            transform: `translateY(${(1 - clamp01(lab)) * 30}px)`,
          }}
        >
          <span style={{background: accent, padding: '8px 24px', borderRadius: 14, display: 'inline-block'}}>
            {stat.label}
          </span>
        </div>
      </Camera>
    </>
  );
};

// ---- map with camera fly-over
const PLATE = {x: 36, y: 255, w: 648, h: 735};

const GRAT = (() => {
  let d = '';
  for (let lon = -180; lon <= 180; lon += 15) {
    const x = project(lon, 0)[0];
    d += `M${x} 0L${x} ${WM}`;
  }
  for (let lat = -75; lat <= 75; lat += 15) {
    const y = project(0, lat)[1];
    d += `M0 ${y}L${WM} ${y}`;
  }
  return d;
})();

const MapScene: React.FC<{scene: Scene; accent: string}> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const T = useT();
  const F = scene.frames;
  const vw = PLATE.w;
  const vh = PLATE.h;
  const pts = scene.locations.map((l) => project(l.lon, l.lat));
  const N = pts.length;
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const c0 = [(minX + maxX) / 2, (minY + maxY) / 2];
  const zAll = clamp(0.6 * Math.min(vw / Math.max(maxX - minX, 1), vh / Math.max(maxY - minY, 1)), 0.22, 1.2);
  const zIn = 2.4;

  const dive1 = 0.2 * F;
  const legStart = dive1;
  const legEnd = 0.9 * F;
  const slice = (legEnd - legStart) / Math.max(N - 1, 1);
  const driftStart = N === 1 ? dive1 : legEnd;

  let cx = c0[0];
  let cy = c0[1];
  let z = zAll * (1 + frame * 0.0012);
  if (frame < dive1) {
    const t = ease(frame / dive1);
    cx = lerp(c0[0], pts[0][0], t);
    cy = lerp(c0[1], pts[0][1], t);
    z = Math.exp(lerp(Math.log(zAll), Math.log(zIn), t));
  } else if (frame >= driftStart) {
    const last = pts[N - 1];
    const d = clamp01((frame - driftStart) / Math.max(F - driftStart, 1));
    cx = last[0] + (Math.sin(frame / 30) * 6) / zIn;
    cy = last[1] + (Math.cos(frame / 26) * 6) / zIn;
    z = zIn * (1 + 0.22 * d);
  } else {
    const f = frame - legStart;
    const k = Math.min(N - 2, Math.floor(f / slice));
    const u = clamp01((f - k * slice) / slice);
    const t = ease((u - 0.12) / 0.88);
    const a = pts[k];
    const b = pts[k + 1];
    cx = lerp(a[0], b[0], t);
    cy = lerp(a[1], b[1], t);
    const zFit = clamp(
      0.55 * Math.min(vw / Math.max(Math.abs(b[0] - a[0]), 1), vh / Math.max(Math.abs(b[1] - a[1]), 1)),
      0.3,
      zIn,
    );
    const s = Math.sin(Math.PI * t);
    z = Math.exp(lerp(Math.log(zIn), Math.log(zFit), s));
  }

  const vbx = cx - vw / (2 * z);
  const vby = cy - vh / (2 * z);
  const toScreen = (p: number[]) => [(p[0] - cx) * z + vw / 2, (p[1] - cy) * z + vh / 2];

  const legs = [];
  for (let k = 0; k < N - 1; k++) {
    const a = pts[k];
    const b = pts[k + 1];
    const mx = (a[0] + b[0]) / 2;
    const my = (a[1] + b[1]) / 2;
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const dist = Math.max(Math.hypot(dx, dy), 1);
    let nx = -dy / dist;
    let ny = dx / dist;
    if (ny > 0) {
      nx = -nx;
      ny = -ny;
    }
    const c = [mx + nx * dist * 0.2, my + ny * dist * 0.2];
    const prog = ease((frame - (legStart + k * slice + 0.12 * slice)) / (0.88 * slice));
    legs.push({a, b, c, prog});
  }

  const plate = sp(frame, fps, 0, 16, 100);
  const dash = frame * 0.8;
  const routeCol = T.kind === 'halftone' || T.kind === 'stripes' ? '#e11d48' : accent;

  return (
    <>
      <TextBlock kicker={scene.kicker} heading={scene.heading} top={70} accent={accent} size={scene.heading.length > 22 ? 44 : 56} />
      <div
        style={{
          position: 'absolute',
          left: PLATE.x,
          top: PLATE.y,
          width: vw,
          height: vh,
          border: `5px solid ${T.panel === '#ffffff' ? T.border : accent}`,
          boxShadow: T.panel === '#ffffff' ? T.shadow : `0 24px 50px rgba(0,0,0,0.5), 0 0 40px ${accent}44`,
          borderRadius: 30,
          boxSizing: 'content-box',
          marginLeft: -5,
          marginTop: -5,
          overflow: 'hidden',
          background: T.ocean,
          transform: `translateY(${(1 - clamp01(plate)) * 70}px)`,
          opacity: clamp01(plate * 2),
        }}
      >
        <svg width={vw} height={vh} viewBox={`${vbx} ${vby} ${vw / z} ${vh / z}`} style={{position: 'absolute', left: 0, top: 0}}>
          <rect x={-WM} y={-WM} width={WM * 3} height={WM * 3} fill={T.ocean} />
          <path d={GRAT} stroke={T.grat} strokeWidth={1.2 / z} fill="none" />
          <path d={LAND} fill={T.land} stroke={T.landLine} strokeWidth={1.8 / z} strokeLinejoin="round" />
          <path
            d={BORDERS}
            fill="none"
            stroke={T.landLine}
            strokeOpacity={0.4}
            strokeWidth={1 / z}
            strokeDasharray={`${6 / z} ${4 / z}`}
            strokeDashoffset={-dash / z}
          />
          {legs.map((l, i) => (
            <path
              key={'g' + i}
              d={`M${l.a[0]} ${l.a[1]}Q${l.c[0]} ${l.c[1]} ${l.b[0]} ${l.b[1]}`}
              fill="none"
              stroke={routeCol}
              strokeOpacity={0.35}
              strokeWidth={16 / z}
              strokeLinecap="round"
              pathLength={1}
              strokeDasharray={1}
              strokeDashoffset={1 - l.prog}
            />
          ))}
          {legs.map((l, i) => (
            <path
              key={i}
              d={`M${l.a[0]} ${l.a[1]}Q${l.c[0]} ${l.c[1]} ${l.b[0]} ${l.b[1]}`}
              fill="none"
              stroke={routeCol}
              strokeWidth={7 / z}
              strokeLinecap="round"
              pathLength={1}
              strokeDasharray={1}
              strokeDashoffset={1 - l.prog}
            />
          ))}
          {legs.map((l, i) => {
            if (l.prog <= 0 || l.prog >= 1) return null;
            const t = l.prog;
            const px = (1 - t) * (1 - t) * l.a[0] + 2 * (1 - t) * t * l.c[0] + t * t * l.b[0];
            const py = (1 - t) * (1 - t) * l.a[1] + 2 * (1 - t) * t * l.c[1] + t * t * l.b[1];
            return <circle key={'d' + i} cx={px} cy={py} r={13 / z} fill="#fff" stroke={routeCol} strokeWidth={6 / z} />;
          })}
        </svg>
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
            boxShadow: `inset 0 0 90px ${T.mapShade}`,
          }}
        />
        {scene.locations.map((loc, i) => {
          const arrive = i === 0 ? dive1 - 8 : legStart + i * slice - 8;
          const pp = sp(frame, fps, arrive, 10, 140);
          if (pp <= 0.01) return null;
          const labelOut = i < N - 1 ? clamp01(1 - (frame - (legStart + i * slice + 0.12 * slice)) / 10) : 1;
          const [sx, sy] = toScreen(pts[i]);
          const w = clamp(40 + loc.name.length * 19, 150, 330);
          const lx = clamp(sx - w / 2, 12, vw - w - 12);
          const below = sy < 120;
          const ly = below ? sy + 26 : sy - (loc.note ? 98 : 74);
          const pulse = (frame % 40) / 40;
          return (
            <React.Fragment key={i}>
              <div
                style={{
                  position: 'absolute',
                  left: sx - 12,
                  top: sy - 12,
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  background: routeCol,
                  border: '4px solid #fff',
                  boxShadow: '0 3px 10px rgba(0,0,0,0.5)',
                  transform: `scale(${pp})`,
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  left: sx - 12,
                  top: sy - 12,
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  border: `3px solid ${routeCol}`,
                  opacity: (1 - pulse) * 0.8,
                  transform: `scale(${1 + pulse * 2.6})`,
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  left: lx,
                  top: ly,
                  width: w,
                  background: 'rgba(8,13,42,0.93)',
                  border: `3px solid ${routeCol}`,
                  borderRadius: 14,
                  padding: '6px 12px',
                  textAlign: 'center',
                  transform: `scale(${clamp01(pp) * (0.85 + 0.15 * labelOut)})`,
                  opacity: clamp01(pp * 2) * labelOut,
                }}
              >
                <div style={{fontFamily: SANS, fontWeight: 900, fontSize: 28, color: '#fff', lineHeight: 1.15}}>
                  {loc.name}
                </div>
                {loc.note ? (
                  <div style={{fontFamily: SANS, fontWeight: 700, fontSize: 20, color: BRIGHT[0]}}>{loc.note}</div>
                ) : null}
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </>
  );
};

// ------------------------------------------------------------- scene + root
const SceneView: React.FC<{scene: Scene; index: number; from: number; total: number}> = ({
  scene,
  index,
  from,
  total,
}) => {
  const frame = useCurrentFrame();
  const T = useT();
  const lead = index > 0 ? OV : 0;
  const accent = T.accents[index % T.accents.length];
  const prog = index > 0 ? clamp01(frame / OV) : 1;
  const slide = index > 0 ? 1 - easeOut(frame / OV) : 0;

  const style: React.CSSProperties = {};
  let band: React.ReactNode = null;
  if (index > 0 && prog < 1) {
    if (T.trans === 'iris') {
      style.clipPath = `circle(${easeOut(prog) * 80}% at 50% 55%)`;
    } else if (T.trans === 'diag') {
      const x = easeOut(prog) * 130;
      style.clipPath = `polygon(0 0, ${x}% 0, ${x - 28}% 100%, 0 100%)`;
    } else {
      const dirs = ['x', 'y', '-x'];
      const dir = dirs[index % 3];
      if (dir === 'x') style.transform = `translateX(${slide * 104}%)`;
      else if (dir === '-x') style.transform = `translateX(${-slide * 104}%)`;
      else style.transform = `translateY(${slide * 104}%)`;
      const bs: React.CSSProperties =
        dir === 'x'
          ? {left: -40, top: 0, width: 40, height: '100%'}
          : dir === '-x'
          ? {right: -40, top: 0, width: 40, height: '100%'}
          : {left: 0, top: -40, width: '100%', height: 40};
      band = <div style={{position: 'absolute', background: accent, ...bs}} />;
    }
  }

  let body: React.ReactNode;
  switch (scene.layout) {
    case 'map':
      body = <MapScene scene={scene} accent={accent} />;
      break;
    case 'timeline':
      body = <Timeline scene={scene} index={index} accent={accent} />;
      break;
    case 'stat':
      body = <StatScene scene={scene} index={index} />;
      break;
    case 'cutout':
      body = <CutoutScene scene={scene} index={index} accent={accent} />;
      break;
    case 'bigtext':
      body = <BigText scene={scene} index={index} accent={accent} />;
      break;
    case 'versus':
      body = <Versus scene={scene} index={index} accent={accent} />;
      break;
    case 'list':
      body = <ListScene scene={scene} index={index} accent={accent} />;
      break;
    default:
      body = <TitleScene scene={scene} index={index} />;
  }
  const gp = clamp01((from + frame - lead) / Math.max(total, 1));
  const capAccent = BRIGHT[index % 4];

  return (
    <AbsoluteFill style={style}>
      {band}
      {index > 0 ? <Audio src={staticFile('whoosh.wav')} volume={0.45} /> : null}
      <Bg index={index} />
      <Sequence from={lead}>
        {scene.audio ? <Audio src={staticFile(scene.audio)} /> : null}
        {body}
        <Captions scene={scene} accent={capAccent} />
        <div style={{position: 'absolute', left: 0, top: 0, height: 8, width: `${gp * 100}%`, background: accent}} />
      </Sequence>
    </AbsoluteFill>
  );
};

export const Documentary: React.FC<Props> = ({rtl, scenes, theme}) => {
  let start = 0;
  const total = scenes.reduce((a, s) => a + s.frames, 0);
  const T = THEMES[(theme || 0) % THEMES.length];
  return (
    <ThemeCtx.Provider value={T}>
      <AbsoluteFill style={{background: '#000', fontFamily: SANS, direction: rtl ? 'rtl' : 'ltr'}}>
        {scenes.map((s, i) => {
          const lead = i > 0 ? OV : 0;
          const from = start - lead;
          const gstart = start;
          start += s.frames;
          return (
            <Sequence key={i} from={from} durationInFrames={s.frames + lead}>
              <SceneView scene={s} index={i} from={gstart} total={total} />
            </Sequence>
          );
        })}
      </AbsoluteFill>
    </ThemeCtx.Provider>
  );
};
"""


def scaffold():
    (VIDEO / "src").mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (VIDEO / "package.json").write_text(PACKAGE_JSON, encoding="utf-8")
    (VIDEO / "src" / "index.ts").write_text(INDEX_TS, encoding="utf-8")
    (VIDEO / "src" / "Root.tsx").write_text(ROOT_TSX, encoding="utf-8")
    (VIDEO / "src" / "Land.ts").write_text(LAND_TS, encoding="utf-8")
    (VIDEO / "src" / "Video.tsx").write_text(VIDEO_TSX, encoding="utf-8")


# ---------------------------------------------------------------- Gemini script
BRIEF_PROMPT = """Research the topic: {TOPIC}
Write plain text notes for a video writer. Use real, verifiable facts only. Include:
1. THE ANGLE: the single most surprising or counter-intuitive angle that most videos on this topic miss.
2. TEN SPECIFIC FACTS with names, dates, numbers, places (mark any you are unsure about with '?').
3. THREE strong opening lines (each under 12 words, no greetings).
4. THE TWIST or deeper meaning to end on.
5. Key places with approximate latitude/longitude, if places matter.
Keep it under 450 words."""

PROMPT = """You are a top short-form video writer (think the best explainer YouTubers: sharp, surprising, never boring). Write a vertical social video about: {TOPIC}
LANGUAGE of ALL narration and on-screen text: {LANGUAGE}. Natural, spoken, conversational, not bookish. Place names, wiki and image_prompt stay in English.
FORMAT FOR THIS VIDEO: {FORMAT}
TONE: {TONE}
RESEARCH NOTES (rely on these facts, plus things you are 100% sure of):
{BRIEF}

Return ONLY JSON in this shape:
{
  "title": "max 4 words",
  "scenes": [
    {
      "narration": "10-22 words, spoken aloud, ONE idea",
      "layout": "title | cutout | map | timeline | stat | bigtext | versus | list",
      "kicker": "2-3 word label",
      "heading": "max 7 words, wrap ONE key word in *asterisks*",
      "sub": "max 9 words (title, cutout, bigtext) or the verdict line (versus)",
      "emoji": "one emoji that matches the scene",
      "wiki": "exact English Wikipedia article title of the REAL subject, or empty",
      "image_prompt": "English, what a simple flat illustration shows",
      "tags": ["max 3 words"],
      "locations": [{"name": "place", "note": "max 3 words", "lat": 9.1, "lon": 40.5}],
      "events": [{"year": "1450", "text": "max 8 words"}],
      "stat": {"value": 71, "prefix": "", "suffix": "%", "label": "max 8 words"},
      "items": [{"rank": "3", "label": "max 4 words", "emoji": "x", "note": "max 4 words"}],
      "vs": {"a": {"name": "Myth", "emoji": "x", "line": "max 8 words"}, "b": {"name": "Fact", "emoji": "x", "line": "max 8 words"}}
    }
  ]
}

Rules:
- 10 to 13 scenes. Total narration about 150-200 words (60-75 seconds).
- Scene 1 is the HOOK: a bold claim, paradox or question in the first words. Layout title or bigtext. NEVER start with a greeting, "welcome", "imagine", "did you know", "in this video", "have you ever".
- Every 2-3 scenes open a curiosity gap ("but the strange part is...") and pay it off soon after.
- Concrete beats adjectives: names, numbers, dates, comparisons to everyday things.
- Vary sentence rhythm: mix very short lines with longer ones. No filler, no repeated phrases.
- Last scene: a one-line takeaway that reframes everything, then a question that makes people comment. Layout bigtext or cutout.
- Follow the FORMAT above for the story structure and layout mix. Never use the same layout 3 times in a row.
- map: at most 2, real places, 2-4 locations in travel order, accurate decimal coordinates (south latitude and west longitude are negative).
- timeline: at most 1, 3-4 dated events in order.
- stat: at most 2, ONE certain number (numeric value; suffix like "%" or " million").
- versus: both sides filled. For myth vs fact: a.name "Myth", b.name "Fact".
- list: 3-5 items. For a ranking use ranks like "5","4","3" in reveal order; for steps use "1","2","3".
- bigtext: one punchy emotional line, no picture needed.
- cutout and title: give wiki (only for real famous things, places, people, objects; else empty), an image_prompt, and for cutout 2-3 tags.
- Fill only the fields the chosen layout needs. If unsure about a fact, use another layout."""


def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def _s(v, n):
    return str(v or "").strip()[:n]


def _clean_scene(s):
    layout = s.get("layout") if s.get("layout") in LAYOUTS else "cutout"
    heading = _s(s.get("heading"), 60)
    out = {
        "narration": str(s["narration"]),
        "layout": layout,
        "kicker": _s(s.get("kicker"), 28),
        "heading": heading,
        "sub": _s(s.get("sub"), 70),
        "emoji": _s(s.get("emoji"), 8) or "📜",
        "wiki": _s(s.get("wiki"), 90),
        "image_prompt": _s(s.get("image_prompt"), 300),
        "image": "",
        "imgKind": "",
        "tags": [str(t)[:22] for t in (s.get("tags") or []) if t][:3],
        "locations": [],
        "events": [],
        "stat": None,
        "items": [],
        "vs": None,
    }

    for loc in s.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        lat, lon = _num(loc.get("lat")), _num(loc.get("lon"))
        if lat is None or lon is None or not (-85 <= lat <= 85 and -180 <= lon <= 180):
            continue
        out["locations"].append({"name": _s(loc.get("name"), 26), "note": _s(loc.get("note"), 26), "lat": lat, "lon": lon})
    out["locations"] = out["locations"][:4]

    for e in s.get("events") or []:
        if isinstance(e, dict) and e.get("year") and e.get("text"):
            out["events"].append({"year": _s(e["year"], 10), "text": _s(e["text"], 60)})
    out["events"] = out["events"][:4]

    st = s.get("stat")
    if isinstance(st, dict) and _num(st.get("value")) is not None:
        v = _num(st["value"])
        suffix = _s(st.get("suffix"), 12)
        if not suffix.strip() and v >= 1e9:
            v, suffix = v / 1e9, " billion"
        elif not suffix.strip() and v >= 1e6:
            v, suffix = v / 1e6, " million"
        v = round(v, 2) if v != int(v) else int(v)
        out["stat"] = {"value": v, "prefix": _s(st.get("prefix"), 4), "suffix": suffix, "label": _s(st.get("label"), 60)}

    for it in s.get("items") or []:
        if isinstance(it, dict) and it.get("label"):
            out["items"].append({"rank": _s(it.get("rank"), 4), "label": _s(it["label"], 34), "emoji": _s(it.get("emoji"), 8) or "⭐", "note": _s(it.get("note"), 34)})
    out["items"] = out["items"][:5]

    vs = s.get("vs")
    if isinstance(vs, dict) and isinstance(vs.get("a"), dict) and isinstance(vs.get("b"), dict):
        sides = []
        for k in ("a", "b"):
            d = vs[k]
            sides.append({"name": _s(d.get("name"), 22), "emoji": _s(d.get("emoji"), 8) or "❓", "line": _s(d.get("line"), 60)})
        if all(x["name"] and x["line"] for x in sides):
            out["vs"] = {"a": sides[0], "b": sides[1]}

    # fall back when a layout has no data
    if layout == "map" and not out["locations"]:
        out["layout"] = "cutout"
    if layout == "timeline" and len(out["events"]) < 2:
        out["layout"] = "cutout"
    if layout == "stat" and not out["stat"]:
        out["layout"] = "cutout"
    if layout == "versus" and not out["vs"]:
        out["layout"] = "bigtext"
    if layout == "list" and len(out["items"]) < 2:
        out["layout"] = "bigtext"
    if out["layout"] in PIC_LAYOUTS and not out["image_prompt"]:
        out["image_prompt"] = heading.replace("*", "")
    return out


def _gemini(prompt, models, key, as_json, grounded=False):
    import requests

    last = None
    for model in [m for m in models if m]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        if as_json:
            body["generationConfig"] = {"responseMimeType": "application/json", "temperature": 1.0}
        if grounded:
            body["tools"] = [{"google_search": {}}]
        try:
            r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
        except Exception as e:
            last = f"{model}: {e}"
            continue
        if r.status_code != 200:
            last = f"{model}: {r.status_code} {r.text[:160]}"
            print("  gemini skip ->", last)
            continue
        try:
            text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
            if text.strip():
                return model, text
            last = f"{model}: empty"
        except Exception as e:
            last = f"{model}: bad response ({e})"
    return None, last


def write_script(topic, fmt, tone):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY missing. Secret me save karein.")

    # step 1: research brief (with Google Search when allowed)
    brief = ""
    for grounded in (True, False):
        m, txt = _gemini(BRIEF_PROMPT.replace("{TOPIC}", topic), [os.environ.get("GEMINI_MODEL"), "gemini-2.5-flash", "gemini-flash-latest"], key, False, grounded)
        if m:
            brief = txt.strip()[:5000]
            print(f"Research brief ready with {m} (search grounding: {grounded})")
            break
    if not brief:
        print("Research brief skipped:", txt)

    # step 2: the script
    prompt = (
        PROMPT.replace("{TOPIC}", topic)
        .replace("{LANGUAGE}", LANGUAGE)
        .replace("{FORMAT}", FORMATS[fmt])
        .replace("{TONE}", tone)
        .replace("{BRIEF}", brief or "(none - use only facts you are certain about)")
    )
    models = [os.environ.get("GEMINI_MODEL"), "gemini-2.5-pro", "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
    last = None
    for model in [m for m in models if m]:
        m, text = _gemini(prompt, [model], key, True)
        if not m:
            last = text
            continue
        try:
            text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
            data = json.loads(text)
            scenes = [_clean_scene(s) for s in data["scenes"] if s.get("narration")]
            if len(scenes) < 4:
                raise ValueError("too few scenes")
            if scenes[0]["layout"] not in ("title", "bigtext"):
                scenes[0]["layout"] = "title" if scenes[0]["image_prompt"] else "bigtext"
            title = _s(data.get("title"), 40) or topic
            print(f"Script ready with {m}: {[s['layout'] for s in scenes]}")
            return {"title": title, "scenes": scenes}
        except Exception as e:
            last = f"{model}: bad script ({e})"
            print("  ", last)
    sys.exit(f"Gemini fail: {last}")


# ---------------------------------------------------------------- pictures
def _save_image(content, ctype, stem):
    ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
    name = stem + ext
    (PUBLIC / name).write_bytes(content)
    return name


def wiki_image(title, stem):
    """Real photo from Wikipedia (free, no key). Returns file name or ''."""
    import requests

    def summary(t):
        u = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(t.replace(" ", "_"), safe="")
        r = requests.get(u, headers=UA, timeout=30)
        return r.json() if r.status_code == 200 else None

    def grab(d):
        if not d or d.get("type") == "disambiguation":
            return ""
        src = None
        th, org = d.get("thumbnail"), d.get("originalimage")
        if th and th.get("source"):
            m = re.search(r"/(\d+)px-", th["source"])
            src = re.sub(r"/\d+px-", "/1100px-", th["source"]) if m else th["source"]
        elif org and org.get("source"):
            src = org["source"]
        if not src or src.lower().endswith(".svg") or ".svg/" in src.lower():
            return ""
        r = requests.get(src, headers=UA, timeout=60)
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 15000:
            return _save_image(r.content, ctype, stem)
        return ""

    try:
        name = grab(summary(title))
        if name:
            return name
        r = requests.get("https://en.wikipedia.org/w/rest.php/v1/search/title", params={"q": title, "limit": 3}, headers=UA, timeout=30)
        if r.status_code == 200:
            for p in r.json().get("pages", []):
                name = grab(summary(p["key"]))
                if name:
                    return name
    except Exception as e:
        print("  wiki error:", e)
    return ""


def fetch_image(prompt, stem, seed):
    """AI picture from Pollinations (free). Returns file name or ''."""
    import requests

    q = urllib.parse.quote(f"{prompt}, {IMAGE_STYLE}")
    url = f"https://image.pollinations.ai/prompt/{q}?width=768&height=768&nologo=true&model=flux&seed={seed}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=120)
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 8000:
                return _save_image(r.content, ctype, stem)
            print(f"  image try {attempt + 1}: HTTP {r.status_code} {ctype} {len(r.content)}B")
        except Exception as e:
            print(f"  image try {attempt + 1}: {e}")
        time.sleep(4 * (attempt + 1))
        url = url.rsplit("seed=", 1)[0] + f"seed={seed + attempt + 1}"
    return ""


def build_images(scenes):
    if os.environ.get("NO_IMAGES"):
        return
    for i, s in enumerate(scenes):
        if s["layout"] not in PIC_LAYOUTS:
            continue
        name, kind = "", ""
        if s["wiki"]:
            name = wiki_image(s["wiki"], f"img{i}")
            kind = "photo" if name else ""
        if not name and s["image_prompt"]:
            name = fetch_image(s["image_prompt"], f"img{i}", seed=1000 + i * 7)
            kind = "ai" if name else ""
        s["image"], s["imgKind"] = name, kind
        print(f"Picture {i + 1}: {kind or 'none -> emoji'}")


def make_whoosh(path):
    """Tiny transition sound made with the standard library."""
    import struct
    import wave

    sr, n = 44100, int(44100 * 0.55)
    rnd = random.Random(7)
    lp, out = 0.0, []
    for i in range(n):
        t = i / n
        env = math.sin(math.pi * t) ** 1.5
        a = 0.02 + 0.5 * math.sin(math.pi * t)
        lp += a * ((rnd.random() * 2 - 1) - lp)
        out.append(int(max(-1.0, min(1.0, lp * env * 1.6)) * 30000))
    w = wave.open(str(path), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(struct.pack("<%dh" % n, *out))
    w.close()


# ---------------------------------------------------------------- Edge TTS
async def tts(text, voice, path):
    import edge_tts

    words = []
    try:
        comm = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    except TypeError:  # older edge-tts
        comm = edge_tts.Communicate(text, voice)
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({"text": chunk["text"], "start": chunk["offset"] / 1e7})
    return words


def build_scenes(data):
    from mutagen.mp3 import MP3

    scenes = data["scenes"]
    build_images(scenes)
    make_whoosh(PUBLIC / "whoosh.wav")

    final = []
    for i, s in enumerate(scenes):
        name = f"scene{i}.mp3"
        path = PUBLIC / name
        words = asyncio.run(tts(s["narration"], VOICE, str(path)))
        length = MP3(str(path)).info.length
        if not words:  # fallback: spread words evenly
            toks = s["narration"].split()
            words = [{"text": t, "start": length * k / max(len(toks), 1)} for k, t in enumerate(toks)]
        scene = {k: v for k, v in s.items() if k not in ("narration", "image_prompt", "wiki")}
        scene.update(
            {
                "audio": name,
                "words": [{"text": w["text"], "frame": int(round(w["start"] * FPS))} for w in words],
                "frames": max(75, int(math.ceil((length + 0.5) * FPS))),
            }
        )
        final.append(scene)
        print(f"Voice {i + 1}/{len(scenes)} done ({length:.1f}s)")
    return final


# ---------------------------------------------------------------- main
def run(cmd):
    subprocess.run(cmd, cwd=VIDEO, check=True, shell=(os.name == "nt"))


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python make_video.py "topic"')
    topic = " ".join(sys.argv[1:]).strip()
    print("== KINETIC VERSION v4 ==")
    rnd = random.Random()
    fmt = os.environ.get("FORMAT") if os.environ.get("FORMAT") in FORMATS else rnd.choice(list(FORMATS))
    tone = rnd.choice(TONES)
    try:
        theme = int(os.environ.get("STYLE"))
    except Exception:
        theme = rnd.randrange(len(THEME_NAMES))
    theme %= len(THEME_NAMES)
    print(f"Format: {fmt} | Look: {THEME_NAMES[theme]} | Tone: {tone}")

    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    scaffold()
    data = write_script(topic, fmt, tone)
    scenes = build_scenes(data)
    rtl = any(x in LANGUAGE.lower() for x in RTL_LANGS)
    (VIDEO / "props.json").write_text(
        json.dumps({"title": data["title"], "rtl": rtl, "theme": theme, "scenes": scenes}, ensure_ascii=False),
        encoding="utf-8",
    )

    run(["npm", "install", "--no-audit", "--no-fund"])
    out = ROOT / "out.mp4"
    run(
        [
            "npx", "remotion", "render", "src/index.ts", "Documentary",
            str(out), "--props=props.json", "--concurrency=2", "--timeout=120000",
        ]
    )
    print(f"Done: {out}")


if __name__ == "__main__":
    main()

"""
make_video.py - topic do, documentary-style vertical video (out.mp4) pao.

Usage:  python make_video.py "history of coffee"

Pipeline:
  Gemini      -> story script (scenes, layouts, map coordinates, image prompts)
  Pollinations-> paper-cutout style pictures (free, no key; emoji fallback if it fails)
  Edge TTS    -> voice + word timings
  Remotion    -> paper look, real world map with camera fly-over, captions -> out.mp4

Env: GEMINI_API_KEY (required), VOICE, LANGUAGE, GEMINI_MODEL (optional), NO_IMAGES=1 (skip pictures).
"""
import asyncio
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
LAYOUTS = ["title", "cutout", "map", "timeline", "stat"]
RTL_LANGS = ("urdu", "arabic", "persian", "farsi", "hebrew")
IMAGE_STYLE = (
    "paper cut-out collage illustration, flat muted vintage colors, subtle paper texture, "
    "single subject centered, plain white background, no text, no letters"
)

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
      defaultProps={{title: 'Demo', rtl: false, scenes: []} as Props}
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
export type Scene = {
  layout: 'title' | 'cutout' | 'map' | 'timeline' | 'stat';
  kicker: string;
  heading: string;
  sub: string;
  emoji: string;
  image: string;
  tags: string[];
  locations: Loc[];
  events: Ev[];
  stat: Stat | null;
  audio: string;
  words: Word[];
  frames: number;
};
export type Props = {title: string; rtl: boolean; scenes: Scene[]};

// ------------------------------------------------------------- style
const INK = '#221b16';
const PAPER = '#efe5d0';
const RED = '#c4412b';
const MUSTARD = '#e2a62f';
const SCRAPS = ['#ead08a', '#bccaa4', '#eab9a4', '#abc8d3'];
const OCEAN = '#cddbd6';
const LANDC = '#ead7a6';

const SERIF = "'Noto Serif','Noto Naskh Arabic','Noto Nastaliq Urdu','DejaVu Serif',Georgia,serif";
const SANS = "'Noto Sans','Noto Sans Arabic','Noto Naskh Arabic','DejaVu Sans',Arial,sans-serif";
const EMOJI = "'Noto Color Emoji','Apple Color Emoji','Segoe UI Emoji',sans-serif";

const OV = 14; // frames a new sheet of paper takes to slide in

const clamp01 = (n: number) => Math.max(0, Math.min(1, n));
const clamp = (n: number, a: number, b: number) => Math.max(a, Math.min(b, n));
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const ease = (t: number) => Easing.inOut(Easing.cubic)(clamp01(t));

const sp = (frame: number, fps: number, delay = 0, damping = 14, stiffness = 100) =>
  spring({frame: frame - delay, fps, config: {damping, stiffness}});

const Emoji: React.FC<{c: string; s: number}> = ({c, s}) => (
  <span style={{fontFamily: EMOJI, fontSize: s, lineHeight: 1}}>{c}</span>
);

// ------------------------------------------------------------- shared pieces
const Defs: React.FC = () => (
  <svg width={0} height={0} style={{position: 'absolute'}}>
    <defs>
      <filter id="rough" x="-10%" y="-10%" width="120%" height="120%">
        <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves={3} seed={4} result="n" />
        <feDisplacementMap in="SourceGraphic" in2="n" scale={14} xChannelSelector="R" yChannelSelector="G" />
      </filter>
      <filter id="grain">
        <feTurbulence type="fractalNoise" baseFrequency="0.75" numOctaves={2} seed={8} />
        <feColorMatrix values="0 0 0 0 0.36  0 0 0 0 0.28  0 0 0 0 0.18  0 0 0 0.2 0" />
      </filter>
    </defs>
  </svg>
);

const Paper: React.FC = () => (
  <AbsoluteFill style={{background: PAPER}}>
    <svg width="100%" height="100%" style={{position: 'absolute', left: 0, top: 0}}>
      <rect width="100%" height="100%" filter="url(#grain)" />
    </svg>
    <AbsoluteFill
      style={{
        background:
          'radial-gradient(ellipse at 50% 38%, rgba(255,255,255,0.35), rgba(120,88,48,0.30) 100%)',
      }}
    />
  </AbsoluteFill>
);

// *word* in a heading gets a marker highlight
const Rich: React.FC<{text: string; p: number}> = ({text, p}) => (
  <>
    {text.split('*').map((s, i) =>
      i % 2 ? (
        <span
          key={i}
          style={{
            background: `linear-gradient(transparent 58%, ${MUSTARD}cc 58%) no-repeat`,
            backgroundSize: `${clamp01(p) * 100}% 100%`,
            padding: '0 6px',
          }}
        >
          {s}
        </span>
      ) : (
        <React.Fragment key={i}>{s}</React.Fragment>
      )
    )}
  </>
);

const TextBlock: React.FC<{
  kicker: string;
  heading: string;
  sub?: string;
  top: number;
  size?: number;
}> = ({kicker, heading, sub, top, size}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const plain = heading.replace(/\*/g, '');
  const fs = size || (plain.length > 38 ? 56 : plain.length > 24 ? 68 : 82);
  const p = sp(frame, fps, 2, 16, 90);
  const hl = interpolate(frame, [18, 38], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const sp2 = sp(frame, fps, 24, 18, 80);
  return (
    <div
      style={{
        position: 'absolute',
        top,
        left: 48,
        right: 48,
        textAlign: 'center',
        transform: `translateY(${(1 - p) * 40}px)`,
        opacity: clamp01(p * 1.4),
      }}
    >
      {kicker ? (
        <div
          style={{
            fontFamily: SANS,
            fontSize: 25,
            letterSpacing: 6,
            textTransform: 'uppercase',
            color: RED,
            fontWeight: 700,
            marginBottom: 14,
          }}
        >
          {kicker}
        </div>
      ) : null}
      <div style={{fontFamily: SERIF, fontWeight: 900, fontSize: fs, lineHeight: 1.1, color: INK}}>
        <Rich text={heading} p={hl} />
      </div>
      {sub ? (
        <div
          style={{
            fontFamily: SANS,
            fontSize: 33,
            lineHeight: 1.3,
            color: '#5a4c40',
            marginTop: 18,
            opacity: clamp01(sp2),
            transform: `translateY(${(1 - clamp01(sp2)) * 20}px)`,
          }}
        >
          {sub}
        </div>
      ) : null}
    </div>
  );
};

const Cutout: React.FC<{
  src: string;
  emoji: string;
  size: number;
  rot: number;
  color: string;
  delay?: number;
}> = ({src, emoji, size, rot, color, delay = 0}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = sp(frame, fps, delay, 11, 80);
  const bob = Math.sin(frame / 24) * 6;
  return (
    <div
      style={{
        position: 'relative',
        width: size,
        height: size,
        opacity: clamp01(p * 2),
        transform: `translateY(${bob + (1 - p) * 80}px) rotate(${rot - (1 - p) * 10}deg) scale(${0.7 + 0.3 * p})`,
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: -18,
          top: -18,
          right: -18,
          bottom: -18,
          background: color,
          filter: 'url(#rough) drop-shadow(0 14px 14px rgba(40,25,10,0.35))',
        }}
      />
      {src ? (
        <Img
          src={staticFile(src)}
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            mixBlendMode: 'multiply',
          }}
        />
      ) : (
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: '100%',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Emoji c={emoji} s={size * 0.55} />
        </div>
      )}
      <div
        style={{
          position: 'absolute',
          top: -38,
          left: size / 2 - 66,
          width: 132,
          height: 40,
          background: 'rgba(255,255,255,0.55)',
          transform: 'rotate(-4deg)',
          boxShadow: '0 2px 6px rgba(0,0,0,0.18)',
        }}
      />
    </div>
  );
};

const Tag: React.FC<{text: string; x: number; y: number; rot: number; delay: number}> = ({
  text,
  x,
  y,
  rot,
  delay,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = sp(frame, fps, delay, 9, 120);
  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        top: y,
        fontFamily: SANS,
        fontWeight: 800,
        fontSize: 29,
        color: INK,
        background: '#fffaf0',
        padding: '10px 22px',
        borderRadius: 8,
        border: `3px solid ${INK}`,
        boxShadow: `5px 5px 0 ${INK}`,
        transform: `rotate(${rot}deg) scale(${p})`,
        opacity: clamp01(p * 2),
        whiteSpace: 'nowrap',
      }}
    >
      {text}
    </div>
  );
};

const Captions: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const CHUNK = 4;
  let active = 0;
  scene.words.forEach((w, i) => {
    if (frame >= w.frame) active = i;
  });
  const start = Math.floor(active / CHUNK) * CHUNK;
  const chunk = scene.words.slice(start, start + CHUNK);
  if (!chunk.length) return null;
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 64,
        left: 28,
        right: 28,
        display: 'flex',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          fontFamily: SANS,
          background: 'rgba(255,251,240,0.96)',
          border: `3px solid ${INK}`,
          boxShadow: `6px 6px 0 ${INK}`,
          borderRadius: 10,
          padding: '14px 28px',
          textAlign: 'center',
          fontSize: 44,
          fontWeight: 800,
          lineHeight: 1.25,
          color: INK,
        }}
      >
        {chunk.map((w, i) => (
          <span
            key={start + i}
            style={{
              display: 'inline-block',
              color: start + i === active ? RED : INK,
              marginLeft: 7,
              marginRight: 7,
            }}
          >
            {w.text}
          </span>
        ))}
      </div>
    </div>
  );
};

// slow camera push for flat layouts
const Camera: React.FC<{frames: number; children: React.ReactNode}> = ({frames, children}) => {
  const frame = useCurrentFrame();
  const t = clamp01(frame / Math.max(frames, 1));
  return (
    <AbsoluteFill style={{transform: `scale(${1 + 0.05 * t}) translateY(${-t * 14}px)`}}>
      {children}
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------- layouts
const TitleScene: React.FC<{scene: Scene}> = ({scene}) => (
  <Camera frames={scene.frames}>
    <TextBlock kicker={scene.kicker} heading={scene.heading} sub={scene.sub} top={130} />
    <div style={{position: 'absolute', top: 690, left: 720 / 2 - 190}}>
      <Cutout src={scene.image} emoji={scene.emoji} size={380} rot={-3} color={SCRAPS[0]} delay={10} />
    </div>
  </Camera>
);

const CutoutScene: React.FC<{scene: Scene; index: number}> = ({scene, index}) => {
  const tags = scene.tags.slice(0, 3);
  const spots = [
    {x: 10, y: 215, r: -6},
    {x: 470, y: 590, r: 5},
    {x: 24, y: 615, r: -3},
  ];
  return (
    <Camera frames={scene.frames}>
      <div style={{position: 'absolute', top: 180, left: 720 / 2 - 240}}>
        <Cutout
          src={scene.image}
          emoji={scene.emoji}
          size={480}
          rot={index % 2 ? 3 : -3}
          color={SCRAPS[index % SCRAPS.length]}
          delay={4}
        />
      </div>
      {tags.map((t, i) => (
        <Tag key={i} text={t} x={spots[i].x} y={spots[i].y} rot={spots[i].r} delay={28 + i * 12} />
      ))}
      <TextBlock kicker={scene.kicker} heading={scene.heading} sub={scene.sub} top={745} size={56} />
    </Camera>
  );
};

const Timeline: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const evs = scene.events.slice(0, 4);
  const n = evs.length;
  const top = 330;
  const step = Math.min(190, 650 / n);
  const line = ease((frame - 6) / (n * 16 + 10));
  return (
    <Camera frames={scene.frames}>
      <TextBlock kicker={scene.kicker} heading={scene.heading} top={90} size={58} />
      <div
        style={{
          position: 'absolute',
          left: 58,
          top: top + 18,
          width: 8,
          height: (n * step - 20) * line,
          background: RED,
          borderRadius: 4,
        }}
      />
      {evs.map((e, i) => {
        const p = sp(frame, fps, 10 + i * 16, 13, 90);
        const y = top + i * step;
        const small = e.year.length > 5;
        return (
          <React.Fragment key={i}>
            <div
              style={{
                position: 'absolute',
                left: 44,
                top: y + 36,
                width: 36,
                height: 36,
                borderRadius: '50%',
                background: RED,
                border: `5px solid ${PAPER}`,
                transform: `scale(${clamp01(p)})`,
              }}
            />
            <div
              style={{
                position: 'absolute',
                left: 110,
                right: 36,
                top: y,
                height: step - 26,
                background: '#fffaf0',
                borderRadius: 6,
                border: `3px solid ${INK}`,
                boxShadow: `6px 6px 0 ${INK}`,
                display: 'flex',
                alignItems: 'center',
                gap: 22,
                padding: '0 24px',
                transform: `translateX(${(1 - clamp01(p)) * 140}px) rotate(${i % 2 ? 0.8 : -0.8}deg)`,
                opacity: clamp01(p * 2),
              }}
            >
              <div
                style={{
                  fontFamily: SERIF,
                  fontWeight: 900,
                  fontSize: small ? 34 : 54,
                  color: RED,
                  minWidth: small ? 150 : 150,
                  direction: 'ltr',
                }}
              >
                {e.year}
              </div>
              <div style={{fontFamily: SANS, fontWeight: 600, fontSize: 31, lineHeight: 1.2, color: INK}}>
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
  const stat = scene.stat as Stat;
  const t = interpolate(frame, [14, 80], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const v = stat.value * t;
  const dec = Math.min(2, (String(stat.value).split('.')[1] || '').length);
  const shown = dec
    ? v.toFixed(dec)
    : Math.round(v).toLocaleString('en-US');
  const txtLen = (stat.prefix + shown + stat.suffix).length;
  const big = txtLen > 11 ? 92 : txtLen > 8 ? 118 : txtLen > 5 ? 140 : 160;
  const strip = sp(frame, fps, 6, 15, 90);
  const under = ease((frame - 70) / 20);
  const lab = sp(frame, fps, 44, 18, 80);
  return (
    <Camera frames={scene.frames}>
      <TextBlock kicker={scene.kicker} heading={scene.heading} top={110} size={58} />
      <div style={{position: 'absolute', top: 400, left: 720 / 2 - 140}}>
        <Cutout
          src={scene.image}
          emoji={scene.emoji}
          size={280}
          rot={4}
          color={SCRAPS[(index + 1) % SCRAPS.length]}
          delay={2}
        />
      </div>
      <div
        style={{
          position: 'absolute',
          top: 730,
          left: 14,
          right: 14,
          height: 220,
          background: SCRAPS[index % SCRAPS.length],
          filter: 'url(#rough) drop-shadow(0 12px 12px rgba(40,25,10,0.3))',
          transform: `translateX(${(1 - clamp01(strip)) * -740}px) rotate(-1.5deg)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          top: 742,
          left: 0,
          right: 0,
          textAlign: 'center',
          fontFamily: SERIF,
          fontWeight: 900,
          fontSize: big,
          lineHeight: 1.15,
          color: INK,
          direction: 'ltr',
          opacity: clamp01(strip),
        }}
      >
        {stat.prefix}
        {shown}
        <span style={{fontSize: big * 0.55}}>{stat.suffix}</span>
      </div>
      <svg width={720} height={40} style={{position: 'absolute', top: 925, left: 0}}>
        <path
          d="M150 20 C 250 6, 330 32, 430 14 S 560 22, 575 16"
          stroke={RED}
          strokeWidth={9}
          fill="none"
          strokeLinecap="round"
          pathLength={1}
          strokeDasharray={1}
          strokeDashoffset={1 - under}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          top: 975,
          left: 50,
          right: 50,
          textAlign: 'center',
          fontFamily: SANS,
          fontWeight: 700,
          fontSize: 36,
          color: INK,
          opacity: clamp01(lab),
        }}
      >
        {stat.label}
      </div>
    </Camera>
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

const MapScene: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
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

  const dive0 = 0.03 * F;
  const dive1 = 0.26 * F;
  const legStart = dive1;
  const legEnd = 0.86 * F;
  const slice = (legEnd - legStart) / Math.max(N - 1, 1);

  let cx = c0[0];
  let cy = c0[1];
  let z = zAll;
  if (frame < dive1) {
    const t = ease((frame - dive0) / (dive1 - dive0));
    cx = lerp(c0[0], pts[0][0], t);
    cy = lerp(c0[1], pts[0][1], t);
    z = Math.exp(lerp(Math.log(zAll), Math.log(zIn), t));
  } else if (N === 1) {
    cx = pts[0][0];
    cy = pts[0][1];
    z = zIn * (1 + 0.18 * clamp01((frame - dive1) / Math.max(F - dive1, 1)));
  } else {
    const f = frame - legStart;
    const k = Math.min(N - 2, Math.floor(f / slice));
    const u = clamp01((f - k * slice) / slice);
    const t = ease((u - 0.2) / 0.8);
    const a = pts[k];
    const b = pts[k + 1];
    cx = lerp(a[0], b[0], t);
    cy = lerp(a[1], b[1], t);
    const zFit = clamp(
      0.55 * Math.min(vw / Math.max(Math.abs(b[0] - a[0]), 1), vh / Math.max(Math.abs(b[1] - a[1]), 1)),
      0.3,
      zIn
    );
    const s = Math.sin(Math.PI * t);
    z = Math.exp(lerp(Math.log(zIn), Math.log(zFit), s));
  }

  const vbx = cx - vw / (2 * z);
  const vby = cy - vh / (2 * z);
  const toScreen = (p: number[]) => [(p[0] - cx) * z + vw / 2, (p[1] - cy) * z + vh / 2];

  // route legs (curved arcs)
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
    const prog = ease((frame - (legStart + k * slice + 0.2 * slice)) / (0.8 * slice));
    legs.push({a, b, c, prog});
  }

  const plate = sp(frame, fps, 0, 18, 90);

  return (
    <>
      <TextBlock kicker={scene.kicker} heading={scene.heading} top={70} size={scene.heading.length > 22 ? 44 : 56} />
      <div
        style={{
          position: 'absolute',
          left: PLATE.x,
          top: PLATE.y,
          width: vw,
          height: vh,
          border: `5px solid ${INK}`,
          boxShadow: `8px 8px 0 ${INK}`,
          boxSizing: 'content-box',
          marginLeft: -5,
          marginTop: -5,
          overflow: 'hidden',
          background: OCEAN,
          transform: `translateY(${(1 - clamp01(plate)) * 60}px)`,
          opacity: clamp01(plate * 2),
        }}
      >
        <svg
          width={vw}
          height={vh}
          viewBox={`${vbx} ${vby} ${vw / z} ${vh / z}`}
          style={{position: 'absolute', left: 0, top: 0}}
        >
          <rect x={-WM} y={-WM} width={WM * 3} height={WM * 3} fill={OCEAN} />
          <path d={GRAT} stroke="rgba(60,90,100,0.28)" strokeWidth={1.2 / z} fill="none" />
          <path d={LAND} transform={`translate(${5 / z} ${8 / z})`} fill="rgba(60,40,20,0.30)" />
          <path d={LAND} fill={LANDC} stroke="#9b7f4e" strokeWidth={1.5 / z} strokeLinejoin="round" />
          <path
            d={BORDERS}
            fill="none"
            stroke="rgba(120,86,48,0.6)"
            strokeWidth={1 / z}
            strokeDasharray={`${6 / z} ${4 / z}`}
          />
          {legs.map((l, i) => (
            <path
              key={i}
              d={`M${l.a[0]} ${l.a[1]}Q${l.c[0]} ${l.c[1]} ${l.b[0]} ${l.b[1]}`}
              fill="none"
              stroke={RED}
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
            return (
              <circle key={'d' + i} cx={px} cy={py} r={11 / z} fill="#fffaf0" stroke={RED} strokeWidth={5 / z} />
            );
          })}
        </svg>
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
            boxShadow: 'inset 0 0 110px rgba(95,62,22,0.5)',
          }}
        />
        {scene.locations.map((loc, i) => {
          const arrive = i === 0 ? dive1 - 8 : legStart + i * slice - 8;
          const pp = sp(frame, fps, arrive, 10, 120);
          if (pp <= 0.01) return null;
          // label leaves when the camera departs for the next stop
          const labelOut =
            i < N - 1 ? clamp01(1 - (frame - (legStart + i * slice + 0.2 * slice)) / 10) : 1;
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
                  left: sx - 11,
                  top: sy - 11,
                  width: 22,
                  height: 22,
                  borderRadius: '50%',
                  background: RED,
                  border: '4px solid #fffaf0',
                  boxShadow: '0 3px 8px rgba(0,0,0,0.4)',
                  transform: `scale(${pp})`,
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  left: sx - 11,
                  top: sy - 11,
                  width: 22,
                  height: 22,
                  borderRadius: '50%',
                  border: `3px solid ${RED}`,
                  opacity: (1 - pulse) * 0.8,
                  transform: `scale(${1 + pulse * 2.4})`,
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  left: lx,
                  top: ly,
                  width: w,
                  background: '#fffaf0',
                  border: `3px solid ${INK}`,
                  boxShadow: `4px 4px 0 ${INK}`,
                  borderRadius: 6,
                  padding: '6px 12px',
                  textAlign: 'center',
                  transform: `scale(${clamp01(pp) * (0.85 + 0.15 * labelOut)})`,
                  opacity: clamp01(pp * 2) * labelOut,
                }}
              >
                <div style={{fontFamily: SERIF, fontWeight: 900, fontSize: 28, color: INK, lineHeight: 1.15}}>
                  {loc.name}
                </div>
                {loc.note ? (
                  <div style={{fontFamily: SANS, fontWeight: 600, fontSize: 20, color: RED}}>{loc.note}</div>
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
const SceneView: React.FC<{scene: Scene; index: number}> = ({scene, index}) => {
  const frame = useCurrentFrame();
  const lead = index > 0 ? OV : 0;
  const slide = index > 0 ? 1 - ease(frame / OV) : 0;

  let body: React.ReactNode;
  switch (scene.layout) {
    case 'map':
      body = <MapScene scene={scene} />;
      break;
    case 'timeline':
      body = <Timeline scene={scene} />;
      break;
    case 'stat':
      body = <StatScene scene={scene} index={index} />;
      break;
    case 'cutout':
      body = <CutoutScene scene={scene} index={index} />;
      break;
    default:
      body = <TitleScene scene={scene} />;
  }

  return (
    <AbsoluteFill
      style={{
        transform: `translateX(${slide * 105}%)`,
        filter: slide > 0 ? 'drop-shadow(-16px 0 20px rgba(0,0,0,0.35))' : undefined,
      }}
    >
      <Paper />
      <Sequence from={lead}>
        {scene.audio ? <Audio src={staticFile(scene.audio)} /> : null}
        {body}
        <Captions scene={scene} />
      </Sequence>
    </AbsoluteFill>
  );
};

export const Documentary: React.FC<Props> = ({rtl, scenes}) => {
  let start = 0;
  return (
    <AbsoluteFill style={{background: '#000', fontFamily: SANS, direction: rtl ? 'rtl' : 'ltr'}}>
      <Defs />
      {scenes.map((s, i) => {
        const lead = i > 0 ? OV : 0;
        const from = start - lead;
        start += s.frames;
        return (
          <Sequence key={i} from={from} durationInFrames={s.frames + lead}>
            <SceneView scene={s} index={i} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
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
PROMPT = """You are a documentary writer (think Vox-style storytelling). Write a short vertical social video about: {TOPIC}
Language of ALL narration and on-screen text: {LANGUAGE}. (image_prompt stays in English.)
Return ONLY JSON in this shape:
{
  "title": "max 4 words",
  "scenes": [
    {
      "narration": "20-30 words, vivid storytelling, spoken aloud, ONE idea per scene",
      "layout": "title | cutout | map | timeline | stat",
      "kicker": "2-3 word label, e.g. THE LEGEND",
      "heading": "max 7 words. Wrap ONE key word in *asterisks* to highlight it",
      "sub": "max 10 words (title and cutout layouts only)",
      "emoji": "one emoji",
      "image_prompt": "English. What the paper-cutout picture shows (title, cutout and stat layouts)",
      "tags": ["max 3 words"],
      "locations": [{"name": "place", "note": "max 3 words", "lat": 9.1, "lon": 40.5}],
      "events": [{"year": "1450", "text": "max 8 words"}],
      "stat": {"value": 71, "prefix": "", "suffix": "%", "label": "max 8 words"}
    }
  ]
}
Rules:
- Exactly 7 scenes. Scene 1 uses layout title (a curiosity hook). The last scene uses layout cutout (a memorable takeaway).
- Tell a story: hook, origin, journey or spread, turning point, impact, takeaway.
- map: only when real places are central. Give 2-4 locations in travel order with accurate decimal coordinates (south latitude and west longitude are negative). Use at most 2 map scenes.
- timeline: 3-4 dated events in chronological order. At most 1 timeline scene.
- stat: ONE famous, certain number. At most 1 stat scene.
- cutout: also give 2-3 short tags (a place, a date, a name).
- Fill only the fields the chosen layout needs.
- Only accurate, well-known facts. If you are unsure about a number or a date, choose another layout."""


def _num(v):
    try:
        return float(v)
    except Exception:
        return None


def _clean_scene(s):
    layout = s.get("layout") if s.get("layout") in LAYOUTS else "cutout"
    heading = str(s.get("heading") or "")[:60]
    out = {
        "narration": str(s["narration"]),
        "layout": layout,
        "kicker": str(s.get("kicker") or "")[:28],
        "heading": heading,
        "sub": str(s.get("sub") or "")[:70],
        "emoji": str(s.get("emoji") or "📜")[:8],
        "image_prompt": str(s.get("image_prompt") or "")[:300],
        "image": "",
        "tags": [str(t)[:22] for t in (s.get("tags") or []) if t][:3],
        "locations": [],
        "events": [],
        "stat": None,
    }

    for loc in s.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        lat, lon = _num(loc.get("lat")), _num(loc.get("lon"))
        if lat is None or lon is None or not (-85 <= lat <= 85 and -180 <= lon <= 180):
            continue
        out["locations"].append(
            {"name": str(loc.get("name") or "")[:26], "note": str(loc.get("note") or "")[:26], "lat": lat, "lon": lon}
        )
    out["locations"] = out["locations"][:4]

    for e in s.get("events") or []:
        if isinstance(e, dict) and e.get("year") and e.get("text"):
            out["events"].append({"year": str(e["year"])[:10], "text": str(e["text"])[:60]})
    out["events"] = out["events"][:4]

    st = s.get("stat")
    if isinstance(st, dict) and _num(st.get("value")) is not None:
        v = _num(st["value"])
        suffix = str(st.get("suffix") or "")[:12]
        if not suffix.strip() and v >= 1e9:
            v, suffix = v / 1e9, " billion"
        elif not suffix.strip() and v >= 1e6:
            v, suffix = v / 1e6, " million"
        v = round(v, 2) if v != int(v) else int(v)
        out["stat"] = {"value": v, "prefix": str(st.get("prefix") or "")[:4], "suffix": suffix, "label": str(st.get("label") or "")[:60]}

    # fall back when a layout has no data
    if layout == "map" and not out["locations"]:
        out["layout"] = "cutout"
    if layout == "timeline" and len(out["events"]) < 2:
        out["layout"] = "cutout"
    if layout == "stat" and not out["stat"]:
        out["layout"] = "cutout"
    if out["layout"] in ("title", "cutout", "stat") and not out["image_prompt"]:
        out["image_prompt"] = heading.replace("*", "")
    return out


def write_script(topic):
    import requests

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY missing. Secret me save karein.")

    prompt = PROMPT.replace("{TOPIC}", topic).replace("{LANGUAGE}", LANGUAGE)
    last = None
    for model in [m for m in MODELS if m]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(
                url,
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=120,
            )
        except Exception as e:
            last = f"{model}: {e}"
            continue
        if r.status_code != 200:
            last = f"{model}: {r.status_code} {r.text[:200]}"
            continue
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
            data = json.loads(text)
            scenes = [_clean_scene(s) for s in data["scenes"] if s.get("narration")]
            if not scenes:
                raise ValueError("no scenes")
            scenes[0]["layout"] = "title" if scenes[0]["layout"] in ("cutout", "stat", "title") else scenes[0]["layout"]
            title = str(data.get("title") or topic)[:40]
            print(f"Script ready with {model}: {[s['layout'] for s in scenes]}")
            return {"title": title, "scenes": scenes}
        except Exception as e:
            last = f"{model}: bad response ({e})"
    sys.exit(f"Gemini fail: {last}")


# ---------------------------------------------------------------- pictures (Pollinations, free)
def fetch_image(prompt, path, seed):
    import requests

    if os.environ.get("NO_IMAGES"):
        return False
    q = urllib.parse.quote(f"{prompt}, {IMAGE_STYLE}")
    url = f"https://image.pollinations.ai/prompt/{q}?width=768&height=768&nologo=true&model=flux&seed={seed}"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=120)
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 8000:
                Path(path).write_bytes(r.content)
                return True
            print(f"  image try {attempt + 1}: HTTP {r.status_code} {ctype} {len(r.content)}B")
        except Exception as e:
            print(f"  image try {attempt + 1}: {e}")
        time.sleep(4 * (attempt + 1))
        url = url.rsplit("seed=", 1)[0] + f"seed={seed + attempt + 1}"
    return False


def build_images(scenes):
    for i, s in enumerate(scenes):
        if s["layout"] in ("title", "cutout", "stat") and s["image_prompt"]:
            name = f"img{i}.jpg"
            ok = fetch_image(s["image_prompt"], PUBLIC / name, seed=1000 + i * 7)
            s["image"] = name if ok else ""
            print(f"Picture {i + 1}: {'ok' if ok else 'failed -> emoji fallback'}")


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

    final = []
    for i, s in enumerate(scenes):
        name = f"scene{i}.mp3"
        path = PUBLIC / name
        words = asyncio.run(tts(s["narration"], VOICE, str(path)))
        length = MP3(str(path)).info.length
        if not words:  # fallback: spread words evenly
            toks = s["narration"].split()
            words = [{"text": t, "start": length * k / max(len(toks), 1)} for k, t in enumerate(toks)]
        scene = {k: v for k, v in s.items() if k not in ("narration", "image_prompt")}
        scene.update(
            {
                "audio": name,
                "words": [{"text": w["text"], "frame": int(round(w["start"] * FPS))} for w in words],
                "frames": max(90, int(math.ceil((length + 0.6) * FPS))),
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
    print("== DOCUMENTARY VERSION v3 ==")

    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    scaffold()
    data = write_script(topic)
    scenes = build_scenes(data)
    rtl = any(x in LANGUAGE.lower() for x in RTL_LANGS)
    (VIDEO / "props.json").write_text(
        json.dumps({"title": data["title"], "rtl": rtl, "scenes": scenes}, ensure_ascii=False),
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

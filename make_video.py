"""
make_video.py - topic do, vertical explainer video (out.mp4) pao.

Usage:  python make_video.py "water cycle"

Steps: Gemini script + layouts -> Edge TTS voice + word timings -> Remotion render.
Env: GEMINI_API_KEY (required), VOICE (optional), LANGUAGE (optional),
     GEMINI_MODEL (optional).
"""
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FPS = 30
ROOT = Path(__file__).parent.resolve()
VIDEO = ROOT / "video"
PUBLIC = VIDEO / "public"
VOICE = os.environ.get("VOICE") or "en-US-AriaNeural"
LANGUAGE = os.environ.get("LANGUAGE") or "English"
MODELS = [os.environ.get("GEMINI_MODEL"), "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
LAYOUTS = ["hero", "flow", "stat", "bars", "compare"]
RTL_LANGS = ("urdu", "arabic", "persian", "farsi", "hebrew")

# ---------------------------------------------------------------- Remotion files
PACKAGE_JSON = r"""{
  "name": "explainer",
  "private": true,
  "dependencies": {
    "@remotion/cli": "4.0.250",
    "remotion": "4.0.250",
    "react": "18.3.1",
    "react-dom": "18.3.1"
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
import {Explainer, Props} from './Video';

export const Root: React.FC = () => {
  return (
    <Composition
      id="Explainer"
      component={Explainer}
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

VIDEO_TSX = r"""import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Easing,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type Word = {text: string; frame: number};
export type Item = {emoji: string; label: string};
export type Bar = {label: string; value: number};
export type Stat = {value: number; prefix: string; suffix: string; label: string};
export type Scene = {
  layout: 'hero' | 'flow' | 'stat' | 'bars' | 'compare';
  heading: string;
  emoji: string;
  bullets: string[];
  items: Item[];
  stat: Stat | null;
  bars: Bar[];
  audio: string;
  words: Word[];
  frames: number;
};
export type Props = {title: string; rtl: boolean; scenes: Scene[]};

const FONT =
  "'Noto Sans', 'Noto Sans Arabic', 'Noto Naskh Arabic', 'Noto Nastaliq Urdu', Arial, sans-serif";
const EMOJI = "'Noto Color Emoji', 'Apple Color Emoji', 'Segoe UI Emoji', sans-serif";

const PALETTES = [
  ['#0b1020', '#1d4ed8', '#22d3ee'],
  ['#1a0b2e', '#7e22ce', '#f472b6'],
  ['#042f2e', '#0f766e', '#a3e635'],
  ['#2a0a0a', '#c2410c', '#fbbf24'],
  ['#0f172a', '#4338ca', '#a78bfa'],
  ['#052e16', '#15803d', '#fde047'],
];

const pop = (frame: number, fps: number, delay = 0, damping = 12) =>
  spring({frame: frame - delay, fps, config: {damping, stiffness: 110}});

const smooth = (frame: number, fps: number, delay = 0) =>
  spring({frame: frame - delay, fps, config: {damping: 200}});

// ------------------------------------------------------------- background
const Background: React.FC<{index: number; frames: number}> = ({index, frames}) => {
  const frame = useCurrentFrame();
  const [c0, c1, c2] = PALETTES[index % PALETTES.length];
  const angle = interpolate(frame, [0, frames], [140, 220]);
  const blobs = Array.from({length: 7}).map((_, i) => {
    const seed = (i + 1) * 37 + index * 11;
    const x = (seed * 53) % 100;
    const y = (seed * 29) % 100;
    const size = 200 + ((seed * 13) % 260);
    const dx = Math.sin(frame / (40 + i * 7) + i) * 45;
    const dy = Math.cos(frame / (50 + i * 5) + i * 2) * 55;
    return (
      <div
        key={i}
        style={{
          position: 'absolute',
          left: `${x}%`,
          top: `${y}%`,
          width: size,
          height: size,
          borderRadius: '50%',
          background: i % 2 ? c2 : c1,
          opacity: 0.25,
          filter: 'blur(45px)',
          transform: `translate(${dx}px, ${dy}px)`,
        }}
      />
    );
  });
  return (
    <AbsoluteFill style={{background: `linear-gradient(${angle}deg, ${c0}, ${c1})`}}>
      {blobs}
      <AbsoluteFill
        style={{
          backgroundImage: 'radial-gradient(rgba(255,255,255,0.09) 2px, transparent 2px)',
          backgroundSize: '44px 44px',
          transform: `translateY(${(frame * 0.4) % 44}px)`,
        }}
      />
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------- shared pieces
const Heading: React.FC<{text: string; accent: string}> = ({text, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = smooth(frame, fps);
  const size = text.length > 22 ? 62 : 76;
  return (
    <>
      <div
        style={{
          position: 'absolute',
          top: 140,
          left: 360 - 60,
          width: 120 * p,
          height: 10,
          borderRadius: 5,
          background: accent,
          marginLeft: 60 * (1 - p),
        }}
      />
      <div
        style={{
          position: 'absolute',
          top: 170,
          left: 50,
          right: 50,
          textAlign: 'center',
          fontSize: size,
          fontWeight: 800,
          lineHeight: 1.12,
          opacity: p,
          transform: `translateY(${(1 - p) * 50}px)`,
          textShadow: '0 6px 24px rgba(0,0,0,0.35)',
        }}
      >
        {text}
      </div>
    </>
  );
};

const Emoji: React.FC<{char: string; size: number}> = ({char, size}) => (
  <span style={{fontFamily: EMOJI, fontSize: size, lineHeight: 1}}>{char}</span>
);

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
        bottom: 110,
        left: 30,
        right: 30,
        display: 'flex',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: 'rgba(0,0,0,0.5)',
          borderRadius: 34,
          padding: '18px 34px',
          textAlign: 'center',
          fontSize: 54,
          fontWeight: 800,
          lineHeight: 1.25,
          color: 'white',
        }}
      >
        {chunk.map((w, i) => {
          const on = start + i === active;
          return (
            <span
              key={start + i}
              style={{
                display: 'inline-block',
                color: on ? '#facc15' : 'white',
                transform: `scale(${on ? 1.1 : 1})`,
                marginInline: 8,
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

// ------------------------------------------------------------- layouts
const Hero: React.FC<{scene: Scene; accent: string}> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = pop(frame, fps, 6, 9);
  const float = Math.sin(frame / 14) * 12;
  const n = scene.bullets.length;
  return (
    <>
      <div style={{position: 'absolute', top: 350, left: 210, width: 300, height: 300}}>
        <svg width="300" height="300" style={{position: 'absolute', inset: 0}}>
          <circle cx="150" cy="150" r="120" fill="rgba(255,255,255,0.13)" />
          <circle
            cx="150"
            cy="150"
            r="140"
            fill="none"
            stroke={accent}
            strokeWidth="6"
            strokeDasharray="14 18"
            strokeLinecap="round"
            transform={`rotate(${frame * 0.8} 150 150)`}
          />
        </svg>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transform: `translateY(${float}px) scale(${p})`,
          }}
        >
          <Emoji char={scene.emoji} size={150} />
        </div>
      </div>
      <div
        style={{
          position: 'absolute',
          top: 690,
          left: 60,
          right: 60,
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        {scene.bullets.slice(0, 3).map((b, i) => {
          const t = smooth(frame, fps, 22 + i * Math.max(8, Math.floor(60 / Math.max(n, 1))));
          return (
            <div
              key={i}
              style={{
                fontSize: 40,
                fontWeight: 600,
                background: 'rgba(255,255,255,0.16)',
                borderLeft: `10px solid ${accent}`,
                borderRadius: 22,
                padding: '16px 26px',
                transform: `translateX(${(1 - t) * 140}px)`,
                opacity: t,
              }}
            >
              {b}
            </div>
          );
        })}
      </div>
    </>
  );
};

const Flow: React.FC<{scene: Scene; accent: string}> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items = scene.items.slice(0, 4);
  const STEP = 150;
  const TOP = 350;
  return (
    <>
      {items.map((it, i) => {
        const delay = 8 + i * 14;
        const p = pop(frame, fps, delay, 11);
        const line = interpolate(frame, [delay + 6, delay + 16], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        const top = TOP + i * STEP;
        return (
          <React.Fragment key={i}>
            {i < items.length - 1 ? (
              <div
                style={{
                  position: 'absolute',
                  left: 70 + 56 - 3,
                  top: top + 112,
                  width: 6,
                  height: (STEP - 112) * line,
                  background: accent,
                  borderRadius: 3,
                }}
              />
            ) : null}
            <div
              style={{
                position: 'absolute',
                left: 70,
                top,
                width: 112,
                height: 112,
                borderRadius: '50%',
                background: 'rgba(255,255,255,0.2)',
                border: `5px solid ${accent}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transform: `scale(${p})`,
              }}
            >
              <Emoji char={it.emoji} size={60} />
            </div>
            <div
              style={{
                position: 'absolute',
                left: 210,
                right: 50,
                top: top + 6,
                height: 100,
                borderRadius: 26,
                background: 'rgba(255,255,255,0.16)',
                display: 'flex',
                alignItems: 'center',
                padding: '0 28px',
                fontSize: 42,
                fontWeight: 700,
                opacity: p,
                transform: `translateX(${(1 - Math.min(p, 1)) * 100}px)`,
              }}
            >
              {it.label}
            </div>
          </React.Fragment>
        );
      })}
    </>
  );
};

const StatView: React.FC<{scene: Scene; accent: string}> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const stat = scene.stat as Stat;
  const t = interpolate(frame, [12, 75], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  const v = stat.value * t;
  const shown = Number.isInteger(stat.value)
    ? Math.round(v).toLocaleString('en-US')
    : v.toFixed(1);
  const p = pop(frame, fps, 4, 9);
  const float = Math.sin(frame / 14) * 10;
  const labelP = smooth(frame, fps, 40);
  return (
    <>
      <div
        style={{
          position: 'absolute',
          top: 350,
          width: '100%',
          textAlign: 'center',
          transform: `translateY(${float}px) scale(${p})`,
        }}
      >
        <Emoji char={scene.emoji} size={130} />
      </div>
      <div
        style={{
          position: 'absolute',
          top: 520,
          width: '100%',
          textAlign: 'center',
          fontWeight: 900,
          fontSize: 170,
          lineHeight: 1.1,
          color: accent,
          textShadow: '0 8px 30px rgba(0,0,0,0.4)',
          direction: 'ltr',
        }}
      >
        {stat.prefix}
        {shown}
        <span style={{fontSize: 90}}>{stat.suffix}</span>
      </div>
      <div
        style={{
          position: 'absolute',
          top: 760,
          left: 60,
          right: 60,
          textAlign: 'center',
          fontSize: 50,
          fontWeight: 700,
          opacity: labelP,
          transform: `translateY(${(1 - labelP) * 30}px)`,
        }}
      >
        {stat.label}
      </div>
    </>
  );
};

const Bars: React.FC<{scene: Scene; accent: string}> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const bars = scene.bars.slice(0, 4);
  const max = Math.max(...bars.map((b) => b.value), 1);
  return (
    <>
      {bars.map((b, i) => {
        const delay = 8 + i * 10;
        const t = interpolate(frame, [delay, delay + 40], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.out(Easing.cubic),
        });
        const a = smooth(frame, fps, delay);
        const shown = Number.isInteger(b.value)
          ? Math.round(b.value * t).toLocaleString('en-US')
          : (b.value * t).toFixed(1);
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: 50,
              right: 50,
              top: 370 + i * 135,
              opacity: a,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: 38,
                fontWeight: 700,
                marginBottom: 10,
              }}
            >
              <span>{b.label}</span>
              <span style={{color: accent, direction: 'ltr'}}>{shown}</span>
            </div>
            <div
              style={{
                height: 46,
                borderRadius: 23,
                background: 'rgba(255,255,255,0.16)',
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  height: '100%',
                  width: `${(b.value / max) * 100 * t}%`,
                  borderRadius: 23,
                  background: `linear-gradient(90deg, ${accent}, white)`,
                }}
              />
            </div>
          </div>
        );
      })}
    </>
  );
};

const Compare: React.FC<{scene: Scene; accent: string}> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const [a, b] = scene.items;
  const pa = smooth(frame, fps, 6);
  const pb = smooth(frame, fps, 14);
  const vs = pop(frame, fps, 24, 8);
  const card = (it: Item, left: number, p: number, dir: number) => (
    <div
      style={{
        position: 'absolute',
        left,
        top: 360,
        width: 300,
        height: 520,
        borderRadius: 36,
        background: 'rgba(255,255,255,0.16)',
        border: '3px solid rgba(255,255,255,0.3)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 36,
        textAlign: 'center',
        padding: 20,
        opacity: p,
        transform: `translateX(${(1 - p) * 220 * dir}px)`,
      }}
    >
      <Emoji char={it.emoji} size={130} />
      <div style={{fontSize: 42, fontWeight: 800, lineHeight: 1.15}}>{it.label}</div>
    </div>
  );
  return (
    <>
      {card(a, 40, pa, -1)}
      {card(b, 380, pb, 1)}
      <div
        style={{
          position: 'absolute',
          left: 360 - 45,
          top: 620 - 45,
          width: 90,
          height: 90,
          borderRadius: '50%',
          background: accent,
          color: '#111',
          fontSize: 40,
          fontWeight: 900,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transform: `scale(${vs})`,
          boxShadow: '0 6px 24px rgba(0,0,0,0.4)',
        }}
      >
        VS
      </div>
    </>
  );
};

// ------------------------------------------------------------- scene + root
const SceneView: React.FC<{scene: Scene; index: number}> = ({scene, index}) => {
  const frame = useCurrentFrame();
  const accent = PALETTES[index % PALETTES.length][2];
  const fadeIn = interpolate(frame, [0, 8], [0, 1], {extrapolateRight: 'clamp'});
  const fadeOut = interpolate(frame, [scene.frames - 6, scene.frames], [1, 0.0], {
    extrapolateLeft: 'clamp',
  });
  const zoom = interpolate(frame, [0, scene.frames], [1, 1.04]);

  let body: React.ReactNode;
  switch (scene.layout) {
    case 'flow':
      body = <Flow scene={scene} accent={accent} />;
      break;
    case 'stat':
      body = <StatView scene={scene} accent={accent} />;
      break;
    case 'bars':
      body = <Bars scene={scene} accent={accent} />;
      break;
    case 'compare':
      body = <Compare scene={scene} accent={accent} />;
      break;
    default:
      body = <Hero scene={scene} accent={accent} />;
  }

  return (
    <AbsoluteFill style={{color: 'white', opacity: Math.min(fadeIn, fadeOut + 0.0001)}}>
      <Background index={index} frames={scene.frames} />
      {scene.audio ? <Audio src={staticFile(scene.audio)} /> : null}
      <AbsoluteFill style={{transform: `scale(${zoom})`}}>
        <Heading text={scene.heading} accent={accent} />
        {body}
      </AbsoluteFill>
      <Captions scene={scene} />
    </AbsoluteFill>
  );
};

export const Explainer: React.FC<Props> = ({title, rtl, scenes}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  let from = 0;
  return (
    <AbsoluteFill
      style={{background: '#000', fontFamily: FONT, direction: rtl ? 'rtl' : 'ltr'}}
    >
      {scenes.map((s, i) => {
        const f = from;
        from += s.frames;
        return (
          <Sequence key={i} from={f} durationInFrames={s.frames}>
            <SceneView scene={s} index={i} />
          </Sequence>
        );
      })}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          height: 12,
          width: `${(frame / durationInFrames) * 100}%`,
          background: '#facc15',
        }}
      />
      <div
        style={{
          position: 'absolute',
          top: 52,
          width: '100%',
          textAlign: 'center',
          fontSize: 28,
          letterSpacing: 4,
          textTransform: 'uppercase',
          color: 'white',
          opacity: 0.7,
        }}
      >
        {title}
      </div>
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
    (VIDEO / "src" / "Video.tsx").write_text(VIDEO_TSX, encoding="utf-8")


# ---------------------------------------------------------------- Gemini script
PROMPT = """Write a short vertical social-media explainer video about: {TOPIC}
Language for all narration and on-screen text: {LANGUAGE}.
Return ONLY JSON in this shape:
{
  "title": "max 4 words",
  "scenes": [
    {
      "narration": "18-28 words, simple spoken sentences",
      "heading": "max 5 words",
      "layout": "hero | flow | stat | bars | compare",
      "emoji": "ONE emoji that illustrates the scene",
      "bullets": ["max 5 words", "max 5 words"],
      "items": [{"emoji": "🌊", "label": "max 3 words"}],
      "stat": {"value": 71, "prefix": "", "suffix": "%", "label": "short label"},
      "bars": [{"label": "short", "value": 10}]
    }
  ]
}
Rules:
- Exactly 6 scenes. Scene 1 is a curiosity hook (layout hero). Scene 6 is a takeaway (layout hero).
- Use at least 3 different layouts across the video.
- hero: use emoji + 2-3 bullets.
- flow: a process or ordered steps, 3-4 items (each with an emoji and a label). Fill only items.
- compare: exactly 2 items (A versus B). Fill only items.
- stat: ONE famous number. Fill emoji and stat.
- bars: 3-4 comparable numbers. Fill only bars.
- Fill only the fields that the chosen layout needs.
- Use only accurate, well-known facts. Use stat or bars only if you are certain the numbers are right; otherwise choose hero or flow.
- Never invent statistics."""


def _clean_scene(s):
    layout = s.get("layout") if s.get("layout") in LAYOUTS else "hero"
    emoji = str(s.get("emoji") or "✨")[:8]
    bullets = [str(b)[:60] for b in (s.get("bullets") or []) if b][:3]
    items = []
    for i in s.get("items") or []:
        if isinstance(i, dict) and i.get("label"):
            items.append({"emoji": str(i.get("emoji") or "✨")[:8], "label": str(i["label"])[:40]})
    items = items[:4]
    bars = []
    for b in s.get("bars") or []:
        try:
            bars.append({"label": str(b["label"])[:24], "value": float(b["value"])})
        except Exception:
            pass
    bars = bars[:4]
    stat = None
    st = s.get("stat")
    if isinstance(st, dict):
        try:
            v = float(st["value"])
            stat = {
                "value": int(v) if v == int(v) else v,
                "prefix": str(st.get("prefix") or "")[:4],
                "suffix": str(st.get("suffix") or "")[:6],
                "label": str(st.get("label") or "")[:60],
            }
        except Exception:
            stat = None

    if layout == "flow" and len(items) < 2:
        layout = "hero"
    if layout == "compare" and len(items) < 2:
        layout = "hero"
    if layout == "stat" and not stat:
        layout = "hero"
    if layout == "bars" and len(bars) < 2:
        layout = "hero"
    if layout == "hero" and not bullets:
        bullets = [str(s.get("heading") or "")[:40]] if s.get("heading") else []

    return {
        "narration": str(s["narration"]),
        "heading": str(s.get("heading") or "")[:50],
        "layout": layout,
        "emoji": emoji,
        "bullets": bullets,
        "items": items,
        "stat": stat,
        "bars": bars,
    }


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
            title = str(data.get("title") or topic)[:40]
            print(f"Script ready with {model}: {[s['layout'] for s in scenes]}")
            return {"title": title, "scenes": scenes}
        except Exception as e:
            last = f"{model}: bad response ({e})"
    sys.exit(f"Gemini fail: {last}")


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

    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)

    scenes = []
    for i, s in enumerate(data["scenes"]):
        name = f"scene{i}.mp3"
        path = PUBLIC / name
        words = asyncio.run(tts(s["narration"], VOICE, str(path)))
        length = MP3(str(path)).info.length
        if not words:  # fallback: spread words evenly
            toks = s["narration"].split()
            words = [
                {"text": t, "start": length * k / max(len(toks), 1)}
                for k, t in enumerate(toks)
            ]
        scene = {k: v for k, v in s.items() if k != "narration"}
        scene.update(
            {
                "audio": name,
                "words": [
                    {"text": w["text"], "frame": int(round(w["start"] * FPS))}
                    for w in words
                ],
                "frames": int(math.ceil((length + 0.6) * FPS)),
            }
        )
        scenes.append(scene)
        print(f"Voice {i + 1}/{len(data['scenes'])} done ({length:.1f}s)")
    return scenes


# ---------------------------------------------------------------- main
def run(cmd):
    subprocess.run(cmd, cwd=VIDEO, check=True, shell=(os.name == "nt"))


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python make_video.py "topic"')
    topic = " ".join(sys.argv[1:]).strip()

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
            "npx", "remotion", "render", "src/index.ts", "Explainer",
            str(out), "--props=props.json", "--concurrency=2",
        ]
    )
    print(f"Done: {out}")


if __name__ == "__main__":
    main()

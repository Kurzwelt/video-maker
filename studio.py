"""
studio.py - MinuteEarth-style still-frame videos. Do steps:

  python studio.py plan "topic"   -> Gemini script + one picture prompt per beat (shown in the run Summary)
  python studio.py render         -> uses the pictures you uploaded to frames/<id>/ -> out.mp4

Pictures are made by YOU in the free Gemini app (Nano Banana) using the prompts, so the style stays consistent.
Everything else (research, script, voice, captions, editing, render) is automatic.

Env: GEMINI_API_KEY (required), VOICE, LANGUAGE, MOTION (zoom | none), FORMAT (optional)
"""
import asyncio
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

FPS = 30
ROOT = Path(__file__).parent.resolve()
VIDEO = ROOT / "studio_build"
PUBLIC = VIDEO / "public"
PLAN = ROOT / "plan.json"
VOICE = os.environ.get("VOICE") or "en-US-AriaNeural"
LANGUAGE = os.environ.get("LANGUAGE") or "English"
RTL_LANGS = ("urdu", "arabic", "persian", "farsi", "hebrew")
MODELS_TEXT = [os.environ.get("GEMINI_MODEL"), "gemini-2.5-pro", "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
MODELS_VISION = [os.environ.get("GEMINI_MODEL"), "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]

STYLE_BIBLE = (
    "Flat 2D hand-drawn editorial cartoon illustration for a science explainer channel: thick, slightly wobbly black outlines, "
    "simple chunky shapes, soft flat colors (pastel greens, warm yellow, light grey, one orange accent), no gradients, no shadows, "
    "no textures. Plain off-white background (#FAFAFA). Simple stick-figure people: round head, two dot eyes, thin black stick limbs. "
    "Vertical 9:16 composition, main subject large and centered, generous empty space at the top and bottom. No text unless told."
)
CHARACTER = "Recurring narrator character: a stick figure with a round cream-colored head, big round glasses and a small red cap."

FORMATS = {
    "mystery": "A MYSTERY. Open with a baffling paradox or question. Reveal clues one by one, build tension, deliver a twist, then the answer.",
    "countdown": "A COUNTDOWN of 4-5 surprising items ordered from least to most shocking, one beat per item, then a one-line recap.",
    "myth_vs_fact": "MYTH vs FACT. Start from a popular belief people are sure about, break it with evidence 2-3 times, end with what to believe now.",
    "journey": "A JOURNEY through places and time, from origin to today, with real places and dates.",
    "numbers": "A NUMBERS SHOCK built around 3-4 astonishing, certain numbers, each explained right after it with an everyday comparison.",
    "cause_chain": "A DOMINO CHAIN of cause and effect: because of this -> that -> that. Each beat ends with a hook into the next link.",
    "how_it_works": "A HOW-IT-WORKS explainer in simple words with everyday analogies, step by step.",
}
TONES = [
    "calm, curious, gently funny narrator who loves surprising facts",
    "witty smart friend: playful, a little sarcastic, very clear",
    "gripping storyteller: serious, cinematic, building suspense",
]

# ---------------------------------------------------------------- Remotion project files
PACKAGE_JSON = r"""{
  "name": "studio",
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
import {Studio, Props} from './Studio';

export const Root: React.FC = () => {
  return (
    <Composition
      id="Studio"
      component={Studio}
      durationInFrames={300}
      fps={30}
      width={720}
      height={1280}
      defaultProps={{rtl: false, motion: 'zoom', scenes: []} as Props}
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
STUDIO_TSX = r"""import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type Word = {text: string; frame: number};
export type Scene = {
  image: string;
  bg: string;
  label: string;
  audio: string;
  words: Word[];
  frames: number;
};
export type Props = {rtl: boolean; motion: string; scenes: Scene[]};

const SANS = "'Noto Sans','Noto Sans Arabic','Noto Naskh Arabic','Noto Nastaliq Urdu','DejaVu Sans',Arial,sans-serif";
const FADE = 6; // frames of cross-fade between pictures

const Captions: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const CHUNK = 7;
  let active = 0;
  scene.words.forEach((w, i) => {
    if (frame >= w.frame) active = i;
  });
  const start = Math.floor(active / CHUNK) * CHUNK;
  const chunk = scene.words.slice(start, start + CHUNK);
  if (!chunk.length) return null;
  return (
    <div style={{position: 'absolute', left: 36, right: 36, bottom: 210, display: 'flex', justifyContent: 'center'}}>
      <div
        style={{
          background: '#ffffff',
          color: '#111',
          fontFamily: SANS,
          fontWeight: 700,
          fontSize: 44,
          lineHeight: 1.25,
          textAlign: 'center',
          padding: '12px 26px',
          borderRadius: 16,
          boxShadow: '0 4px 14px rgba(0,0,0,0.18)',
        }}
      >
        {chunk.map((w) => w.text).join(' ')}
      </div>
    </div>
  );
};

const Label: React.FC<{text: string}> = ({text}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const p = spring({frame: frame - 6, fps, config: {damping: 12, stiffness: 140}});
  if (!text) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: 40,
        top: 90,
        fontFamily: SANS,
        fontWeight: 900,
        fontSize: 64,
        color: '#1b1b1b',
        textShadow: '3px 3px 0 #fff, -3px -3px 0 #fff, 3px -3px 0 #fff, -3px 3px 0 #fff',
        transform: `scale(${Math.max(0, Math.min(1.05, p))}) rotate(-3deg)`,
        transformOrigin: 'left top',
      }}
    >
      {text}
    </div>
  );
};

const Frame: React.FC<{scene: Scene; index: number; motion: string}> = ({scene, index, motion}) => {
  const frame = useCurrentFrame();
  const lead = index > 0 ? FADE : 0;
  const op = index > 0 ? Math.min(1, frame / FADE) : 1;
  const t = Math.min(1, Math.max(0, frame / Math.max(scene.frames, 1)));
  const zoom = motion === 'none' ? 1 : 1 + 0.04 * t;
  const dir = index % 2 ? 1 : -1;
  return (
    <AbsoluteFill style={{background: scene.bg, opacity: op}}>
      <Img
        src={staticFile(scene.image)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          transform: motion === 'none' ? undefined : `scale(${zoom}) translate(${dir * t * 8}px, ${-t * 6}px)`,
        }}
      />
      <Sequence from={lead}>
        {scene.audio ? <Audio src={staticFile(scene.audio)} /> : null}
        <Label text={scene.label} />
        <Captions scene={scene} />
      </Sequence>
    </AbsoluteFill>
  );
};

export const Studio: React.FC<Props> = ({rtl, motion, scenes}) => {
  let start = 0;
  return (
    <AbsoluteFill style={{background: '#fafafa', direction: rtl ? 'rtl' : 'ltr'}}>
      {scenes.map((s, i) => {
        const lead = i > 0 ? FADE : 0;
        const from = start - lead;
        start += s.frames;
        return (
          <Sequence key={i} from={from} durationInFrames={s.frames + lead}>
            <Frame scene={s} index={i} motion={motion} />
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
    (VIDEO / "src" / "Studio.tsx").write_text(STUDIO_TSX, encoding="utf-8")


# ---------------------------------------------------------------- Gemini
_MODEL_CACHE = []


def discover_models(key):
    """Ask the API which models exist now (names change often), newest flash first."""
    if _MODEL_CACHE:
        return _MODEL_CACHE
    import requests

    names = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": key}, timeout=60)
        for m in r.json().get("models", []):
            n = m.get("name", "").replace("models/", "")
            if "generateContent" in m.get("supportedGenerationMethods", []) and n.startswith("gemini") and not any(x in n for x in ("image", "tts", "live", "embed", "audio", "robotics", "computer", "thinking-exp", "preview-")):
                names.append(n)
    except Exception as e:
        print("  model list failed:", e)

    def ver(n):
        v = re.findall(r"(\d+(?:\.\d+)?)", n)
        return float(v[0]) if v else 0.0

    flash = sorted([n for n in names if "flash" in n and "lite" not in n], key=lambda n: (-ver(n), len(n)))
    lite = sorted([n for n in names if "lite" in n], key=lambda n: (-ver(n), len(n)))
    pro = sorted([n for n in names if "pro" in n], key=lambda n: (-ver(n), len(n)))
    order = [os.environ.get("GEMINI_MODEL"), "gemini-flash-latest"] + flash + pro + lite
    seen = []
    for n in order:
        if n and n not in seen:
            seen.append(n)
    _MODEL_CACHE.extend(seen[:8])
    print("  models to try:", _MODEL_CACHE)
    return _MODEL_CACHE


def _gemini(parts, models, key, as_json, grounded=False):
    import requests

    last = None
    for model in discover_models(key):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"contents": [{"parts": parts}]}
        if as_json:
            body["generationConfig"] = {"responseMimeType": "application/json", "temperature": 1.0}
        if grounded:
            body["tools"] = [{"google_search": {}}]
        for attempt in range(3):
            try:
                r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
            except Exception as e:
                last = f"{model}: {e}"
                break
            if r.status_code in (429, 503) and attempt < 2:
                print(f"  gemini {model} busy ({r.status_code}), retry in 10s")
                time.sleep(10)
                continue
            if r.status_code != 200:
                last = f"{model}: {r.status_code} {r.text[:160]}"
                print("  gemini skip ->", last)
                break
            try:
                text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
                if text.strip():
                    return model, text
                last = f"{model}: empty"
            except Exception as e:
                last = f"{model}: bad response ({e})"
            break
    return None, last


BRIEF_PROMPT = """Research the topic: {TOPIC}
Write plain text notes for a video writer. Use real, verifiable facts only. Include:
1. THE ANGLE: the single most surprising or counter-intuitive angle that most videos miss.
2. TEN SPECIFIC FACTS with names, dates, numbers, places (mark any you are unsure about with '?').
3. THREE strong opening lines (each under 12 words, no greetings).
4. THE TWIST or deeper meaning to end on.
Keep it under 400 words."""

PLAN_PROMPT = """You are a top script writer for illustrated explainer videos (think the best science-explainer channels: clear, surprising, funny, never boring).
Write the script for a vertical video about: {TOPIC}
LANGUAGE of narration and labels: {LANGUAGE}. Natural, spoken, conversational. The image_prompt stays in English.
STRUCTURE: {FORMAT}
TONE: {TONE}
RESEARCH NOTES (rely on these facts, plus things you are 100% sure of):
{BRIEF}

The video is a sequence of BEATS. Each beat = one narrated sentence or two + ONE full illustrated picture that stays on screen.
Return ONLY JSON:
{
  "title": "max 5 words",
  "beats": [
    {
      "narration": "10-24 words, spoken aloud, ONE idea",
      "label": "optional date or number shown in the corner like 1850s or 400 tons, max 14 characters, else empty",
      "image_prompt": "English. Describe ONE clear scene to illustrate this beat (see rules)"
    }
  ]
}
Rules:
- 10 to 14 beats, total narration about 170-220 words (about 70-85 seconds).
- Beat 1 is the HOOK: a bold claim, paradox or question in the first words. NEVER start with a greeting, 'welcome', 'imagine', 'did you know', 'in this video', 'have you ever'.
- Every 2-3 beats open a curiosity gap and pay it off soon after. Concrete beats adjectives: names, numbers, dates, comparisons to everyday things. Vary sentence rhythm. No filler.
- Last beat: a one-line takeaway that reframes everything, then a question that makes people comment.
- image_prompt rules: describe the scene only (the art style is added separately). One main subject, 2-4 simple elements, a clear visual idea or metaphor that explains the sentence. Keep top and bottom of the picture empty (captions cover the bottom). No text in the picture except an optional label of at most 3 words that you spell exactly. If a person is needed use the recurring stick-figure narrator.
- Facts must be accurate. If unsure about a number, choose another way to make the point."""


def plan(topic):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY missing. Secret me save karein.")
    rnd = random.Random()
    fmt = os.environ.get("FORMAT") if os.environ.get("FORMAT") in FORMATS else rnd.choice(list(FORMATS))
    tone = rnd.choice(TONES)
    print(f"Format: {fmt} | Tone: {tone}")

    brief = ""
    for grounded in (True, False):
        m, txt = _gemini([{"text": BRIEF_PROMPT.replace("{TOPIC}", topic)}], MODELS_TEXT[1:], key, False, grounded)
        if m:
            brief = txt.strip()[:5000]
            print(f"Research brief ready with {m} (search grounding: {grounded})")
            break

    prompt = (
        PLAN_PROMPT.replace("{TOPIC}", topic)
        .replace("{LANGUAGE}", LANGUAGE)
        .replace("{FORMAT}", FORMATS[fmt])
        .replace("{TONE}", tone)
        .replace("{BRIEF}", brief or "(none - use only facts you are certain about)")
    )
    last = None
    for _try in range(3):
        m, text = _gemini([{"text": prompt}], None, key, True)
        if not m:
            last = text
            continue
        model = m
        try:
            text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
            data = json.loads(text)
            beats = []
            for b in data["beats"]:
                if b.get("narration") and b.get("image_prompt"):
                    beats.append({
                        "narration": str(b["narration"]).strip(),
                        "label": str(b.get("label") or "").strip()[:14],
                        "image_prompt": str(b["image_prompt"]).strip()[:600],
                    })
            if len(beats) < 4:
                raise ValueError("too few beats")
            slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:24] or "video"
            vid = f"{datetime.utcnow():%m%d-%H%M}-{slug}"
            out = {
                "title": str(data.get("title") or topic)[:50],
                "topic": topic,
                "language": LANGUAGE,
                "format": fmt,
                "folder": f"frames/{vid}",
                "style": STYLE_BIBLE,
                "character": CHARACTER,
                "beats": beats,
            }
            print(f"Script ready with {m}: {len(beats)} beats")
            return out
        except Exception as e:
            last = f"{model}: bad script ({e})"
            print("  ", last)
    sys.exit(f"Gemini fail: {last}")


def write_plan_outputs(p):
    PLAN.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    folder = ROOT / p["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "UPLOAD_PICTURES_HERE.txt").write_text("Is folder me is video ki pictures upload karein.\n", encoding="utf-8")

    lines = [
        f"# {p['title']}",
        "",
        f"Format: **{p['format']}** | Beats: **{len(p['beats'])}**",
        "",
        "## Step 1 - Gemini app me NAYI chat kholein, ye style paste karein (sirf ek baar)",
        "",
        "```",
        p["style"] + " " + p["character"] + " Reply OK, then I will send frames one by one. Keep exactly this style for every frame.",
        "```",
        "",
        "## Step 2 - ab har frame ka prompt isi chat me bhejein (Nano Banana / image on)",
        "Har prompt ke aage likhein: *Frame N, vertical 9:16:*  (neeche already likha hai). Ek frame ek image.",
        "",
    ]
    for i, b in enumerate(p["beats"], 1):
        lines += [f"### Frame {i}", "", "```", f"Frame {i}, vertical 9:16: {b['image_prompt']}", "```", f"_Narration:_ {b['narration']}", ""]
    lines += [
        "## Step 3 - pictures upload karein",
        f"GitHub repo me folder kholein: `{p['folder']}` -> Add file -> Upload files -> sari pictures select karein -> Commit.",
        "(Pictures ka order mix bhi ho to koi masla nahi, system khud sahi jagah laga lega.)",
        "",
        "## Step 4 - Actions -> Studio -> Run workflow -> step: render",
    ]
    md = "\n".join(lines)
    (ROOT / "plan.md").write_text(md, encoding="utf-8")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    print(md)


# ---------------------------------------------------------------- render
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def corner_bg(img):
    """Page colour of a picture = average of its corners, so any aspect ratio blends in."""
    w, h = img.size
    px = [img.getpixel(p) for p in [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3), (w // 2, 2), (w // 2, h - 3)]]
    r = sum(p[0] for p in px) // len(px)
    g = sum(p[1] for p in px) // len(px)
    b = sum(p[2] for p in px) // len(px)
    return f"#{r:02x}{g:02x}{b:02x}"


def load_frames(folder):
    from PIL import Image

    files = sorted([f for f in Path(folder).iterdir() if f.suffix.lower() in IMG_EXT], key=lambda f: natural_key(f.name))
    out = []
    for i, f in enumerate(files):
        img = Image.open(f).convert("RGB")
        img.thumbnail((1600, 1600))
        name = f"frame{i}.jpg"
        img.save(PUBLIC / name, quality=92)
        out.append({"file": name, "orig": f.name, "bg": corner_bg(img), "path": PUBLIC / name})
    return out


def match_frames(beats, frames):
    """Gemini vision: which picture belongs to which beat (so upload order does not matter)."""
    n, m = len(frames), len(beats)
    default = list(range(min(n, m)))
    key = os.environ.get("GEMINI_API_KEY")
    if not key or n < 2:
        return default
    import base64
    from PIL import Image
    import io

    parts = [{"text": "Match each picture to the narrated beat it illustrates best.\nBEATS:\n" + "\n".join(f"{j + 1}. {b['narration']}  [scene: {b['image_prompt'][:140]}]" for j, b in enumerate(beats))
              + f"\n\nBelow are {n} pictures labelled IMAGE 1..{n}. Return ONLY JSON: {{\"assignment\": [beat_number_for_image_1, beat_number_for_image_2, ...]}}. Use each beat at most once."}]
    for i, f in enumerate(frames):
        img = Image.open(f["path"]).convert("RGB")
        img.thumbnail((512, 512))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        parts.append({"text": f"IMAGE {i + 1}:"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}})
    m_used, text = _gemini(parts, MODELS_VISION, key, True)
    if not m_used:
        print("Picture matching skipped:", text)
        return default
    try:
        asg = json.loads(re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip()))["assignment"]
        beat_of = [int(x) - 1 for x in asg]
        if len(beat_of) != n or len(set(beat_of)) != n or any(not (0 <= b < m) for b in beat_of):
            raise ValueError(f"bad mapping {asg}")
        print(f"Pictures matched with {m_used}: image->beat {[b + 1 for b in beat_of]}")
        return beat_of
    except Exception as e:
        print("Picture matching failed, using file order:", e)
        return default


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


def run(cmd):
    subprocess.run(cmd, cwd=VIDEO, check=True, shell=(os.name == "nt"))


def render():
    from mutagen.mp3 import MP3

    if not PLAN.exists():
        sys.exit("plan.json nahi mili. Pehle step=plan chalayein.")
    p = json.loads(PLAN.read_text(encoding="utf-8"))
    folder = ROOT / p["folder"]
    if not folder.exists():
        sys.exit(f"Folder {p['folder']} nahi mila.")
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    scaffold()
    frames = load_frames(folder)
    if not frames:
        sys.exit(f"{p['folder']} me koi picture nahi mili. Pictures upload karke dobara chalayein.")
    beats = p["beats"]
    beat_of = match_frames(beats, frames)  # frame i -> beat index
    img_for_beat = {b: i for i, b in enumerate(beat_of)}
    print(f"{len(frames)} pictures for {len(beats)} beats")

    scenes, prev = [], 0
    for j, b in enumerate(beats):
        fi = img_for_beat.get(j, prev)  # a beat without its own picture keeps the previous one
        prev = fi
        name = f"beat{j}.mp3"
        words = asyncio.run(tts(b["narration"], os.environ.get("VOICE") or p.get("voice") or VOICE, str(PUBLIC / name)))
        length = MP3(str(PUBLIC / name)).info.length
        if not words:
            toks = b["narration"].split()
            words = [{"text": t, "start": length * k / max(len(toks), 1)} for k, t in enumerate(toks)]
        scenes.append({
            "image": frames[fi]["file"],
            "bg": frames[fi]["bg"],
            "label": b.get("label", ""),
            "audio": name,
            "words": [{"text": w["text"], "frame": int(round(w["start"] * FPS))} for w in words],
            "frames": max(60, int(math.ceil((length + 0.45) * FPS))),
        })
        print(f"Voice {j + 1}/{len(beats)} done ({length:.1f}s)")

    rtl = any(x in (p.get("language") or LANGUAGE).lower() for x in RTL_LANGS)
    motion = "none" if os.environ.get("MOTION") == "none" else "zoom"
    (VIDEO / "props.json").write_text(json.dumps({"rtl": rtl, "motion": motion, "scenes": scenes}, ensure_ascii=False), encoding="utf-8")
    run(["npm", "install", "--no-audit", "--no-fund"])
    out = ROOT / "out.mp4"
    run(["npx", "remotion", "render", "src/index.ts", "Studio", str(out), "--props=props.json", "--concurrency=2", "--timeout=120000"])
    print(f"Done: {out}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("plan", "render"):
        sys.exit('Usage: python studio.py plan "topic"  |  python studio.py render')
    print("== STUDIO v1 (still-frame explainer) ==")
    if sys.argv[1] == "plan":
        topic = " ".join(sys.argv[2:]).strip()
        if not topic:
            sys.exit("Topic likhna zaroori hai.")
        write_plan_outputs(plan(topic))
    else:
        render()


if __name__ == "__main__":
    main()

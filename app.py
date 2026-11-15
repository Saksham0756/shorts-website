"""Shortsmith: YouTube link -> captioned 9:16 shorts. Run: python app.py"""
import json, os, re, subprocess, threading, uuid
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).parent
WORK = ROOT / "jobs"; WORK.mkdir(exist_ok=True)
app = Flask(__name__, static_folder="static")
JOBS = {}
_whisper = None

def whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(os.getenv("WHISPER_MODEL", "base"), compute_type="int8")
    return _whisper

def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode: raise RuntimeError(r.stderr[-600:])

def transcribe(video):
    segs, _ = whisper().transcribe(str(video), word_timestamps=True, vad_filter=True)
    out = []
    for s in segs:
        words = [{"w": w.word.strip(), "s": w.start, "e": w.end} for w in (s.words or [])]
        out.append({"s": s.start, "e": s.end, "t": s.text.strip(), "words": words})
    return out

HOOKS = re.compile(r"\b(secret|never|mistake|why|how|truth|biggest|best|worst|stop|free|money|crazy|insane|shocking|you)\b", re.I)

def pick_heuristic(segs, n, dur):
    cands = []
    for i in range(len(segs)):
        j = i
        while j < len(segs) and segs[j]["e"] - segs[i]["s"] <= dur: j += 1
        if j == i: continue
        chunk = segs[i:j]; text = " ".join(c["t"] for c in chunk)
        length = chunk[-1]["e"] - chunk[0]["s"]
        if length < dur * 0.5: continue
        score = len(text.split()) / length + 0.4 * len(HOOKS.findall(text)) + 0.3 * text.count("?") + 0.5 * bool(HOOKS.search(chunk[0]["t"]))
        cands.append((score, chunk[0]["s"], chunk[-1]["e"], chunk[0]["t"][:60]))
    picked = []
    for sc, s, e, t in sorted(cands, reverse=True):
        if all(e <= p["start"] or s >= p["end"] for p in picked):
            picked.append({"start": s, "end": e, "title": t})
        if len(picked) == n: break
    return sorted(picked, key=lambda p: p["start"])

def pick_ai(segs, n, dur):
    import anthropic
    tx = "\n".join(f"[{s['s']:.1f}-{s['e']:.1f}] {s['t']}" for s in segs)[:60000]
    msg = anthropic.Anthropic().messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=1500,
        messages=[{"role": "user", "content":
          f"Pick the {n} most viral-worthy moments from this transcript for YouTube Shorts. "
          f"Each must be a self-contained story or insight, start on a strong hook, last about {dur}s (max {dur+10}s), "
          f"and align to timestamps. Reply ONLY with JSON: "
          f'[{{"start":0.0,"end":0.0,"title":"catchy title"}}]\n\n{tx}'}])
    txt = re.sub(r"```json|```", "", msg.content[0].text).strip()
    return json.loads(txt)

def ass_for(words, t0, path):
    def ts(x):
        x = max(x, 0); return f"{int(x//3600)}:{int(x%3600//60):02d}:{x%60:05.2f}"
    head = ("[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
      "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
      "Style: Default,DejaVu Sans,80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,2,2,60,60,520,1\n\n"
      "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    for g in range(0, len(words), 3):
        grp = words[g:g+3]
        for k, w in enumerate(grp):
            end = grp[k+1]["s"] if k+1 < len(grp) else w["e"]
            txt = " ".join((f"{{\\c&H00D2FF&}}{x['w'].upper()}{{\\c&HFFFFFF&}}" if m == k else x["w"].upper()) for m, x in enumerate(grp))
            lines.append(f"Dialogue: 0,{ts(w['s']-t0)},{ts(end-t0)},Default,,0,0,0,,{txt}")
    Path(path).write_text(head + "\n".join(lines), encoding="utf-8")

def render(src, job, idx, clip, segs, mode):
    d = WORK / job
    words = [w for s in segs for w in s["words"] if clip["start"] <= w["s"] < clip["end"]]
    ass_for(words, clip["start"], d / f"c{idx}.ass")
    if mode == "crop":
        vf = f"scale=-2:1920,crop=1080:1920,subtitles=c{idx}.ass"
    else:
        vf = ("split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
              f"[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles=c{idx}.ass")
    run(["ffmpeg", "-y", "-ss", str(clip["start"]), "-to", str(clip["end"]), "-i", "src.mp4",
         "-vf", vf, "-af", "loudnorm=I=-14", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", f"short_{idx}.mp4"], cwd=d)

def pipeline(job, url, n, dur, mode):
    J = JOBS[job]; d = WORK / job; d.mkdir(exist_ok=True)
    try:
        J.update(step="Downloading video", pct=5)
        cmd = ["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
               "--no-playlist", "-o", str(d / "src.mp4"), url]
        if os.getenv("YTDLP_COOKIES"): cmd[1:1] = ["--cookies", os.getenv("YTDLP_COOKIES")]
        run(cmd)
        J.update(step="Transcribing speech", pct=25)
        segs = transcribe(d / "src.mp4")
        if not segs: raise RuntimeError("No speech found in this video.")
        J.update(step="Finding the best moments", pct=50)
        try:
            clips = pick_ai(segs, n, dur) if os.getenv("ANTHROPIC_API_KEY") else pick_heuristic(segs, n, dur)
        except Exception:
            clips = pick_heuristic(segs, n, dur)
        for i, c in enumerate(clips):
            J.update(step=f"Editing short {i+1} of {len(clips)}", pct=55 + int(40 * i / max(len(clips), 1)))
            render(d / "src.mp4", job, i, c, segs, mode)
            J["clips"].append({"title": c["title"], "url": f"/jobs/{job}/short_{i}.mp4", "start": c["start"], "end": c["end"]})
        J.update(step="Done", pct=100, done=True)
    except Exception as e:
        J.update(error=str(e), done=True)

def cleanup_loop():
    import shutil, time
    while True:
        time.sleep(1800)
        for d in WORK.iterdir():
            if d.is_dir() and time.time() - d.stat().st_mtime > int(os.getenv("KEEP_HOURS", "6")) * 3600:
                shutil.rmtree(d, ignore_errors=True); JOBS.pop(d.name, None)

threading.Thread(target=cleanup_loop, daemon=True).start()

@app.post("/api/process")
def process():
    b = request.get_json(force=True)
    url = (b.get("url") or "").strip()
    if not re.match(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", url):
        return jsonify(error="Paste a valid YouTube link."), 400
    job = uuid.uuid4().hex[:10]
    JOBS[job] = {"step": "Queued", "pct": 0, "clips": [], "done": False, "error": None}
    threading.Thread(target=pipeline, daemon=True, args=(
        job, url, min(int(b.get("count", 3)), 8), int(b.get("duration", 40)), b.get("mode", "blur"))).start()
    return jsonify(job=job)

@app.get("/api/job/<job>")
def status(job): return jsonify(JOBS.get(job) or {"error": "Unknown job", "done": True})

@app.get("/jobs/<job>/<name>")
def files(job, name): return send_from_directory(WORK / job, name)

@app.get("/")
def home(): return send_from_directory("static", "index.html")

if __name__ == "__main__":
    app.run(port=5000, threaded=True)

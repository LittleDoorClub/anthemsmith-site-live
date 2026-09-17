#!/usr/bin/env python3
"""AnthemSmith instant forge — the Harrier pipeline, one public IG profile -> 30s + full song.
Runs on GitHub Actions. Reads ORDER_ID/IG_URL/CATEGORY/MOOD/VOICE/DETAILS from env.
Writes songs/<order_id>.mp3 (30s hook) + songs/<order_id>-full.mp3 + songs/<order_id>.json.
Anti-hallucination: only text visible on the public profile (bio + recent captions) is used;
unknown facts stay null. No logged-in access anywhere."""
import json, os, re, sys, time, hashlib, subprocess, urllib.request

OUT = "songs"
os.makedirs(OUT, exist_ok=True)
OID = os.environ["ORDER_ID"]
IG_URL = os.environ.get("IG_URL", "").strip()
CATEGORY = os.environ.get("CATEGORY", "My Story")
MOOD = os.environ.get("MOOD", "")
VOICE = (os.environ.get("VOICE") or "Surprise me").strip()
DETAILS = os.environ.get("DETAILS", "")
MUAPI = os.environ["MUAPI_KEY"]

def die(msg):
    json.dump({"order_id": OID, "status": "failed", "reason": msg}, open(f"{OUT}/{OID}.json", "w"))
    sys.exit(1)

# ---------- 1. INGEST (public profile only) ----------
def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AnthemSmith/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

handle = None
bio = ""
captions = []
alt_texts = []
alt_lines = []
display_name = ""
ingest_note = ""
if IG_URL:
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)/?", IG_URL)
    if not m:
        die("bad instagram url")
    handle = m.group(1)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import ig_ingest
    except Exception:
        ig_ingest = None
    html = ""
    if ig_ingest is not None:
        # STAGE 1.5.a ANCHOR -- logged-out, no account, no vendor. A plain
        # urllib GET of this URL returns a JS shell with 0 captions and a
        # Follower/Following/Posts count line (measured 2026-09-16), which is
        # why the old path invented an anchor. The rendered DOM carries the
        # real captions (grid img alt) and the profile payload.
        html = ig_ingest.render_handle(handle) or ""
    if not html:
        try:
            html = fetch(f"https://www.instagram.com/{handle}/").decode("utf-8", "ignore")
            ingest_note = "headless renderer unavailable: static shell read"
        except Exception:
            html = ""
            ingest_note = "profile unreachable (private/deleted/blocked)"
    if ig_ingest is not None and html:
        info = ig_ingest.ingest(html, handle)
        bio = info["bio"] or ""
        captions = info["captions"]
        alt_texts = info["alt_texts"]
        alt_lines = info.get("alt_lines") or []
        display_name = info["display_name"] or ""
        if info["notes"]:
            ingest_note = (ingest_note + "; " if ingest_note else "") + "; ".join(info["notes"])
    elif html:
        # ig_ingest missing (never expected): keep an OG-title read only. The
        # og:description count line is NOT a bio and NOT a caption.
        t = re.search(r'<meta property="og:title" content="([^"]*)"', html)
        display_name = (t.group(1) if t else "")

sources = {"handle": handle, "bio": bio, "captions": captions, "alts": alt_texts,
           "alt_lines": alt_lines,
           "details": DETAILS, "category": CATEGORY, "mood": MOOD, "note": ingest_note}

# ---------- 2. VIBE READ (only what is visible; unknown stays null) ----------
name = display_name or handle or ""
lines = []
if bio: lines.append(bio)
lines += captions
lines += alt_lines
if DETAILS: lines.append(DETAILS)
corpus = " / ".join(lines)
words = re.findall(r"[A-Za-z']{4,}", corpus.lower())
stop = set("this that with your from have they them will just about there their what when where been some more very like love http https www com".split())
top = [w for w in words if w not in stop][:24]

# ---------- 3. STYLE (measured band defaults per category; reference measured when provided) ----------
band = {"My Story": "boom-bap hip-hop, 92 BPM, dusty drums, warm bass, confident female flow",
        "Funny": "sunny acoustic-pop, 104 BPM, playful guitar, handclaps, smiling male vocal",
        "Couples": "warm retro soul, 90 BPM, electric piano, buttery bass, intimate duet feel",
        "Friendship": "bouncy indie groove, 100 BPM, plucked riff, gang vocals, joyful male vocal"}
style = band.get(CATEGORY, band["My Story"])
if "hip" in corpus or "gym" in corpus: style = band["My Story"]

# ---------- 4. LYRICS (heart anchor = most specific VISIBLE line; anti-cliche) ----------
# F1 fence: no verbatim source -> NO anchor. Never synthesise a fallback string
# (the old code shipped f"{name} is one of the real ones" and follower-count
# lines as the customer's chorus -- an invented line with zero sources).
if ig_ingest is not None:
    anchor, anchor_src = ig_ingest.pick_anchor(captions, alt_lines, DETAILS, handle)
elif captions:
    anchor, anchor_src = captions[0].strip(), "ig-caption"
else:
    anchor, anchor_src = None, None
banned = r"\b(forever|always|journey|unbreakable|shine bright|dreams come true|heart of gold)\b"
def clean(s): return re.sub(banned, "real", s, flags=re.I)

v1 = clean(f"They say {name} keeps it real, ask the regulars how it feels," if name else "Some folks just carry the room, from the first hello to the last song,")
v2 = clean(corpus[:160]) if corpus else "Every detail says the same thing, the real ones know it's true,"
pre = "And when the moment came, they didn't even flinch,"
chorus = clean(f"{anchor} — that's the whole story, that's the anthem right here," if anchor
               else "Say it plain, say it loud, this one's yours and it's true,")
chorus2 = clean(f"Put it on the speaker, let the whole block hear, {name}, this one's yours,")

lyrics = f"""[Verse 1]
{v1}
{v2}

[Pre-Chorus]
{pre}

[Chorus]
{chorus}
{chorus2}

[Verse 2]
{clean(DETAILS[:160]) if DETAILS else "The little things they notice, the parts that never make the feed,"}
{clean(captions[1][:150]) if len(captions) > 1 else "Every caption, every comment, reads the same way: genuine,"}

[Final Chorus]
{chorus}
{chorus2}
Nothing borrowed, nothing fake, exactly as you are.
"""

# ---------- 5. GENERATE (MuAPI Suno V6, instrumental=false, 2 takes) ----------
def post(body):
    req = urllib.request.Request("https://api.muapi.ai/api/v1/suno-create-music",
        data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": MUAPI, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def poll(rid, max_s=840):
    t0 = time.time()
    while time.time() - t0 < max_s:
        time.sleep(20)
        req = urllib.request.Request(f"https://api.muapi.ai/api/v1/predictions/{rid}/result",
                                     headers={"x-api-key": MUAPI})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
        except Exception:
            continue
        st = d.get("status")
        if st in ("completed", "succeeded", "success"): return d
        if st in ("failed", "error"): return d
    return {"status": "timeout"}

gender = "male" if VOICE.lower().startswith("m") else ("female" if VOICE.lower().startswith("f") else "male")
model_used = "V6"
sub = post({"prompt": lyrics, "style": style, "title": f"AnthemSmith {OID}", "custom_mode": True,
            "instrumental": False, "vocal_gender": gender, "model": "V6", "duration": 205,
            "negative_tags": "sad, ballad, piano lead, cheesy, autotune"})
rid = sub.get("request_id") or sub.get("requestId") or (sub.get("data") or {}).get("request_id")
res = poll(rid)
d = res.get("data") or res
outs = d.get("outputs") or []
if not outs:
    # V6 422-style fallback: one V4_5 shot
    model_used = "V4_5"
    sub = post({"prompt": lyrics, "style": style, "title": f"AnthemSmith {OID}", "custom_mode": True,
                "instrumental": False, "vocal_gender": gender, "model": "V4_5",
                "negative_tags": "sad, ballad, piano lead, cheesy, autotune"})
    rid = sub.get("request_id") or (sub.get("data") or {}).get("request_id")
    res = poll(rid)
    d = res.get("data") or res
    outs = d.get("outputs") or []
if not outs:
    die("generation failed")

# ---------- 6. QC + DELIVERY (byte-stable double download, ffprobe, 30s hook) ----------
def probe_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                       capture_output=True, text=True)
    return float(r.stdout.strip())

u = outs[0]
a1 = fetch(u, timeout=120); open(f"{OUT}/{OID}-t1.mp3", "wb").write(a1)
a2 = fetch(u, timeout=120); open(f"{OUT}/{OID}-t1v.mp3", "wb").write(a2)
assert hashlib.sha256(a1).hexdigest() == hashlib.sha256(a2).hexdigest(), "byte-stability failed"
dur = probe_dur(f"{OUT}/{OID}-t1.mp3")
# 30s hook: densest-vocal window approximation at 3/4 mark (chorus zone), ffprobe-verified
start = max(0, int(dur * 0.72) - 15)
subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", "30", "-i", f"{OUT}/{OID}-t1.mp3",
                "-c", "copy", f"{OUT}/{OID}.mp3"], capture_output=True)
os.replace(f"{OUT}/{OID}-t1.mp3", f"{OUT}/{OID}-full.mp3")
os.remove(f"{OUT}/{OID}-t1v.mp3")

meta = {"order_id": OID, "status": "delivered", "handle": handle, "category": CATEGORY,
        "display_name": display_name or None,
        "duration_full": dur, "model": model_used,
        "heart_anchor": anchor, "heart_anchor_source": anchor_src,
        "sources": {"bio": bool(bio), "captions_used": len(captions),
                    "alts_seen": len(alt_texts), "alt_lines_used": len(alt_lines),
                    "details": bool(DETAILS), "anchor_source": anchor_src},
        "ingest_note": ingest_note}
json.dump(meta, open(f"{OUT}/{OID}.json", "w"), indent=1)
print("DELIVERED", OID, dur, "anchor=", (anchor or "(none)")[:60], "src=", anchor_src)

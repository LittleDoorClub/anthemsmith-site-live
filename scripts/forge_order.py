#!/usr/bin/env python3
"""AnthemSmith instant forge — the Harrier pipeline, one public IG profile -> 30s + full song.
Runs on GitHub Actions. Reads ORDER_ID/IG_URL/CATEGORY/MOOD/VOICE/DETAILS from env.
Writes songs/<order_id>.mp3 (30s hook) + songs/<order_id>-full.mp3 + songs/<order_id>.json.
Anti-hallucination: only text visible on the public profile (bio + recent captions) is used;
unknown facts stay null. No logged-in access anywhere."""
import json, os, re, sys, time, hashlib, subprocess, urllib.request

# ============================================================
# PAYMENT GATE (top of forge — before ANY scrape/generation).
# The relay poller forwards the order payload's paid field verbatim
# as client_payload; the workflow exposes it to us as PAID_STATUS.
# Accepted verified-payment markers are exactly the values app.js writes
# on the paid/claimed code paths. Anything else (missing, empty, or the
# pay-panel-open 'awaiting_payment') MUST NOT forge: a song that was never
# paid for must not be generated, committed, or shipped.
# ============================================================
PAID_ACCEPTED = ("paid_claimed", "paid_stripe_return", "paid_verified", "paid")
PAID_STATUS = (os.environ.get("PAID_STATUS") or "").strip().lower()

def reject_unpaid(reason):
    """Write the <OID>.failed.json signal and stop the run RED.
    NEVER writes songs/<OID>.json: that is the DELIVERED signal the
    customer page polls, and writing it for an unpaid order would fake
    'your song is ready' AND permanently block the retry via the relay
    poller's claim_order() delivered-check."""
    oid = os.environ.get("ORDER_ID", "UNKNOWN")
    out = "songs"
    os.makedirs(out, exist_ok=True)
    json.dump({"order_id": oid, "status": "failed", "reason": reason},
              open(f"{out}/{oid}.failed.json", "w"))
    print("PAYMENT-GATE REJECTED", oid, "paid_status=%r" % PAID_STATUS, flush=True)
    sys.exit(3)

if os.environ.get("AS_FORGE_OVERRIDE") == "1":
    print("PAYMENT-GATE overridden (manual forge)", flush=True)
elif PAID_STATUS not in PAID_ACCEPTED:
    reject_unpaid("no verified payment marker (paid_status=%r; accepted=%s)"
                  % (PAID_STATUS, ",".join(PAID_ACCEPTED)))

if os.environ.get("AS_PAYMENT_GATE_TEST") == "1":
    print("PAYMENT-GATE ACCEPTED", os.environ.get("ORDER_ID"), "paid_status=%r" % PAID_STATUS, flush=True)
    sys.exit(0)

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
    # NEVER write <OID>.json on failure: songs/<OID>.json is the DELIVERED signal.
    # app.js's order poll celebrated on r.ok alone, and the relay poller's
    # claim_order() skips any order whose <OID>.json exists -> a failed record
    # would both fake a "Your song is ready" and permanently block the retry.
    # Failure records now live beside the delivered signal, mirroring the
    # AS-ANCHOR-GATE <OID>.blocked.json pattern (never the polled path).
    json.dump({"order_id": OID, "status": "failed", "reason": msg},
              open(f"{OUT}/{OID}.failed.json", "w"))
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
render_status = None
render_chrome = None
ig_ingest = None  # file-intake orders never enter the IG branch; pick_anchor gate below must not NameError
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
    render_status = None
    render_chrome = None
    if ig_ingest is not None:
        # STAGE 1.5.a ANCHOR -- logged-out, no account, no vendor. A plain
        # urllib GET of this URL returns a JS shell with 0 captions and a
        # Follower/Following/Posts count line (measured 2026-09-16), which is
        # why the old path invented an anchor. The rendered DOM carries the
        # real captions (grid img alt) and the profile payload.
        # render_handle_detail ALSO reports WHY a render produced nothing
        # (no_renderer / login_wall / empty / bad_handle): those causes are not
        # interchangeable, and folding them into one string is what made an
        # empty live order read as "the customer's profile is private".
        if hasattr(ig_ingest, "render_handle_detail"):
            rd = ig_ingest.render_handle_detail(handle)
            html = rd.get("html") or ""
            render_status = rd.get("status")
            render_chrome = rd.get("chrome")
            print("RENDER handle=%s status=%s bytes=%s chrome=%s head=%r"
                  % (handle, render_status, rd.get("bytes"), render_chrome,
                     (rd.get("head") or "").replace("\n", " ")[:160]), flush=True)
        else:
            html = ig_ingest.render_handle(handle) or ""
    if not html:
        try:
            html = fetch(f"https://www.instagram.com/{handle}/").decode("utf-8", "ignore")
            ingest_note = "headless renderer unavailable: static shell read"
        except Exception:
            html = ""
            ingest_note = "profile unreachable (private/deleted/blocked)"
    if not html and not render_status:
        render_status = "unavailable"
    if ig_ingest is not None and html:
        try:
            info = ig_ingest.ingest(html, handle, render_status=render_status)
        except TypeError:  # an older ig_ingest revision: never crash the lane
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
           "details": DETAILS, "category": CATEGORY, "mood": MOOD, "note": ingest_note,
           "render_status": render_status, "render_chrome": render_chrome}

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
elif DETAILS:
    # file-intake orders: the customer's own brief words ARE verbatim source
    anchor, anchor_src = DETAILS.strip(), "brief"
else:
    anchor, anchor_src = None, None

# ---------- 4b. SHIP GATE -- no heart anchor, no ship (house law) ----------
# Measured on this lane 2026-09-17: order AS-ANCHORPROOF1 committed
# songs/AS-ANCHORPROOF1.json with heart_anchor=null and status="delivered".
# The F1 fence stopped this lane INVENTING a line; nothing stopped it SHIPPING
# without one, and app.js treats the EXISTENCE of songs/<id>.json as "Your song
# is ready" -- so a committed anchorless build is a broken product in front of a
# paying customer.
# The gate fires BEFORE the paid generation (a song that must not ship must not
# cost $0.10 either) and exits non-zero: the run goes RED, no song is committed,
# and the customer page keeps waiting instead of celebrating a missing mp3. It
# is re-attempted on the next tick and self-heals the moment the profile becomes
# readable. The blocked record is written to <OID>.blocked.json -- NEVER
# <OID>.json, which the customer page polls.
if not anchor:
    reason = ("no verbatim, source-backed line was readable -- refusing to "
              "invent the customer's chorus (house law: no heart anchor => no "
              "ship). render_status=" + str(render_status))
    blocked = {"order_id": OID, "status": "blocked_no_anchor", "reason": reason,
               "handle": handle, "category": CATEGORY, "mood": MOOD,
               "display_name": display_name or None,
               "duration_full": None, "model": None,
               "heart_anchor": None, "heart_anchor_source": None,
               "sources": {"bio": bool(bio), "captions_used": len(captions),
                           "alts_seen": len(alt_texts),
                           "alt_lines_used": len(alt_lines),
                           "details": bool(DETAILS), "anchor_source": None,
                           "render_status": render_status,
                           "render_chrome": render_chrome},
               "ingest_note": ingest_note}
    json.dump(blocked, open(f"{OUT}/{OID}.blocked.json", "w"), indent=1)
    print("BLOCKED", OID, "-", reason, flush=True)
    sys.exit(2)

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
                    "details": bool(DETAILS), "anchor_source": anchor_src,
                    "render_status": render_status,
                    "render_chrome": render_chrome},
        "ingest_note": ingest_note}
json.dump(meta, open(f"{OUT}/{OID}.json", "w"), indent=1)
print("DELIVERED", OID, dur, "anchor=", (anchor or "(none)")[:60], "src=", anchor_src)

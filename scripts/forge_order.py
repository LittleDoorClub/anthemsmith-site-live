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
REFERENCE_URL = (os.environ.get("REFERENCE_URL") or "").strip()
PHOTO_URLS = (os.environ.get("PHOTO_URLS") or "").strip()
OPENAI_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
SUPABASE_SR_KEY = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
SUPABASE_URL = "https://veqpmdsiqcjjpxgcubrp.supabase.co"

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

# ---------- 0. PHOTO INTAKE (download from ntfy, upload to Supabase, vision) ----------
photo_descriptions = []
photo_ocr_texts = []
photo_storage_paths = []
vision_raw = None

if PHOTO_URLS:
    photo_url_list = [u.strip() for u in PHOTO_URLS.split(",") if u.strip()]
    print(f"PHOTO INTAKE: {len(photo_url_list)} photo(s) from ntfy attachments", flush=True)
    
    for i, url in enumerate(photo_url_list):
        # Download from ntfy
        try:
            photo_bytes = fetch(url, timeout=30)
            print(f"  Photo {i+1}: downloaded {len(photo_bytes)} bytes", flush=True)
        except Exception as e:
            print(f"  Photo {i+1}: download failed: {e}", flush=True)
            continue
        
        # Upload to Supabase Storage
        storage_path = f"anthemsmith-orders/{OID}/photo_{i+1:02d}.jpg"
        try:
            storage_url = f"{SUPABASE_URL}/storage/v1/object/{storage_path}"
            req = urllib.request.Request(storage_url, data=photo_bytes, method="POST")
            req.add_header("Authorization", f"Bearer {SUPABASE_SR_KEY}")
            req.add_header("Content-Type", "image/jpeg")
            with urllib.request.urlopen(req, timeout=30) as r:
                upload_result = json.loads(r.read())
            photo_storage_paths.append(storage_path)
            print(f"  Photo {i+1}: stored -> {storage_path}", flush=True)
        except Exception as e:
            print(f"  Photo {i+1}: storage failed: {e}", flush=True)
            photo_storage_paths.append(None)
        
        # Vision analysis via OpenAI GPT-4o
        if OPENAI_KEY and photo_bytes:
            try:
                import base64 as b64
                img_b64 = b64.b64encode(photo_bytes).decode()
                vision_prompt = (
                    "Describe this photo in detail for a songwriter. What do you see? "
                    "People, places, objects, actions, mood, lighting, colors. "
                    "Include any visible text or signs you can read. "
                    "Be specific and grounded — no invented facts. "
                    "If you can't determine something, say so.\n\n"
                    "Respond in JSON:\n"
                    '{"description": "paragraph describing the scene", '
                    '"people": "who appears to be there (age, expression, activity)", '
                    '"setting": "where this is", '
                    '"mood": "emotional tone", '
                    '"colors": "dominant colors and lighting", '
                    '"visible_text": "any text you can read in the image", '
                    '"objects": "notable objects"}'
                )
                vision_req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=json.dumps({
                        "model": "gpt-4o",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": vision_prompt},
                                {"type": "image_url", "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_b64}",
                                    "detail": "high"
                                }}
                            ]
                        }],
                        "max_tokens": 500,
                        "temperature": 0.3
                    }).encode(),
                    method="POST"
                )
                vision_req.add_header("Authorization", f"Bearer {OPENAI_KEY}")
                vision_req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(vision_req, timeout=60) as r:
                    vision_resp = json.loads(r.read())
                content = vision_resp["choices"][0]["message"]["content"]
                # Parse JSON from response
                try:
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0]
                    parsed = json.loads(content)
                except:
                    parsed = {"description": content, "people": "", "setting": "", 
                              "mood": "", "colors": "", "visible_text": "", "objects": ""}
                
                photo_descriptions.append(parsed.get("description", ""))
                photo_ocr_texts.append(parsed.get("visible_text", ""))
                if i == 0:
                    vision_raw = parsed
                print(f"  Photo {i+1}: vision done — {parsed.get('description','')[:80]}...", flush=True)
            except Exception as e:
                print(f"  Photo {i+1}: vision failed: {e}", flush=True)
                photo_descriptions.append("")
                photo_ocr_texts.append("")
    
    print(f"PHOTO INTAKE: {len(photo_descriptions)} analyzed, {len(photo_storage_paths)} stored", flush=True)

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
        # STAGE 1.5.a ANCHOR — try instaloader (mobile API) first.
        # The mobile API doesn't login-wall; headless Chrome does on cloud IPs.
        info_il = None
        if hasattr(ig_ingest, "scrape_instaloader"):
            import concurrent.futures
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    info_il = ex.submit(ig_ingest.scrape_instaloader, handle).result(timeout=60)
            except (concurrent.futures.TimeoutError, Exception):
                info_il = None
                print("instaloader timeout or error — falling through to headless Chrome", flush=True)
        if info_il:
            info = info_il
            render_status = info_il.get("render_status") or "posts"
            html = "<instaloader>"  # marker: no DOM needed downstream
            # extract fields directly (mobile API returns structured data, no ingest needed)
            bio = info_il.get("bio") or ""
            captions = info_il.get("captions") or []
            alt_texts = []   # mobile API returns no alt-text
            alt_lines = info_il.get("alt_lines") or []
            display_name = info_il.get("display_name") or ""
            print("RENDER handle=%s method=instaloader status=%s captions=%d" % (handle, render_status, len(captions)), flush=True)
        else:
            # Fall back to headless Chrome — the original path.
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
    if ig_ingest is not None and html and not html.startswith("<instaloader"):
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
           "render_status": render_status, "render_chrome": render_chrome,
           "photo_count": len(photo_descriptions),
           "photo_descriptions": photo_descriptions,
           "photo_ocr": photo_ocr_texts,
           "photo_storage": [p for p in photo_storage_paths if p],
           "vision_raw": vision_raw}

# ---------- 2. VIBE READ (only what is visible; unknown stays null) ----------
name = display_name or handle or ""
lines = []
if bio: lines.append(bio)
lines += captions
lines += alt_lines
if DETAILS: lines.append(DETAILS)
# Photo intake: vision descriptions become the primary corpus
if photo_descriptions:
    lines += photo_descriptions
if photo_ocr_texts:
    lines += [t for t in photo_ocr_texts if t]
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

# Reference resolution: the old path appended a raw URL to the style prompt as if
# the model could listen to it. MuAPI custom_mode=true does NOT support audio_urls,
# so the URL string was meaningless noise. This replacement resolves YouTube/Spotify
# links via their free oEmbed/OG endpoints and extracts measurable metadata (title,
# artist) for use as musical guidance. If resolution fails, the reference is recorded
# unresolved in metadata — the model is never told it heard a URL it didn't hear.
# ---- Measured reference analysis: genre inference + musical parameters ----
# oEmbed gives title/artist (metadata). For measured musical guidance we
# need BPM ranges, instrumentation hints, and production style — not just
# a name. This lookup table infers genre from artist (exact match) and
# falls back to the AnthemSmith category mapping.

_GENRE_ARTISTS = {  # lowercase artist -> genre key
    "slayer": "thrash_metal", "metallica": "thrash_metal", "megadeth": "thrash_metal",
    "nirvana": "grunge", "pearl jam": "grunge", "soundgarden": "grunge",
    "drake": "trap_rnb", "kendrick lamar": "conscious_rap", "j. cole": "conscious_rap",
    "taylor swift": "pop_anthem", "billie eilish": "dark_pop", "olivia rodrigo": "pop_punk",
    "the weeknd": "synth_rnb", "sza": "alt_rnb",
    "daft punk": "french_house", "calvin harris": "edm", "avicii": "prog_house",
    "adele": "piano_ballad", "ed sheeran": "acoustic_pop", "bruno mars": "funk_pop",
    "beyonce": "rnb_anthem", "rihanna": "pop_rnb", "lady gaga": "electropop",
    "travis scott": "trap", "playboi carti": "rage_rap",
    "radiohead": "art_rock", "tame impala": "psych_pop",
    "tyler the creator": "alt_hiphop", "frank ocean": "alt_rnb", "childish gambino": "funk_rap",
    "bad bunny": "reggaeton", "bts": "kpop",
    "kanye west": "chipmunk_soul", "jay-z": "east_coast",
    "charli xcx": "hyperpop", "100 gecs": "hyperpop",
    "bon iver": "indie_folk", "fleetwood mac": "classic_rock", "queen": "arena_rock",
    "green day": "pop_punk", "blink-182": "pop_punk",
    "lil wayne": "southern_rap", "future": "trap",
    "ariana grande": "diva_pop", "dua lipa": "nu_disco", "doja cat": "pop_rap_fem",
    "bob marley": "reggae", "the beatles": "brit_invasion", "pink floyd": "prog_rock",
}

_GENRE_PROFILES = {
    "thrash_metal": {"name":"thrash metal","bpm_hint":"160-200 BPM aggressive double-kick",
        "instruments":"distorted guitars, fast palm-muted riffs, screaming vocals",
        "style_hint":"aggressive, dark, relentless energy"},
    "grunge": {"name":"grunge","bpm_hint":"100-130 BPM sludgy mid-tempo",
        "instruments":"fuzzy guitars, heavy bass, raw-strained vocals",
        "style_hint":"angsty, raw, quiet-loud dynamics"},
    "trap_rnb": {"name":"trap R&B","bpm_hint":"120-140 BPM half-time hi-hats",
        "instruments":"808 bass, atmospheric pads, autotuned vocals",
        "style_hint":"moody, nocturnal, minimal"},
    "conscious_rap": {"name":"conscious rap","bpm_hint":"80-95 BPM boom-bap swing",
        "instruments":"soul samples, warm bass, crisp snares",
        "style_hint":"reflective, storytelling, jazz-influenced"},
    "dark_pop": {"name":"dark pop","bpm_hint":"60-80 BPM sparse minimal",
        "instruments":"sub bass, whispered vocals, ASMR textures",
        "style_hint":"intimate, eerie, bass-driven"},
    "synth_rnb": {"name":"synth R&B","bpm_hint":"90-110 BPM retro drum machines",
        "instruments":"analog synths, reverb-drenched falsetto",
        "style_hint":"cinematic, nocturnal, 80s-influenced"},
    "pop_anthem": {"name":"pop anthem","bpm_hint":"120-130 BPM four-on-floor",
        "instruments":"sparkling synths, layered vocals, big drums",
        "style_hint":"euphoric, stadium-sized, bright"},
    "french_house": {"name":"French house","bpm_hint":"120-128 BPM filter sweeps",
        "instruments":"disco samples, sidechain compression, vocoder",
        "style_hint":"groovy, filtered, dancefloor"},
    "prog_house": {"name":"progressive house","bpm_hint":"125-130 BPM builds",
        "instruments":"saw synths, piano leads, big drops",
        "style_hint":"euphoric, anthemic, festival"},
    "pop_punk": {"name":"pop punk","bpm_hint":"160-200 BPM double-time",
        "instruments":"power chords, fast drums, nasal vocals",
        "style_hint":"energetic, youthful, catchy"},
    "reggaeton": {"name":"reggaeton","bpm_hint":"90-100 BPM dembow rhythm",
        "instruments":"dembow beat, synth brass, Spanish vocals",
        "style_hint":"rhythmic, danceable, Caribbean"},
    "kpop": {"name":"K-pop","bpm_hint":"100-130 BPM genre-blending",
        "instruments":"layered synths, rap verses, polished vocals",
        "style_hint":"high-energy, maximalist, choreographed"},
    "hyperpop": {"name":"hyperpop","bpm_hint":"140-180 BPM glitchy",
        "instruments":"pitched vocals, distorted bass, chipmunk edits",
        "style_hint":"maximalist, digital, chaotic"},
    "alt_rnb": {"name":"alternative R&B","bpm_hint":"70-90 BPM slow-burn",
        "instruments":"ambient textures, falsetto, minimal drums",
        "style_hint":"atmospheric, introspective, experimental"},
    "trap": {"name":"trap","bpm_hint":"130-150 BPM triplets",
        "instruments":"808s, rapid hi-hats, ad-libs",
        "style_hint":"hard-hitting, dark, rhythmic"},
    "indie_folk": {"name":"indie folk","bpm_hint":"70-90 BPM fingerpicked",
        "instruments":"acoustic guitar, falsetto, brass swells",
        "style_hint":"warm, organic, nostalgic"},
    "funk_pop": {"name":"funk pop","bpm_hint":"105-115 BPM tight groove",
        "instruments":"slap bass, horn section, rhythmic guitar",
        "style_hint":"groovy, upbeat, danceable"},
    "arena_rock": {"name":"arena rock","bpm_hint":"120-140 BPM stomp-clap",
        "instruments":"anthemic guitars, crowd vocals, big drums",
        "style_hint":"stadium-sized, singalong, triumphant"},
    "nu_disco": {"name":"nu-disco","bpm_hint":"110-120 BPM four-on-floor",
        "instruments":"funky bass, string stabs, falsetto",
        "style_hint":"glamorous, danceable, retro-futuristic"},
}

_CATEGORY_GENRE = {"My Story":"conscious_rap","Funny":"pop_anthem","Couples":"synth_rnb","Friendship":"pop_anthem"}

def _infer_genre(artist, track, category):
    """Infer measurable musical parameters from artist name or category fallback.
    Returns dict with name, bpm_hint, instruments, style_hint — or None."""
    if artist:
        key = artist.lower().strip()
        if key in _GENRE_ARTISTS:
            return _GENRE_PROFILES[_GENRE_ARTISTS[key]]
        for ak, gk in _GENRE_ARTISTS.items():
            if key in ak or ak in key:
                return _GENRE_PROFILES[gk]
    cat_genre = _CATEGORY_GENRE.get(category)
    if cat_genre and cat_genre in _GENRE_PROFILES:
        return _GENRE_PROFILES[cat_genre]
    return None


reference_title = None
reference_artist = None
reference_resolved = False
if REFERENCE_URL:
    ref_meta = {"url": REFERENCE_URL}
    try:
        ref_lower = REFERENCE_URL.lower()
        # YouTube oEmbed — free, no API key, returns title in JSON
        if "youtube.com/watch" in ref_lower or "youtu.be/" in ref_lower:
            import re as _re
            vid = None
            for pat in (_re.compile(r'[?&]v=([^&]+)'), _re.compile(r'youtu\.be/([^?&]+)')):
                m = pat.search(REFERENCE_URL)
                if m:
                    vid = m.group(1); break
            if vid:
                oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
                try:
                    oembed = json.loads(fetch(oembed_url, timeout=10).decode())
                    ref_meta["title"] = oembed.get("title")
                    ref_meta["provider"] = "youtube"
                    # YouTube titles are often "Artist — Song Title"; split if possible
                    full_title = oembed.get("title", "")
                    if " — " in full_title:
                        parts = full_title.split(" — ", 1)
                        ref_meta["artist"] = parts[0].strip()
                        ref_meta["track"] = parts[1].strip()
                    elif " - " in full_title:
                        parts = full_title.split(" - ", 1)
                        ref_meta["artist"] = parts[0].strip()
                        ref_meta["track"] = parts[1].strip()
                    else:
                        ref_meta["artist"] = None
                        ref_meta["track"] = full_title
                except Exception:
                    ref_meta["oembed_error"] = "unreachable"

        # Spotify oEmbed — free, no API key
        elif "open.spotify.com/track" in ref_lower or "spotify.link" in ref_lower:
            try:
                oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(REFERENCE_URL, safe='')}"
                oembed = json.loads(fetch(oembed_url, timeout=10).decode())
                ref_meta["title"] = oembed.get("title")
                ref_meta["provider"] = "spotify"
                ref_meta["artist"] = None
                ref_meta["track"] = oembed.get("title")
            except Exception:
                ref_meta["oembed_error"] = "unreachable"
        else:
            ref_meta["resolution"] = "domain not supported — no oEmbed endpoint known"

        # Build style guidance from resolved metadata
        if ref_meta.get("title"):
            ref_desc = ref_meta.get("track") or ref_meta["title"]
            if ref_meta.get("artist"):
                ref_desc = f"{ref_meta['artist']} — {ref_desc}"
            style += f", in the spirit of: {ref_desc}"
            reference_title = ref_meta.get("title")
            reference_artist = ref_meta.get("artist")
            reference_resolved = True
            # Measured analysis: genre inference from artist → BPM, instruments
            ref_genre = _infer_genre(reference_artist, ref_meta.get("track", ""), CATEGORY)
            if ref_genre:
                style += f", {ref_genre['bpm_hint']}, {ref_genre['instruments']}, {ref_genre['style_hint']}"
                ref_meta["genre"] = ref_genre["name"]
                ref_meta["bpm_range"] = ref_genre["bpm_hint"]
                ref_meta["style_analysis"] = ref_genre["style_hint"]
        else:
            ref_meta["resolution"] = ref_meta.get("resolution") or "could not resolve metadata"
            # Don't append garbage to style — just flag it
    except Exception as ex:
        ref_meta["resolution_error"] = str(ex)[:80]
    ref_meta["resolved"] = reference_resolved
    # Note: ref_meta is not used directly below but its values feed reference_title/artist/resolved
else:
    ref_meta = None

if "hip" in corpus or "gym" in corpus: style = band["My Story"]

# ---------- 4. LYRICS (heart anchor = most specific VISIBLE line; anti-cliche) ----------
# F1 fence: no verbatim source -> NO anchor. Never synthesise a fallback string
# (the old code shipped f"{name} is one of the real ones" and follower-count
# lines as the customer's chorus -- an invented line with zero sources).
if photo_descriptions:
    # Photo intake: DETAILS text is the customer's own words = verbatim source
    if DETAILS:
        anchor, anchor_src = DETAILS.strip(), "brief"
    elif photo_descriptions:
        # Use the first line of the vision description as a fallback anchor
        anchor = photo_descriptions[0].split(".")[0].strip()[:120] if photo_descriptions[0] else None
        anchor_src = "vision" if anchor else None
    else:
        anchor, anchor_src = None, None
elif ig_ingest is not None:
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

meta = {"order_id": OID, "status": "audio_ready", "handle": handle, "category": CATEGORY,
        "display_name": display_name or None,
        "duration_full": dur, "model": model_used,
        "heart_anchor": anchor, "heart_anchor_source": anchor_src,
        "reference": {"url": REFERENCE_URL or None, 
                      "resolved": reference_resolved,
                      "title": reference_title, 
                      "artist": reference_artist} if REFERENCE_URL else None,
        "sources": {"bio": bool(bio), "captions_used": len(captions),
                    "alts_seen": len(alt_texts), "alt_lines_used": len(alt_lines),
                    "details": bool(DETAILS), "anchor_source": anchor_src,
                    "render_status": render_status,
                    "render_chrome": render_chrome},
        "ingest_note": ingest_note}
json.dump(meta, open(f"{OUT}/{OID}.json", "w"), indent=1)
print("DELIVERED", OID, dur, "anchor=", (anchor or "(none)")[:60], "src=", anchor_src)

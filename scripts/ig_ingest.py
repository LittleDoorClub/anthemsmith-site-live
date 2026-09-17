"""Honest public-Instagram ingest for the AnthemSmith instant forge (fence F1/F4).

WHY THIS EXISTS -- measured live 2026-09-16, not theorised
---------------------------------------------------------
A plain ``urllib`` GET of ``instagram.com/<handle>/`` returns a ~700 KB
JavaScript SHELL: 0 ``alt=`` attributes, 0 caption strings, and one
``og:description`` that is only a COUNT LINE, e.g.

    984 Followers, 1,010 Following, 34 Posts - See Instagram photos and
    videos from David Johnson (#olafjohnsoniv)

so ``scripts/forge_order.py`` got ``captions == []`` on every real order and
fell through to an INVENTED anchor -- either the follower-count string or the
literal ``f"{name} is one of the real ones"``. A shipped artifact proves it:
``songs/AS-MU4NULIY.json`` carried
``heart_anchor: "cozyafllc is one of the real ones"`` with
``sources: {bio: false, captions_used: 0}``.

An invented line with no source is worse than no line, so this module:

1. renders the LOGGED-OUT profile with headless Chrome
   (``--headless=new --dump-dom --virtual-time-budget=N``), which executes the
   client-side payload; the rendered DOM carries the profile object
   (full_name / biography / follower counts) and every grid image's ``alt``
   attribute -- which contains the REAL caption text verbatim;
2. never invents: a value that is not in the pixels/DOM stays ``None``.

No logged-in access, no account, no vendor. Works on PUBLIC handles only; a
private handle simply yields an empty read (and the caller refuses honestly).

Pure parsing (``ingest`` / ``pick_anchor``) is offline-testable; only
``render`` / ``render_handle`` touch the network, and only through the browser.
"""
import json
import os
import re
import shutil
import subprocess
import sys

BUDGET_MS = 25000
UA = ("Mozilla/5.0 (compatible; AnthemSmith/1.0; +https://anthemsmith.com)")

_SCRIPT = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)
_META = re.compile(r"<meta\b[^>]*>", re.I)
_IMG_ALT = re.compile(r"""<img\b[^>]*\balt=(?:"([^"]*)"|'([^']*)')""", re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_COUNT = re.compile(r"([\d,]+)\s+(Followers|Following|Posts)", re.I)
_COUNT_LINE = re.compile(r"^\s*[\d,]+\s+Followers\b.*?\bSee Instagram", re.I | re.S)
# IG writes wrapper alts ("Photo by <who> in <where> on <date>.") and Meta writes
# pixel-derived accessibility captions ("May be an image of ..."). Neither is the
# person's own words, so neither may become a heart anchor.
_WRAP = re.compile(r"^(Photo|Video) by\b", re.I)
_METADESC = re.compile(r"May be (an image|a close-up|a graphic|text|a screenshot|a picture)", re.I)


# ------------------------------------------------------------------ browser
def chrome_bin():
    """Locate a Chrome/Chromium binary. Honors CHROME_BIN first."""
    env = os.environ.get("CHROME_BIN")
    if env and os.path.exists(env):
        return env
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              "/usr/bin/google-chrome", "/usr/bin/chromium",
              "/usr/bin/chromium-browser"):
        if os.path.exists(p):
            return p
    return None


def render(url, budget_ms=BUDGET_MS, timeout=180):
    """Render a URL logged-out and return the executed DOM, or "" on any failure.

    Never raises: a missing renderer, a blocked profile or a timeout all
    degrade to an empty string, which the caller treats as 'nothing readable',
    NEVER as 'invent something'.
    """
    bin_ = chrome_bin()
    if not bin_:
        return ""
    import tempfile
    prof = tempfile.mkdtemp(prefix="asig-")
    args = [bin_, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--disable-dev-shm-usage", "--hide-scrollbars",
            "--user-agent=" + UA, f"--user-data-dir={prof}",
            f"--virtual-time-budget={budget_ms}", "--dump-dom", url]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:
        return ""
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def render_handle(handle, budget_ms=BUDGET_MS):
    handle = (handle or "").lstrip("@").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.]{2,}", handle or ""):
        return ""
    return render(f"https://www.instagram.com/{handle}/", budget_ms=budget_ms)


# ------------------------------------------------------------- pure parsing
def _attrs(tag):
    return {m.group(1).lower(): m.group(2) for m in _ATTR.finditer(tag)}


def meta_map(html):
    out = {}
    for tag in _META.findall(html or ""):
        a = _attrs(tag)
        k = a.get("property") or a.get("name")
        if k and a.get("content"):
            out.setdefault(k.lower(), a["content"])
    return out


def img_alts(html):
    out = []
    for m in _IMG_ALT.finditer(html or ""):
        t = (m.group(1) if m.group(1) is not None else m.group(2) or "").strip()
        if t and len(t) >= 12 and "profile picture" not in t.lower():
            out.append(t)
    return out


def json_blobs(html):
    out = []
    for m in _SCRIPT.findall(html or ""):
        s = m.strip()
        if not s.startswith(("{", "[")):
            continue
        try:
            out.append(json.loads(s))
        except Exception:
            continue
    return out


def _walk(obj, pred, out, depth=0):
    if depth > 40:
        return
    if isinstance(obj, dict):
        if pred(obj):
            out.append(obj)
        for v in obj.values():
            _walk(v, pred, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, pred, out, depth + 1)


def _is_user(d):
    return (isinstance(d.get("username"), str)
            and ("full_name" in d or "biography" in d)
            and ("follower_count" in d or "is_private" in d))


def _is_edges(d):
    e = d.get("edges")
    return (isinstance(e, list) and bool(e) and isinstance(e[0], dict)
            and isinstance(e[0].get("node"), dict) and "code" in e[0]["node"])


def _num(v):
    try:
        return int(str(v).replace(",", "").strip())
    except Exception:
        return None


def _text(v):
    """Caption/alt arrive as str, {"text": ...}, or a list of those."""
    if v is None:
        return None
    if isinstance(v, str):
        return v or None
    if isinstance(v, dict):
        for k in ("text", "value", "caption"):
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k]
        return None
    if isinstance(v, list):
        parts = [p for p in (_text(x) for x in v) if p]
        return "\n".join(parts) or None
    return None


def is_count_line(s):
    """True for the og:description Follower/Following/Posts summary -- evidence
    about the ACCOUNT, never a caption or a lyric line."""
    return bool(s) and bool(_COUNT_LINE.match(s))


def line_ok(s):
    """A line that can carry a chorus: 2+ words, 8+ letters, not a count line,
    not a 'Photo/Video by <name> in <place>' wrapper, not a Meta pixel caption
    ('May be an image of ...'), not a hashtag-only tag dump."""
    if not s:
        return False
    t = s.strip()
    if is_count_line(t) or _WRAP.match(t) or _METADESC.search(t):
        return False
    toks = t.split()
    if toks and sum(1 for w in toks if w.startswith("#")) * 2 >= len(toks):
        return False
    words = re.findall(r"[A-Za-z']+", t)
    letters = sum(len(w) for w in words)
    return len(words) >= 2 and letters >= 8


def posts_from_html(html):
    edges = []
    for blob in json_blobs(html):
        _walk(blob, _is_edges, edges)
    posts, seen = [], set()
    for container in edges:
        for e in container["edges"]:
            n = e.get("node") or {}
            code = n.get("code")
            if not code or code in seen:
                continue
            seen.add(code)
            posts.append({
                "code": code,
                "caption": _text(n.get("caption")),
                "accessibility_caption": _text(n.get("accessibility_caption")),
            })
    return posts


def ingest(html, handle):
    """Rendered DOM -> what is actually readable. Unknown stays null/empty."""
    handle = (handle or "").lstrip("@").strip()
    meta = meta_map(html)
    og_desc = meta.get("og:description") or ""
    og_title = meta.get("og:title") or ""
    users = []
    for blob in json_blobs(html):
        _walk(blob, _is_user, users)
    u = users[0] if users else {}
    posts = posts_from_html(html)
    alts = img_alts(html)

    notes = []
    captions = []
    for p in posts:
        v = p.get("caption")
        if v and v.strip() and v.strip() not in captions:
            captions.append(v.strip())
    accessibility = []
    for p in posts:
        v = p.get("accessibility_caption")
        if v and v.strip() and v.strip() not in accessibility:
            accessibility.append(v.strip())
    alt_lines = [a for a in alts if line_ok(a)]

    payload_bio = (u.get("biography") or "").strip()
    if payload_bio:
        bio = payload_bio
    elif og_desc and not is_count_line(og_desc):
        bio = og_desc.strip()
    else:
        bio = ""
        if og_desc:
            notes.append("og:description is only a Follower/Following/Posts count "
                         "line -- not a bio; ignored (never used as a caption)")

    display = (u.get("full_name") or "").strip()
    if not display and og_title:
        m = re.match(r"^(?P<name>.*?)\s*\(@", og_title)
        if m:
            display = m.group("name").strip()

    if not posts and not alts:
        notes.append("no rendered posts or alts (private handle, empty profile, "
                     "or a shell that never executed) -- nothing to anchor beyond "
                     "the profile meta; do NOT invent a line")

    return {
        "handle": (u.get("username") or handle),
        "display_name": display or None,
        "bio": bio or None,
        "bio_is_count_line": is_count_line(bio),
        "private": (bool(u.get("is_private")) if u else None),
        "counts": {k.lower(): _num(v) for v, k in _COUNT.findall(og_desc)},
        "captions": captions,
        "accessibility_captions": accessibility,
        "alt_texts": alts,
        "alt_lines": alt_lines,
        "posts_seen": len(posts),
        "notes": notes,
    }


def pick_anchor(captions, alt_lines, details, handle=None):
    """Choose the heart anchor from REAL evidence only, most-specific first.

    Order: a verbatim caption -> a grid alt that is a real line (not the
    'Photo by <name> in <place>' wrapper) -> the customer's own brief words.
    Returns (anchor, source_ref) and (None, None) when nothing qualifies.
    NEVER synthesises a fallback string -- that is the F1 defect.
    """
    for c in captions or []:
        if line_ok(c):
            return c.strip(), "ig-caption"
    for a in alt_lines or []:
        if line_ok(a):
            return a.strip(), "ig-alt"
    for part in re.split(r"[\n.]+", details or ""):
        if line_ok(part):
            return part.strip(), "brief"
    return None, None


# ------------------------------------------------------------------ selftest
_FIXTURE = """<!doctype html><html><head>
<meta property="og:title" content="David Johnson (@olafjohnsoniv) &#x2022; Instagram photos and videos">
<meta property="og:description" content="984 Followers, 1,010 Following, 34 Posts - See Instagram photos and videos from David Johnson (#olafjohnsoniv)">
</head><body>
<img alt="Photo by David Johnson in Tampa, Florida.">
<img alt="Rhea is a big fan of gator nuggets #friends">
<img alt="profile picture">
<script type="application/json">{"data":{"user":{"username":"olafjohnsoniv","full_name":"David Johnson","biography":"Tampa Bay Raised","follower_count":984,"is_private":false,
"edge_owner_to_timeline_media":{"edges":[{"node":{"code":"AAA1","caption":{"text":"I'm just gonna fuckin do it"},"accessibility_caption":"a man smiling"}}]}}}}</script>
</body></html>"""


def _selftest():
    ok = 0
    bad = []
    info = ingest(_FIXTURE, "olafjohnsoniv")
    checks = [
        ("display name read", info["display_name"] == "David Johnson"),
        ("payload bio read", info["bio"] == "Tampa Bay Raised"),
        ("count line NOT used as bio", not info["bio_is_count_line"]),
        ("counts parsed", info["counts"].get("followers") == 984),
        ("caption from payload", "I'm just gonna fuckin do it" in info["captions"]),
        ("accessibility caption is NOT a caption", "a man smiling" not in info["captions"]),
        ("alt captions read", any("gator nuggets" in a for a in info["alt_texts"])),
        ("photo-by wrapper rejected as anchor line",
         not line_ok("Photo by David Johnson in Tampa, Florida.")),
        ("Meta pixel caption rejected as anchor line",
         not line_ok("Video by David Johnson on August 25, 2026. May be an image of bull terrier.")),
        ("hashtag dump rejected as anchor line",
         not line_ok("#renfaire #renfest #findfun #tampa #cowboy")),
        ("alt_lines keeps the real caption",
         any("gator nuggets" in a for a in info["alt_lines"])),
        ("profile-picture alt dropped", all("profile picture" not in a.lower()
                                            for a in info["alt_texts"])),
        ("no post codes duplicated", info["posts_seen"] == 1),
    ]
    for name, cond in checks:
        if cond:
            ok += 1
        else:
            bad.append(name)

    # anchor rules
    a, s = pick_anchor(info["captions"], info["alt_lines"], "")
    if a == "I'm just gonna fuckin do it" and s == "ig-caption":
        ok += 1
    else:
        bad.append("anchor picks the verbatim caption first")

    a2, s2 = pick_anchor([], ["Photo by David Johnson in Tampa, Florida."], "My best friend of 20 years")
    if a2 == "My best friend of 20 years" and s2 == "brief":
        ok += 1
    else:
        bad.append("bare 'Photo by' alt rejected; brief used instead")

    a3, s3 = pick_anchor([], [], "")
    if a3 is None and s3 is None:
        ok += 1
    else:
        bad.append("no evidence -> (None, None), never an invented line")

    # F1 negative control: the old invented string must not be producible
    if "is one of the real ones" not in json.dumps([a, a2, a3, info]):
        ok += 1
    else:
        bad.append("F1 invented anchor string present")

    # count-line detector
    if is_count_line("984 Followers, 1,010 Following, 34 Posts - See Instagram "
                     "photos and videos from David Johnson (#olafjohnsoniv)"):
        ok += 1
    else:
        bad.append("count-line detector")
    if not is_count_line("Rhea is a big fan of gator nuggets #friends"):
        ok += 1
    else:
        bad.append("count-line detector false positive")

    # empty DOM -> empty read, no crash, no invention
    e = ingest("", "ghost")
    if e["captions"] == [] and e["bio"] is None and e["display_name"] is None:
        ok += 1
    else:
        bad.append("empty DOM -> empty read")

    total = len(checks) + 7
    print(f"ig_ingest selftest: {ok}/{total} passed"
          + ("" if not bad else "  FAILED: " + "; ".join(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if len(sys.argv) > 1:
        h = sys.argv[1].lstrip("@")
        html = render_handle(h)
        print(f"rendered {len(html)} bytes for @{h}")
        print(json.dumps(ingest(html, h), indent=1)[:3000])

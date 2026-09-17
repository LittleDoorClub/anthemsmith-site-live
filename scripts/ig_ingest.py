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
# A datacenter IP is a different failure from a missing binary and from an
# empty profile. Instagram serves a login interstitial to most cloud IPs, and
# its DOM carries one of these markers instead of the profile grid.
_LOGIN_WALL = re.compile(
    r"(Log in to Instagram|loginForm|accounts/login|You must log in|"
    r"Sorry, this page isn't available|Restricted profile|"
    r"Sign up to see|page isn't available)", re.I)

# render_detail() status vocabulary (see render_detail docstring).
RENDER_STATUSES = ("no_renderer", "empty", "login_wall", "posts", "html_no_posts")


# ------------------------------------------------------------------ browser
def chrome_bin():
    """Locate a Chrome/Chromium binary. Honors CHROME_BIN first.

    Env vars win (CHROME_BIN / GOOGLE_CHROME_BIN / CHROME_PATH), then PATH,
    then the well-known install locations -- including /opt/google/chrome/chrome,
    which is where the ubuntu-24.04 GitHub runner image installs the Google
    Chrome it ships. (Measured 2026-09-17: that image includes "Google Chrome
    152" in its manifest, so "the runner has no browser" was never a supported
    reading of an empty render -- see render_detail.)
    """
    for var in ("CHROME_BIN", "GOOGLE_CHROME_BIN", "CHROME_PATH"):
        env = os.environ.get(var)
        if env and os.path.exists(env):
            return env
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome", "chrome-headless-shell"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
              "/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/opt/google/chrome/chrome"):
        if os.path.exists(p):
            return p
    return None


def render_detail(url, budget_ms=BUDGET_MS, timeout=180):
    """Render a URL logged-out and return the CAUSE, not just an empty string.

    Why this exists (measured on the live lane, 2026-09-17). The forge job on
    GitHub Actions committed ``songs/AS-ANCHORPROOF1.json`` with
    ``heart_anchor: null`` and the note "no rendered posts or alts (private
    handle, empty profile, or a shell that never executed)". Those are three
    different defects -- a missing binary, a cloud-IP login wall, and an empty
    profile -- and the artifact committed to none of them, so the next reader
    had to guess. (A hard-coded "the runner has no browser" diagnosis was the
    guess; the ubuntu-24.04 runner image ships Google Chrome 152, so it was
    unsupported.) A silent degrade that folds three causes into one string is a
    wrong-cause machine: name the cause in the artifact.

    Never raises. Returns::

        {"html": str,          # executed DOM, "" on any failure
         "status": str,        # no_renderer | empty | login_wall | posts | html_no_posts
         "chrome": str|None,   # resolved binary, for the log
         "bytes": int,
         "head": str}          # first 300 chars of the DOM -- evidence, never parsed
    """
    bin_ = chrome_bin()
    if not bin_:
        return {"html": "", "status": "no_renderer", "chrome": None,
                "bytes": 0, "head": ""}
    import tempfile
    prof = tempfile.mkdtemp(prefix="asig-")
    args = [bin_, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--disable-dev-shm-usage", "--hide-scrollbars",
            "--user-agent=" + UA, f"--user-data-dir={prof}",
            f"--virtual-time-budget={budget_ms}", "--dump-dom", url]
    html = ""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        html = r.stdout or ""
    except Exception:
        html = ""
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    return {"html": html, "status": classify(html), "chrome": bin_,
            "bytes": len(html), "head": html[:300]}


def render(url, budget_ms=BUDGET_MS, timeout=180):
    """Render a URL logged-out and return the executed DOM, or "" on any failure.

    Back-compatible wrapper over render_detail(); callers that need the cause
    should call render_detail() and report its "status".
    """
    return render_detail(url, budget_ms=budget_ms, timeout=timeout)["html"]


def has_user_payload(html):
    """True when the DOM carries the profile payload (username + name/bio +
    follower_count/is_private). A login interstitial carries none of it, so this
    is what separates "Instagram never showed us the profile" from "this is the
    profile and it has nothing in it" -- including a PRIVATE one, which still
    ships the payload with is_private=true."""
    for blob in json_blobs(html):
        out = []
        _walk(blob, _is_user, out)
        if out:
            return True
    return False


def classify(html):
    """What did the renderer ACTUALLY return? -> one of RENDER_STATUSES.

    Distinguishes the causes an empty read used to fold together:
      empty          -- the browser produced no DOM at all (start-up failure)
      login_wall     -- Instagram's logged-out interstitial (the usual cloud-IP
                        answer); the profile was never rendered
      posts          -- a profile grid was rendered (this one can be anchored)
      html_no_posts  -- the profile page rendered but has no readable posts
                        (private or empty); the payload is present
    """
    if not (html or "").strip():
        return "empty"
    if posts_from_html(html) or img_alts(html):
        return "posts"
    if not has_user_payload(html) and _LOGIN_WALL.search(html):
        return "login_wall"
    return "html_no_posts"


def render_handle(handle, budget_ms=BUDGET_MS):
    return render_handle_detail(handle, budget_ms=budget_ms)["html"]


def render_handle_detail(handle, budget_ms=BUDGET_MS):
    """render_detail() for a profile handle, with the handle validated first.

    An unvalidated handle is a wrong-subject machine (a 1-char handle is a real
    stranger's account), so it never reaches the network: a rejected handle
    reports ``bad_handle`` rather than an empty read.
    """
    handle = (handle or "").lstrip("@").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.]{2,}", handle or ""):
        return {"html": "", "status": "bad_handle", "chrome": None,
                "bytes": 0, "head": ""}
    return render_detail(f"https://www.instagram.com/{handle}/", budget_ms=budget_ms)


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


def ingest(html, handle, render_status=None):
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
        # Name the CAUSE. All four used to be one string, which made "our
        # runner is walled off" read as "the customer's profile is private".
        cause = render_status or classify(html)
        if cause == "no_renderer":
            notes.append("NO RENDERER on this host (chrome_bin() found no "
                         "Chrome/Chromium) -- the profile was never opened; "
                         "nothing to anchor; do NOT invent a line")
        elif cause == "login_wall":
            notes.append("Instagram served a LOGIN INTERSTITIAL to this host's "
                         "IP -- the profile was never rendered, so nothing was "
                         "read; do NOT invent a line")
        elif cause == "empty":
            notes.append("the renderer returned an EMPTY DOM (browser failed to "
                         "start, or the URL was refused) -- nothing was read; "
                         "do NOT invent a line")
        elif cause == "bad_handle":
            notes.append("the handle did not pass validation, so it was never "
                         "requested -- nothing was read; do NOT invent a line")
        else:
            notes.append("real DOM but no posts or alts were readable (private "
                         "handle or empty profile) -- nothing to anchor beyond "
                         "the profile meta; do NOT invent a line")
        if render_status:
            notes.append("render_status=" + str(render_status))

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
        "render_status": render_status,
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
    # Every check is APPENDED to `checks` and counted by the single loop at the
    # bottom. Counting before the appends once made this banner report
    # "13/33 passed" with an empty failure list -- a green-looking summary of
    # checks that were never executed.

    # anchor rules
    a, s = pick_anchor(info["captions"], info["alt_lines"], "")
    checks.append(("anchor picks the verbatim caption first",
                   a == "I'm just gonna fuckin do it" and s == "ig-caption"))

    a2, s2 = pick_anchor([], ["Photo by David Johnson in Tampa, Florida."], "My best friend of 20 years")
    checks.append(("bare 'Photo by' alt rejected; brief used instead",
                   a2 == "My best friend of 20 years" and s2 == "brief"))

    a3, s3 = pick_anchor([], [], "")
    checks.append(("no evidence -> (None, None), never an invented line",
                   a3 is None and s3 is None))

    # F1 negative control: the old invented string must not be producible
    checks.append(("F1 invented anchor string absent",
                   "is one of the real ones" not in json.dumps([a, a2, a3, info])))

    # count-line detector
    checks.append(("count-line detector",
                   is_count_line("984 Followers, 1,010 Following, 34 Posts - See Instagram "
                                 "photos and videos from David Johnson (#olafjohnsoniv)")))
    checks.append(("count-line detector false positive",
                   not is_count_line("Rhea is a big fan of gator nuggets #friends")))

    # empty DOM -> empty read, no crash, no invention
    e = ingest("", "ghost")
    checks.append(("empty DOM -> empty read",
                   e["captions"] == [] and e["bio"] is None and e["display_name"] is None))

    # ---- the renderer CAUSE fence (wrong-cause machine, fixed 2026-09-17) ----
    checks.append(("RENDER_STATUSES vocabulary",
                   RENDER_STATUSES == ("no_renderer", "empty", "login_wall",
                                       "posts", "html_no_posts")))
    checks.append(("classify: empty DOM -> 'empty'", classify("") == "empty"))
    checks.append(("classify: rendered grid -> 'posts'", classify(_FIXTURE) == "posts"))
    checks.append(("classify: login interstitial -> 'login_wall'",
                   classify('<html><body><div>Log in to Instagram</div>'
                            '<a href="/accounts/login/?next=/x/">go</a></body></html>')
                   == "login_wall"))
    checks.append(("classify: real DOM, no posts -> 'html_no_posts'",
                   classify("<html><body><h1>Hello there friend Hello there friend "
                            "Hello there friend</h1></body></html>") == "html_no_posts"))
    # A PRIVATE profile still ships the user payload, so it must NOT be labelled
    # a login wall (the label is the whole point of this fence).
    _PRIVATE = ('<html><body><a href="/accounts/login/">Log in</a>'
                '<script type="application/json">{"user":{"username":"shy","full_name":'
                '"Shy Person","biography":"","follower_count":3,"is_private":true,'
                '"edge_owner_to_timeline_media":{"edges":[]}}}</script></body></html>')
    checks.append(("classify: private profile (payload present) -> 'html_no_posts', "
                   "not 'login_wall'", classify(_PRIVATE) == "html_no_posts"))
    checks.append(("has_user_payload: interstitial carries no profile payload",
                   not has_user_payload('<html><body>Log in to Instagram</body></html>')
                   and has_user_payload(_FIXTURE)))

    # a missing binary must report a missing BINARY, not "saw nothing"
    _orig_bin = chrome_bin
    globals()["chrome_bin"] = lambda: None
    try:
        rd = render_detail("https://www.instagram.com/whoever/")
    finally:
        globals()["chrome_bin"] = _orig_bin
    checks.append(("no renderer -> status 'no_renderer' with chrome=None and html=''",
                   rd["status"] == "no_renderer" and rd["html"] == ""
                   and rd["chrome"] is None))

    # CHROME_BIN is honoured first, and a bogus one never wins
    os.environ["CHROME_BIN"] = sys.executable
    try:
        checks.append(("CHROME_BIN honoured by chrome_bin()",
                       chrome_bin() == sys.executable))
    finally:
        os.environ.pop("CHROME_BIN", None)
    os.environ["CHROME_BIN"] = "/definitely/not/a/binary"
    try:
        checks.append(("bogus CHROME_BIN is not returned (existence-checked)",
                       chrome_bin() != "/definitely/not/a/binary"))
    finally:
        os.environ.pop("CHROME_BIN", None)

    # an invalid handle is refused BEFORE the network (wrong-subject fence)
    checks.append(("bad handle -> 'bad_handle' without rendering",
                   render_handle_detail("o")["status"] == "bad_handle"
                   and render_handle_detail("a b")["status"] == "bad_handle"))

    # the ingest note names the cause and the status rides the result
    c1 = ingest("", "ghost", render_status="login_wall")
    checks.append(("ingest note names the LOGIN WALL",
                   any("LOGIN INTERSTITIAL" in n for n in c1["notes"])))
    c2 = ingest("", "ghost", render_status="no_renderer")
    checks.append(("ingest note names the missing RENDERER",
                   any("NO RENDERER" in n for n in c2["notes"])))
    checks.append(("render_status carried on the ingest result",
                   c1["render_status"] == "login_wall"))
    checks.append(("the three causes no longer share one string",
                   "no rendered posts or alts" not in json.dumps(c1["notes"])))

    for name, cond in checks:
        if cond:
            ok += 1
        else:
            bad.append(name)

    total = len(checks)
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

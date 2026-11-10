#!/usr/bin/env python3
"""CHORUS LAW — story-weight anchor selection and chorus locking.

House order (Gabe via the Anthemist lane, 2026-11-10): the chorus must be the
customer's own human sentence, verbatim. A product/CTA caption is INELIGIBLE
as an anchor. Before the chorus locks, hashtags, URLs, @handles, CTAs and
price/shipping lines are mechanically stripped.

Measured defect this repairs: order AS-TEST-MAYA-20261110 picked the
"Librarian's Backbone" launch caption as the anchor, so the chorus sang a
sales line + "Now living on cozyafsweatshop.com" + the hashtag block, while
the human sentence ("I created my job out of thin air and I absolutely love
it. My 6-year-old would be proud") sat unused in verse 2.

Pure functions, no I/O, no network: safe to unit-test and to import from the
forge and the ingester.
"""
import re

URL_RE = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|shop|co|net|org|io)\b\S*", re.I)
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
HANDLE_RE = re.compile(r"(?<!\w)@\w+")
PRICE_RE = re.compile(r"[$€£]\s?\d|\b\d+(?:\.\d{2})?\s?(?:usd|dollars?)\b", re.I)
SHIPPING_RE = re.compile(r"\b(?:free|fast|same[-\s]?day)?\s*shipping\b|\bships?\s+(?:now|today|worldwide)\b|\bdelivery\b", re.I)
CTA_RE = re.compile(
    r"\b(?:shop|shopping|buy|order|purchase|checkout|link\s+in\s+bio|dm|dms|message\s+me|"
    r"click|sale|discount|promo|use\s+code|coupon|now\s+available|available\s+now|now\s+live|"
    r"is\s+live|just\s+dropped|new\s+drop|restock(?:ed)?|limited\s+time|pre[-\s]?order|"
    r"now\s+living\s+on|stock\s+up)\b",
    re.I,
)
PRODUCT_RE = re.compile(
    r"\b(?:hoodie|hoodies|sweatshirt|sweatshirts|sweatpants|tee|tees|t-shirt|t-shirts|shirt|"
    r"mug|mugs|tumbler|tumblers|sticker|stickers|print|prints|poster|posters|merch|"
    r"collection|drop|listing|listings|price|prices|etsy|shopify|cozyaf\w*|sweatshop|"
    r"bundle|sizes?\s+[xsml]|small\s+medium\s+large)\b",
    re.I,
)
PAST_RE = re.compile(
    r"\b(?:was|were|had|did|made|created|started|built|grew|moved|met|married|lost|found|"
    r"quit|left|got|went|came|became|took|wrote|sang|sung|worked|raised|survived|opened|"
    r"launched|turned|decided|learned|learnt|figured|kept|held|stood|ran|drove|fought|"
    r"raised|adopted|graduated|sold|bought|paid|saved|spent|gave)\b",
    re.I,
)
FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|my|mine|me|we|we're|our|ours|us)\b", re.I)
LIFE_RE = re.compile(
    r"\b(?:job|career|work|kid|kids|child|children|son|daughter|mom|mother|dad|father|"
    r"family|wife|husband|partner|friend|friends|grandma|grandpa|born|raised|years?|ago|"
    r"then|lost|passed|died|cancer|sobriety|sober|school|degree|home|house|business|"
    r"dream|proud|love|loved|scared|afraid|hard|struggle|struggled|fight|fought)\b",
    re.I,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def strip_noise(text):
    """Mechanically remove URLs, hashtags and @handles; collapse whitespace."""
    if not isinstance(text, str):
        return ""
    text = URL_RE.sub(" ", text)
    text = HASHTAG_RE.sub(" ", text)
    text = HANDLE_RE.sub(" ", text)
    # Collapse spaces/tabs only: newlines are sentence boundaries, and
    # collapsing them merged a stripped CTA tail into the NEXT caption
    # ("Shop now at I created my job..." — measured in the lane selftest).
    text = re.sub(r"[ \t]+", " ", text)
    # Mechanical punctuation hygiene: a separator-joined blob can leave "it..";
    # collapse doubled terminal punctuation and stray space-before-punctuation.
    # Words are never altered -- the chorus stays verbatim.
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([.!?])\1+", r"\1", text)
    lines = [line.strip(" \t-–—|·") for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def split_sentences(text):
    return [s.strip(" \t-–—|·") for s in SENTENCE_SPLIT_RE.split(text or "") if s and s.strip()]


def promotional(line):
    """True when a line is a product/CTA line rather than a human story line."""
    if not isinstance(line, str) or not line.strip():
        return True
    if URL_RE.search(line):
        return True
    if len(HASHTAG_RE.findall(line)) >= 2:
        return True
    hits = len(CTA_RE.findall(line)) + len(PRODUCT_RE.findall(line))
    if hits >= 2:
        return True
    # A bare CTA fragment ("Shop now at ." once the URL is stripped) is still a
    # CTA line even with a single hit -- unless it carries a human first-person.
    if CTA_RE.search(line) and not FIRST_PERSON_RE.search(line) and len(line.split()) <= 6:
        return True
    return False


def story_weight(line):
    """Rank a candidate line by story weight: first-person, past-tense, life event."""
    if not isinstance(line, str):
        return -100
    clean = strip_noise(line)
    if len(clean) < 12:
        return -100
    if promotional(line):
        return -100
    score = 0
    score += min(6, 2 * len(FIRST_PERSON_RE.findall(clean)))
    score += min(6, 2 * len(PAST_RE.findall(clean)))
    score += min(6, 2 * len(LIFE_RE.findall(clean)))
    if len(clean.split()) >= 6:
        score += 2
    if clean.isupper():
        score -= 2
    if clean.endswith("?"):
        score -= 1
    return score


def candidates(texts):
    """Yield (score, order, line) for every eligible sentence, best first."""
    out = []
    order = 0
    for text in texts or []:
        for sentence in split_sentences(strip_noise(text)):
            score = story_weight(sentence)
            # -100 = ineligible (promotional or a fragment). A neutral verbatim
            # line scores 0 and stays eligible, ranked below story lines.
            if score > -100:
                out.append((score, order, sentence))
            order += 1
    out.sort(key=lambda item: (-item[0], item[1]))
    return out


def pick(texts, source):
    """Pick the highest story-weight line. Returns (line, source) or (None, None)."""
    ranked = candidates(texts)
    if not ranked:
        return None, None
    return ranked[0][2], source


def chorus(anchor):
    """The chorus line: first non-promotional sentence of the anchor, verbatim.

    Stripping happens BEFORE the lock: hashtags, URLs, @handles, CTAs and
    price/shipping sentences are removed mechanically, then the first
    surviving sentence is the chorus. No paraphrasing, no invented line.
    """
    for sentence in split_sentences(strip_noise(anchor)):
        sentence = strip_noise(sentence)
        if not sentence or PRICE_RE.search(sentence) or SHIPPING_RE.search(sentence):
            continue
        if promotional(sentence):
            continue
        return sentence
    return None


def _selftest():
    checks = []

    human = ("I created my job out of thin air and I absolutely love it. "
             "My 6-year-old would be proud")
    sales = ("Our new Librarian's Backbone hoodie is now live! Shop now at "
             "cozyafsweatshop.com. #handmade #smallbusiness #cozyaf")
    launch = "Now living on cozyafsweatshop.com"

    checks.append(("sales caption is promotional", promotional(sales)))
    checks.append(("launch CTA line is promotional", promotional(launch)))
    checks.append(("human line is not promotional", not promotional(human)))
    checks.append(("human line outweighs sales line",
                   story_weight(human) > story_weight(sales)))
    line, src = pick([sales, human, launch], "brief")
    checks.append(("anchor picks the human story line, not the launch",
                   line == "I created my job out of thin air and I absolutely love it."))
    checks.append(("chorus is the human first sentence verbatim",
                   chorus(line) == "I created my job out of thin air and I absolutely love it."))
    checks.append(("chorus never contains a hashtag or URL",
                   "#" not in (chorus(sales) or "") and "http" not in (chorus(sales) or "")))
    checks.append(("chorus strips a trailing CTA sentence",
                   chorus("I built this with my own hands. Shop the drop now at cozyaf.com") ==
                   "I built this with my own hands."))
    checks.append(("pure sales text yields no chorus", chorus(sales) is None))
    checks.append(("no evidence yields no anchor", pick([], "brief") == (None, None)))

    # Specimen: the real AS-TEST-MAYA details blob = several captions, one per
    # line. The stripped CTA tail of caption 1 must NOT merge into caption 2.
    blob = "\n".join([sales, human, launch])
    blob_line, blob_src = pick([blob], "brief")
    checks.append(("multi-caption blob picks the human line",
                   blob_line == "I created my job out of thin air and I absolutely love it."))
    checks.append(("multi-caption chorus is clean",
                   chorus(blob_line) == "I created my job out of thin air and I absolutely love it."))

    bad = [name for name, ok in checks if not ok]
    print("anchor_law selftest: %d/%d passed" % (len(checks) - len(bad), len(checks)))
    for name in bad:
        print("FAIL:", name)
    return 0 if not bad else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())

"""Pick the keeper take: how much of the lyric sheet a take actually sings.

Pure stdlib. Transcription is a separate, optional step (faster_whisper is not a
runtime dependency); this module scores an existing transcript against the lyric
sheet and the caption anchor, so take selection is a repeatable number, not an ear.
"""
import difflib
import json
import re
import sys
from pathlib import Path

THRESHOLD = 0.72          # a unit counts as sung at or above this ratio
SECTION = re.compile(r"^\s*[\[(].*[\])]\s*$")
NON_WORD = re.compile(r"[^a-z0-9' ]+")


def normalize(text):
    """Lowercase, drop punctuation, collapse runs of whitespace."""
    return " ".join(NON_WORD.sub(" ", (text or "").lower()).split())


def words(text):
    return normalize(text).split()


def lyric_units(lyrics):
    """Lyric sheet -> comparable lines, section tags and blanks removed."""
    return [n for n in (normalize(line) for line in (lyrics or "").splitlines()
                        if not SECTION.match(line)) if n]


def anchor_lines(anchor):
    """Caption anchor -> the lines a take must sing, hashtag lines removed."""
    return [n for n in (normalize(line) for line in (anchor or "").splitlines()
                        if not line.strip().startswith("#")) if n]


def best_match(unit, spoken, threshold=THRESHOLD):
    """Highest similarity between a lyric unit and any same-length window of speech."""
    want, have = unit.split(), spoken
    if not want or not have:
        return 0.0
    span = len(want)
    if len(have) < span:
        return round(difflib.SequenceMatcher(None, want, have).ratio(), 3)
    return round(max(difflib.SequenceMatcher(None, want, have[i:i + span]).ratio()
                     for i in range(len(have) - span + 1)), 3)


def evaluate(transcript, lyrics, anchor="", threshold=THRESHOLD):
    """Score one take: per-unit similarity, coverage, and the anchor-critical lines."""
    spoken = words(transcript)
    units = [(u, best_match(u, spoken, threshold)) for u in lyric_units(lyrics)]
    hits = [u for u, s in units if s >= threshold]
    anchors = [(a, best_match(a, spoken, threshold)) for a in anchor_lines(anchor)]
    return {
        "units_total": len(units),
        "units_hit": len(hits),
        "coverage": round(len(hits) / len(units), 3) if units else 0.0,
        "missing": [{"line": u, "score": s} for u, s in units if s < threshold],
        "anchor_total": len(anchors),
        "anchor_hit": len([a for a, s in anchors if s >= threshold]),
        "anchor_min": min([s for _, s in anchors], default=0.0),
        "anchor_scores": [{"line": a, "score": s} for a, s in anchors],
    }


def pick_keeper(takes, lyrics, anchor="", threshold=THRESHOLD, peaks=None):
    """Rank takes: coverage first, then the weakest anchor line, then headroom.

    Headroom means the more negative true peak wins; an unmeasured take counts as
    0 dBFS so it can never outrank one we actually measured.
    """
    peaks = peaks or {}
    scored = {name: evaluate(t.get("text", ""), lyrics, anchor, threshold)
              for name, t in takes.items()}
    ranked = sorted(scored, key=lambda n: (-scored[n]["coverage"],
                                           -scored[n]["anchor_min"],
                                           peaks.get(n, 0.0)))
    return {"keeper": ranked[0] if ranked else None, "ranking": ranked, "takes": scored}


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[0])
        print("usage: qc_take_lyrics.py <transcripts.json> <prompt.json>")
        return 2
    takes = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    prompt = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(pick_keeper(takes, prompt.get("lyrics", ""), prompt.get("anchor", "")),
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

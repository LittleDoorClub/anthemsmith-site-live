"""Take selection is a number: coverage of the lyric sheet, anchor lines, keeper."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import qc_take_lyrics as q

LYRICS = """[Verse 1]
Dust on the oak and a desk with a dark side
I built the spine myself, bone by bone
[Chorus]
Real spine, real shade, warm light for late pages
Now living on cozyafsweatshop.com
"""
ANCHOR = """For a desk with a dark side.
Real spine. Real shade. Warm light for late pages.
Now living on cozyafsweatshop.com.
#lamplight #gothic"""

SUNG = ("dust on the oak and a desk with a dark side i built the spine myself bone by bone "
        "real spine real shade warm light for late pages now living on cozyafsweatshop com")


class NormalizeTests(unittest.TestCase):
    def test_strips_punctuation_and_case(self):
        self.assertEqual(q.normalize("Real Spine, REAL shade!"), "real spine real shade")

    def test_section_tags_are_not_lyric_lines(self):
        self.assertEqual(len(q.lyric_units(LYRICS)), 4)

    def test_anchor_drops_hashtag_line(self):
        self.assertEqual(len(q.anchor_lines(ANCHOR)), 3)

    def test_empty_input_is_empty_not_an_error(self):
        self.assertEqual(q.lyric_units(""), [])
        self.assertEqual(q.evaluate("", "")["coverage"], 0.0)


class ScoreTests(unittest.TestCase):
    def test_a_verbatim_take_covers_everything(self):
        got = q.evaluate(SUNG, LYRICS, ANCHOR)
        self.assertEqual((got['units_hit'], got['units_total']), (4, 4))
        self.assertEqual((got['anchor_hit'], got['anchor_total']), (3, 3))
        self.assertEqual(got['coverage'], 1.0)
        self.assertEqual(got['missing'], [])

    def test_a_take_that_skips_the_chorus_is_short(self):
        got = q.evaluate("dust on the oak and a desk with a dark side", LYRICS, ANCHOR)
        self.assertEqual(got['units_hit'], 1)
        self.assertLess(got['coverage'], 0.3)
        self.assertEqual(len(got['missing']), 3)

    def test_whisper_garbling_still_clears_the_threshold(self):
        # real artifact: the sung "Ybor City" comes back as "Boer City"
        got = q.best_match("handmade in ybor city", q.words("handmade in boer city"))
        self.assertGreaterEqual(got, q.THRESHOLD)

    def test_similarity_is_bounded_and_symmetric_enough(self):
        self.assertEqual(q.best_match("real spine", ["real", "spine"]), 1.0)
        self.assertLess(q.best_match("real spine", ["dust", "oak"]), 0.4)
        self.assertEqual(q.best_match("", []), 0.0)


class KeeperTests(unittest.TestCase):
    def test_the_take_that_sings_more_lines_wins(self):
        takes = {"take1": {"text": "dust on the oak and a lamp with a dark side"},
                 "take2": {"text": SUNG}}
        got = q.pick_keeper(takes, LYRICS, ANCHOR)
        self.assertEqual(got['keeper'], 'take2')
        self.assertEqual(got['ranking'], ['take2', 'take1'])

    def test_equal_coverage_falls_to_the_stronger_weakest_anchor(self):
        takes = {"a": {"text": SUNG}, "b": {"text": SUNG}}
        self.assertEqual(q.pick_keeper(takes, LYRICS, ANCHOR)['keeper'], 'a')
        self.assertEqual(q.pick_keeper(takes, LYRICS, ANCHOR, peaks={"a": -0.3, "b": -1.1})['keeper'], 'b')

    def test_one_take_is_a_keeper_not_a_crash(self):
        got = q.pick_keeper({"only": {"text": SUNG}}, LYRICS)
        self.assertEqual(got['keeper'], 'only')
        self.assertEqual(got['takes']['only']['anchor_total'], 0)

    def test_no_takes_picks_nothing(self):
        self.assertIsNone(q.pick_keeper({}, LYRICS)['keeper'])


if __name__ == '__main__':
    unittest.main()

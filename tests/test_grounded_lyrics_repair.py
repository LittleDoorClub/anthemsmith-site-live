"""A strict lyric validator must name the rule it enforced and repair before it blocks.

``forge_failed_or_audio_incomplete:photo_lyrics: lyrics_structure_or_provenance_invalid``
was an unactionable hold: the validator swallowed which constraint failed, so the only way
to learn it was another paid run. And a model that drifts on a formatting rule (six
sections, 4-8 lines, 220-520 words, every photo cited) dead-ended the order outright.

Both are pinned here. The second is the important one: the repair is ONE extra composition
call re-judged by the SAME validator -- nothing is loosened, nothing retries forever.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import grounded_lyrics as gl  # noqa: E402

PHOTO = [{"source_id": "photo-01", "sha256": "a" * 64, "storage_path": "orders/x/p1.jpg",
          "provider": "openai",
          "result": {"observations": ["a red mug on a chipped wooden table beside a window"],
                     "visible_text": [{"text": "HELLO", "confidence": "high"}]}}]
LINE = "the red mug waits on the chipped wooden table by the window light"


def composition(sections=6, lines=4):
    return {"title": "Red Mug Morning", "sections": [
        {"name": name, "lines": [{"text": "%s %d %d" % (LINE, s, n),
                                  "source_ids": ["photo-01"]} for n in range(lines)]}
        for s, name in enumerate(gl.SECTION_NAMES[:sections])]}


def response(payload):
    return json.dumps({"id": "req_1", "choices": [{"finish_reason": "stop",
                       "message": {"refusal": None, "content": json.dumps(payload)}}]}).encode()


class ValidatorNamesTheRule(unittest.TestCase):
    def test_missing_sections_name_the_constraint(self):
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl._validate_composition(composition(sections=5), {"photo-01"}, {"photo-01"})
        self.assertTrue(str(caught.exception).endswith(":structure"), caught.exception)

    def test_short_line_names_the_constraint(self):
        bad = composition()
        bad["sections"][0]["lines"][0]["text"] = "hi"
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl._validate_composition(bad, {"photo-01"}, {"photo-01"})
        self.assertTrue(str(caught.exception).endswith(":line_length"), caught.exception)

    def test_short_song_carries_its_own_measurement(self):
        thin = composition()
        for section in thin["sections"]:
            section["lines"] = [{"text": "aaaaaa", "source_ids": ["photo-01"]} for _ in range(4)]
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl._validate_composition(thin, {"photo-01"}, {"photo-01"})
        self.assertTrue(str(caught.exception).endswith(":song_length_24w"), caught.exception)

    def test_direction_names_the_fix(self):
        self.assertIn("add lines", gl._direction("song_length_198w"))
        self.assertIn("shorten", gl._direction("song_length_600w"))
        self.assertEqual(gl._direction("structure"), "")

    def test_uncited_photo_names_the_constraint(self):
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl._validate_composition(composition(), {"photo-01"}, {"photo-02"})
        self.assertTrue(str(caught.exception).endswith(":photo_omitted"), caught.exception)


class OneBoundedRepair(unittest.TestCase):
    def setUp(self):
        self.prompts, self.real = [], gl._API_KEY
        gl._API_KEY = "test-key"

    def tearDown(self):
        gl._API_KEY = self.real

    def http(self, bodies):
        def call(request, timeout, limit):
            payload = json.loads(request.data)
            # System note AND the user payload: refused line ids ride in the user message.
            self.prompts.append(" || ".join(m["content"] for m in payload["messages"]))
            return response(bodies.pop(0))
        return call

    def test_bad_composition_is_repaired_once(self):
        review = {"title_supported": True, "source_instructions_followed": False,
                  "reviews": [{"line_id": "s%d-l%d" % (s, n), "verdict": "supported"}
                              for s in range(1, 7) for n in range(1, 5)]}
        result = gl.compose_photo_lyrics(PHOTO, "a thank you for a friend", "", "", "",
                                         "test-key", self.http([composition(sections=5),
                                                                composition(), review]))
        self.assertEqual(result.title, "Red Mug Morning")
        self.assertEqual(len(self.prompts), 3)
        self.assertIn("rejected by the validator with code structure", self.prompts[1])
        # A bare code leaves the model guessing; the note must carry what it actually wrote.
        self.assertIn("(5 sections, 20 lines,", self.prompts[1])
        self.assertIn("words)", self.prompts[1])

    def test_junk_draft_still_yields_a_note(self):
        self.assertEqual(gl._shape("nonsense"), "")
        self.assertEqual(gl._shape({"sections": [{"lines": "no"}]}), "")

    def test_second_rejection_blocks(self):
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl.compose_photo_lyrics(PHOTO, "", "", "", "", "test-key",
                                    self.http([composition(sections=5), composition(sections=4)]))
        self.assertTrue(str(caught.exception).endswith(":structure"), caught.exception)
        self.assertEqual(len(self.prompts), 2, "a rejection must not buy a third call")

    def test_unsourced_line_is_rewritten_once(self):
        refused = {"title_supported": True, "source_instructions_followed": False,
                   "reviews": [{"line_id": "s%d-l%d" % (s, n),
                                "verdict": "uncertain" if (s, n) == (2, 3) else "supported"}
                               for s in range(1, 7) for n in range(1, 5)]}
        review = {"title_supported": True, "source_instructions_followed": False,
                  "reviews": [{"line_id": "s%d-l%d" % (s, n), "verdict": "supported"}
                              for s in range(1, 7) for n in range(1, 5)]}
        result = gl.compose_photo_lyrics(PHOTO, "", "", "", "", "test-key",
                                         self.http([composition(), refused, composition(), review]))
        self.assertEqual(result.provenance["grounding_review"], "passed")
        self.assertEqual(len(self.prompts), 4)
        # The rewrite names the refused line by id, never its text, and fixes the structure.
        self.assertIn("s2-l3", self.prompts[2])
        self.assertIn("same number of lines per section", self.prompts[2])

    def reviews(self, refused, verdict="uncertain"):
        return {"title_supported": True, "source_instructions_followed": False,
                "reviews": [{"line_id": "s%d-l%d" % (s, n),
                             "verdict": verdict if (s, n) in refused else "supported"}
                            for s in range(1, 7) for n in range(1, 5)]}

    def partial(self, refused, verdict="uncertain"):
        return {"title_supported": True, "source_instructions_followed": False,
                "reviews": [{"line_id": "s%d-l%d" % (s, n), "verdict": verdict}
                            for s in range(1, 7) for n in range(1, 5) if (s, n) in refused]}

    def repair(self, line_id):
        return {"replacements": [{"line_id": line_id,
                                  "text": "the chipped table holds a red mug by the window light",
                                  "source_ids": ["photo-01"]}]}

    def test_targeted_repair_replaces_only_the_refused_line(self):
        bodies = [composition(), self.reviews({(2, 3)}),
                  composition(), self.reviews({(2, 3)}),
                  self.repair("s2-l3"), self.partial({(2, 3)}, "supported")]
        result = gl.compose_photo_lyrics(PHOTO, "", "", "", "", "test-key", self.http(bodies))
        self.assertEqual(len(self.prompts), 6)
        self.assertEqual(result.provenance["grounding_review_scope"], "replaced_lines:1")
        # Only the refused line is asked for again -- never the other 23.
        self.assertIn("s2-l3", self.prompts[4])
        self.assertNotIn("s1-l1", self.prompts[4])

    def test_repair_still_refused_holds(self):
        bodies = [composition(), self.reviews({(2, 3)}),
                  composition(), self.reviews({(2, 3)}),
                  self.repair("s2-l3"), self.partial({(2, 3)}),
                  self.repair("s2-l3"), self.partial({(2, 3)})]
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl.compose_photo_lyrics(PHOTO, "", "", "", "", "test-key", self.http(bodies))
        self.assertTrue(str(caught.exception).endswith(":unsupported_or_uncertain_1"), caught.exception)
        self.assertEqual(len(self.prompts), 8)
        # Pass 2 abandons metaphor: a refused line must become a visible detail, not a feeling.
        self.assertIn("literal description", self.prompts[6])

    def test_repair_must_cover_every_refused_line(self):
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl._apply_replacements(composition(), {"replacements": []}, {"s2-l3"})
        self.assertTrue(str(caught.exception).endswith(":repair_coverage"), caught.exception)

    def test_injection_verdict_raises_without_repair(self):
        dirty = {"title_supported": True, "source_instructions_followed": True, "reviews": []}
        with self.assertRaises(gl.GroundedLyricsError) as caught:
            gl._rejected_lines(dirty, [{"line_id": "s1-l1", "source_ids": ["photo-01"]}])
        self.assertTrue(str(caught.exception).endswith(":title_or_injection"), caught.exception)


if __name__ == "__main__":
    unittest.main()

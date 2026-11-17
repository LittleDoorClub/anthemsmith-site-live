"""The keeper decision has to reach the bytes a customer gets, or it is a note.

Real ffmpeg fixtures, real sha256, no mocks: the point of the module is that it
refuses to lie about a file, so the tests assert refusals too.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import adopt_keeper as ak

ORDER = 'AS-TEST-UNIT'


def build_mp3(path, seconds, freq):
    out = subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=%d:duration=%s' % (freq, seconds),
         '-c:a', 'libmp3lame', '-q:a', '9', str(path)],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-400:])
    return path


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.songs = Path(self.tmp.name)
        self.keeper = build_mp3(self.songs / ('%s-t2-full.mp3' % ORDER), 60, 330)
        self.alternate = build_mp3(self.songs / ('%s-full.mp3' % ORDER), 58, 440)
        self.hook = build_mp3(self.songs / ('%s.mp3' % ORDER), 30, 550)
        self.takes = {
            'keeper': 'take2',
            'keeper_file': self.keeper.name,
            'keeper_sha256': ak.sha256_file(self.keeper),
            'keeper_duration': round(ak.probe_duration(self.keeper), 2),
            'alternate': {'take': 'take1', 'file': self.alternate.name,
                          'sha256': ak.sha256_file(self.alternate),
                          'duration': round(ak.probe_duration(self.alternate), 2)},
            'keeper_decided_at': '2026-11-16T04:09:34-05:00',
            'keeper_decided_by': 'gforce-ox dna4 1c31',
        }
        ak.write_json(self.songs / ('%s.takes.json' % ORDER), self.takes)
        self.manifest = {'order_id': ORDER, 'status': 'audio_ready',
                         'duration_full': self.takes['alternate']['duration']}
        ak.write_json(self.songs / ('%s.json' % ORDER), self.manifest)
        self.manifest_path = self.songs / ('%s.json' % ORDER)

    def tearDown(self):
        self.tmp.cleanup()

    def read_manifest(self):
        return json.loads(self.manifest_path.read_text(encoding='utf-8'))


class AdoptTests(Fixture):
    def test_full_song_becomes_the_keeper_and_old_take_is_kept(self):
        ak.adopt(ORDER, self.songs)
        self.assertEqual(ak.sha256_file(self.alternate), self.takes['keeper_sha256'])
        backup = self.songs / ('%s-take1-full.mp3' % ORDER)
        self.assertTrue(backup.exists(), 'the losing take must not vanish')
        self.assertEqual(ak.sha256_file(backup), self.takes['alternate']['sha256'])

    def test_manifest_records_what_changed_and_why(self):
        before = self.read_manifest()['duration_full']
        ak.adopt(ORDER, self.songs)
        m = self.read_manifest()
        self.assertAlmostEqual(m['duration_full'], 60.0, delta=1.0)
        self.assertEqual(m['duration_full_superseded'], before)
        self.assertEqual(m['keeper']['take'], 'take2')
        self.assertEqual(m['keeper']['sha256'], self.takes['keeper_sha256'])
        self.assertEqual(m['keeper']['decided_by'], 'gforce-ox dna4 1c31')
        self.assertEqual(m['keeper']['qc_tool'], 'scripts/qc_take_lyrics.py')
        self.assertTrue(m['needs_private_ingest'])

    def test_second_run_is_a_no_op(self):
        ak.adopt(ORDER, self.songs)
        first = self.read_manifest()
        full_sha = ak.sha256_file(self.songs / ('%s-full.mp3' % ORDER))
        hook_sha = ak.sha256_file(self.songs / ('%s.mp3' % ORDER))
        again = ak.adopt(ORDER, self.songs)
        self.assertIn('full_already_keeper', again['ops'])
        self.assertEqual(again['mode'], 'noop')
        self.assertEqual(again['note'], 'no_writes')
        self.assertEqual(ak.sha256_file(self.songs / ('%s-full.mp3' % ORDER)), full_sha)
        self.assertEqual(ak.sha256_file(self.songs / ('%s.mp3' % ORDER)), hook_sha)
        self.assertEqual(self.read_manifest(), first)


class RefusalTests(Fixture):
    def test_swapped_keeper_file_is_refused_and_nothing_written(self):
        before_sha = ak.sha256_file(self.alternate)
        before_manifest = self.read_manifest()
        takes = dict(self.takes, keeper_sha256='0' * 64)
        ak.write_json(self.songs / ('%s.takes.json' % ORDER), takes)
        with self.assertRaises(ak.Refused) as ctx:
            ak.adopt(ORDER, self.songs)
        self.assertIn('keeper_sha256_mismatch', str(ctx.exception))
        self.assertEqual(ak.sha256_file(self.alternate), before_sha)
        self.assertEqual(self.read_manifest(), before_manifest)
        self.assertFalse((self.songs / ('%s-take1-full.mp3' % ORDER)).exists())

    def test_missing_keeper_duration_disagreement_is_refused(self):
        takes = dict(self.takes, keeper_duration=999.0)
        ak.write_json(self.songs / ('%s.takes.json' % ORDER), takes)
        with self.assertRaises(ak.Refused) as ctx:
            ak.adopt(ORDER, self.songs)
        self.assertIn('keeper_duration_mismatch', str(ctx.exception))

    def test_missing_takes_manifest_is_refused(self):
        (self.songs / ('%s.takes.json' % ORDER)).unlink()
        with self.assertRaises(ak.Refused) as ctx:
            ak.adopt(ORDER, self.songs)
        self.assertIn('no_takes_manifest', str(ctx.exception))

    def test_cli_reports_refusal_as_exit_2(self):
        takes = dict(self.takes, keeper_sha256='0' * 64)
        ak.write_json(self.songs / ('%s.takes.json' % ORDER), takes)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ak.main([ORDER, '--songs', str(self.songs)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buf.getvalue())['status'], 'refused')


class CheckModeTests(Fixture):
    def test_check_writes_nothing(self):
        before = self.read_manifest()
        report = ak.adopt(ORDER, self.songs, check=True)
        self.assertEqual(report['mode'], 'check')
        self.assertFalse(report['full_is_keeper'])
        self.assertEqual(ak.sha256_file(self.alternate), self.takes['alternate']['sha256'])
        self.assertEqual(self.read_manifest(), before)
        self.assertFalse((self.songs / ('%s-take1-full.mp3' % ORDER)).exists())


class HookTests(Fixture):
    def test_hook_is_recut_from_the_keeper_at_the_forge_rule(self):
        old_hook_sha = ak.sha256_file(self.hook)
        report = ak.adopt(ORDER, self.songs)
        m = self.read_manifest()
        self.assertNotEqual(ak.sha256_file(self.hook), old_hook_sha)
        self.assertEqual(m['hook']['derived_from'], self.keeper.name)
        self.assertEqual(m['hook']['start_seconds'], max(0, int(60 * 0.72) - 15))
        self.assertAlmostEqual(m['hook']['duration'], 30.0, delta=2.0)
        self.assertEqual(report['hook_start_seconds'], m['hook']['start_seconds'])

    def test_no_rehook_leaves_the_demo_alone(self):
        old_sha = ak.sha256_file(self.hook)
        ak.adopt(ORDER, self.songs, rehook=False)
        self.assertEqual(ak.sha256_file(self.hook), old_sha)
        self.assertNotIn('hook', self.read_manifest())

    def test_short_keeper_never_fabricates_a_hook(self):
        tiny = build_mp3(self.songs / 'tiny.mp3', 5, 330)
        takes = dict(self.takes, keeper_file=tiny.name, keeper_sha256=ak.sha256_file(tiny),
                     keeper_duration=round(ak.probe_duration(tiny), 2))
        ak.write_json(self.songs / ('%s.takes.json' % ORDER), takes)
        with self.assertRaises(ak.Refused) as ctx:
            ak.adopt(ORDER, self.songs)
        self.assertIn('hook_duration_out_of_range', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()

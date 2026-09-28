"""Fault and integration tests. Real codec tools; every HTTP call is in-memory."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import private_audio as pa
import order_workflow as w
import notification_provider as n
from private_audio_fixtures import OID, pair, Store, Response, clone

ROOT = Path(__file__).resolve().parents[1]
ENV = {'SUPABASE_SERVICE_ROLE_KEY': 'offline-dummy', 'MUAPI_KEY': 'offline-dummy',
       'AS_PRIVATE_AUDIO_ENABLED': '1', 'AS_MUAPI_OUTPUT_HOSTS': 'media.example.test'}

class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.full = Path(cls.fixture.name) / 'full.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=8000', '-t', '181', '-c:a', 'libmp3lame',
            '-b:a', '8k', str(cls.full)], check=True)
        cls.audio = cls.full.read_bytes()
    @classmethod
    def tearDownClass(cls): cls.fixture.cleanup()
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'checkout'; self.root.mkdir()
        (self.root / 'songs').mkdir()
        self.env = patch.dict(os.environ, {**ENV, 'RUNNER_TEMP': self.tmp.name})
        self.env.start(); self.addCleanup(self.env.stop)
        self.store = Store(self.audio)
        for module, name in ((pa, 'open_request'), (w, 'store_open')):
            p = patch.object(module, name, side_effect=self.store.open)
            p.start(); self.addCleanup(p.stop)
    def test_real_full_decode_and_limits(self):
        qc = pa.qc_file(self.full)
        self.assertEqual(qc['sha256'], hashlib.sha256(self.audio).hexdigest())
        self.assertTrue(180 <= qc['duration_seconds'] <= 240)
        for payload in (b'NOT MP3', self.audio[:4096], b'x' * (pa.MAX_BYTES + 1)):
            p = Path(self.tmp.name) / 'bad.mp3'; p.write_bytes(payload)
            with self.assertRaisesRegex(pa.PrivateAudioError, 'qc_failed'): pa.qc_file(p)
    def test_probe_duration_is_not_decoded_duration(self):
        for seconds in (179, 241):
            p = Path(self.tmp.name) / (str(seconds) + '.mp3')
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono',
                '-t', str(seconds), '-c:a', 'libmp3lame', '-b:a', '8k', str(p)], check=True)
            with self.assertRaisesRegex(pa.PrivateAudioError, 'qc_failed'): pa.qc_file(p)
    def test_round_trip_immutable_upload_context_cas_and_edge(self):
        before = clone(self.store.row['delivery'])
        manifest, context = pa.register_audio(OID, self.full)
        self.assertEqual(len(self.store.objects), 1)
        self.assertEqual(len(pa.context_token(OID, context)), 43)
        self.assertNotIn('url', self.store.manifest)
        self.assertNotIn(pa.context_token(OID, context), json.dumps(self.store.manifest))
        for k, v in before.items(): self.assertEqual(self.store.row['delivery'][k], v)
        methods = [(r.get_method(), urlsplit(r.full_url).path) for r in self.store.calls]
        upload = next(i for i, x in enumerate(methods) if x[0] == 'POST' and '/storage/' in x[1])
        insertion = methods.index(('POST', '/rest/v1/' + pa.TABLE))
        private_read = next(i for i, x in enumerate(methods) if '/object/authenticated/' in x[1])
        self.assertLess(upload, private_read); self.assertLess(private_read, insertion)
        self.assertEqual(self.store.calls[-1].full_url, pa.EDGE)
        patches = [r for r in self.store.calls if r.get_method() == 'PATCH']
        self.assertEqual(len(patches), 2)
        self.assertEqual(json.loads(parse_qs(urlsplit(patches[0].full_url).query)['delivery'][0][3:]), before)
        self.assertEqual(parse_qs(urlsplit(patches[0].full_url).query)['delivery_result'], ['eq.{}'])
        # A retry cannot mint a new token/path or upload/insert again.
        self.store.calls.clear()
        with patch.object(pa.secrets, 'token_urlsafe', side_effect=AssertionError('rotation')):
            self.assertEqual(pa.register_audio(OID, Path('missing-on-fresh-runner')), (manifest, context))
        self.assertTrue(all(r.get_method() == 'GET' for r in self.store.calls))
    def test_fresh_runner_uses_durable_pair_before_generation(self):
        self.store.installed()
        before = clone(self.store.row)
        with patch.object(w, 'read_private', return_value=self.store.order()):
            run = Mock(side_effect=AssertionError('regeneration'))
            self.assertEqual(w.forge(self.root, run), 0)
            run.assert_not_called()
        self.assertEqual(before, self.store.row)
        self.assertFalse(any(self.root.rglob('*.mp3')))
        self.assertTrue(all(r.get_method() == 'GET' for r in self.store.calls))
    def test_each_torn_pair_blocks_before_generation_and_send(self):
        for shape in ('manifest_only', 'context_only', 'pending', 'mismatch', 'revoked', 'expired'):
            with self.subTest(shape=shape):
                self.store = Store(self.audio); self.store.installed()
                # Existing side-effect bindings refer to original Store; rebind each case.
                with patch.object(pa, 'open_request', side_effect=self.store.open):
                    d = self.store.row['delivery']
                    if shape == 'manifest_only': d.pop('private_song_access')
                    elif shape == 'context_only': self.store.manifest = None
                    elif shape == 'pending': d['private_audio_pending'] = {'opaque': True}
                    elif shape == 'mismatch': self.store.manifest['token_sha256'] = 'f' * 64
                    elif shape == 'revoked': self.store.manifest['revoked_at'] = self.store.manifest['token_issued_at']
                    elif shape == 'expired': self.store.manifest['expires_at'] = '2020-01-01T00:00:00Z'
                    with self.assertRaises(pa.PrivateAudioError): pa.read_state(OID)
                    self.assertTrue(all(r.get_method() == 'GET' for r in self.store.calls))
    def test_upload_failure_is_durable_pending_not_regeneratable(self):
        def fail(req, store):
            if req.get_method() == 'POST' and '/storage/' in req.full_url: raise TimeoutError('private body')
        self.store.fault = fail
        with self.assertRaises(pa.PrivateAudioError): pa.register_audio(OID, self.full)
        self.assertIsNone(self.store.manifest)
        self.assertIn('private_audio_pending', self.store.row['delivery'])
        with self.assertRaisesRegex(pa.PrivateAudioError, 'torn_write'): pa.read_state(OID)
        self.assertFalse(self.store.row['delivery_result'])
    def test_storage_readback_mismatch_prevents_registration(self):
        def corrupt(req, store):
            if '/object/authenticated/' in req.full_url:
                for key in store.objects: store.objects[key] = store.objects[key][:-1]
        self.store.fault = corrupt
        with self.assertRaisesRegex(pa.PrivateAudioError, 'bytes_mismatch'): pa.register_audio(OID, self.full)
        self.assertIsNone(self.store.manifest)
        self.assertIn('private_audio_pending', self.store.row['delivery'])
    def test_manifest_insert_timeout_never_mints_replacement(self):
        def tear(req, store):
            if req.get_method() == 'POST' and '/rest/v1/' + pa.TABLE in req.full_url:
                store.manifest = json.loads(req.data)  # committed, response lost
                raise TimeoutError('sensitive body')
        self.store.fault = tear
        with self.assertRaises(pa.PrivateAudioError): pa.register_audio(OID, self.full)
        self.assertIsNotNone(self.store.manifest)
        self.store.fault = None
        with self.assertRaisesRegex(pa.PrivateAudioError, 'torn_write'): pa.register_audio(OID, self.full)
    def test_final_context_cas_loss_is_not_ready(self):
        def tear(req, store):
            if req.get_method() == 'PATCH' and 'private_song_access' in json.loads(req.data).get('delivery', {}):
                raise TimeoutError('lost')
        self.store.fault = tear
        with self.assertRaises(pa.PrivateAudioError): pa.register_audio(OID, self.full)
        self.assertIsNotNone(self.store.manifest)
        with self.assertRaisesRegex(pa.PrivateAudioError, 'torn_write'): pa.read_state(OID)
    def test_two_context_contenders_exactly_one_wins(self):
        row = pa.snapshot(OID)
        barrier = threading.Barrier(2)
        def contend(index):
            barrier.wait()
            try:
                pa.cas_delivery(OID, row, {**row['delivery'], 'private_audio_pending': {'contender': index}})
                return True
            except pa.PrivateAudioError: return False
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(contend, (1, 2)))
        self.assertEqual(sorted(results), [False, True])
    def test_cas_success_response_without_durable_readback_fails(self):
        row = pa.snapshot(OID)
        proposed = {**row['delivery'], 'private_audio_job': {'attempt_id': 'fake'}}
        with patch.object(pa, 'api', return_value=[{**row, 'delivery': proposed}]), patch.object(pa, 'snapshot', return_value=row):
            with self.assertRaisesRegex(pa.PrivateAudioError, 'cas_unverified'): pa.cas_delivery(OID, row, proposed)
    def test_customer_edge_hash_mismatch_never_notifies(self):
        self.store.installed()
        path = self.store.manifest['object_path']; self.store.objects[path] = b'x' * len(self.audio)
        send = Mock(side_effect=AssertionError('notification'))
        with patch.object(w, 'read_private', return_value=self.store.order()):
            with self.assertRaisesRegex(w.GateError, 'bytes_mismatch'): w.deliver(self.root, send=send)
        send.assert_not_called(); self.assertFalse(self.store.row['delivery_result'])
    def test_pair_then_notification_then_fresh_retry_no_resend(self):
        pa.register_audio(OID, self.full)
        self.store.calls.clear()
        links = []
        def send(order, attempt):
            self.assertEqual(self.store.row['delivery_result']['delivery_status'], 'sending')
            self.assertTrue(any(r.full_url == pa.EDGE for r in self.store.calls))
            links.append(order['private_song_access']['url'])
            return {'order_id': OID, 'delivery_status': 'submitted', 'attempt_id': attempt,
                    'provider': 'resend', 'provider_id': '11111111-1111-4111-8111-111111111111', 'provider_status': 'queued'}
        with patch.object(w, 'read_private', return_value=self.store.order()):
            self.assertEqual(w.deliver(self.root, send=send), 0)
            self.assertEqual(w.deliver(self.root, send=send, lookup=lambda order, state: state), 0)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0], self.store.row['delivery']['private_song_access']['url'])
        receipt = json.loads((self.root/'songs'/(OID+'.delivery.json')).read_text())
        self.assertEqual(set(receipt), {'order_id', 'delivery_status', 'provider_status'})
        sends = [r for r in self.store.calls if r.get_method() == 'PATCH' and
                 json.loads(r.data).get('delivery_result', {}).get('delivery_status') == 'sending']
        self.assertEqual(len(sends), 1)
        self.assertIn('delivery', parse_qs(urlsplit(sends[0].full_url).query))
    def test_provider_unknown_fence_survives_new_runner_and_token_unchanged(self):
        self.store.installed()
        before = clone(self.store.row['delivery']['private_song_access'])
        calls = []
        def send(*args):
            calls.append(True)
            raise n.NotificationError('provider_result_unknown', ambiguous=True)
        with patch.object(w, 'read_private', return_value=self.store.order()):
            self.assertEqual(w.deliver(self.root, send=send), 1)
            (self.root/'songs'/(OID+'.delivery.json')).unlink()
            self.assertEqual(w.deliver(self.root, send=send), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(before, self.store.row['delivery']['private_song_access'])
        self.assertEqual(self.store.row['delivery_result']['delivery_status'], 'unknown')
    def test_recipient_change_blocks_before_provider(self):
        self.store.installed()
        self.store.row['delivery']['email'] = 'different@example.test'
        with self.assertRaisesRegex(w.GateError, 'recipient_changed'): w.load_private_access(self.store.order())
    def test_new_generation_runs_outside_git_without_public_consent(self):
        calls = []
        def child(cmd, cwd, env, **kwargs):
            calls.append(cmd)
            self.assertNotEqual(cwd, self.root)
            self.assertEqual(cmd[1], str(self.root/'scripts'/'forge_order.py'))
            with patch.dict(os.environ, env):
                rid = pa.submit_generation(OID, {'model': 'V6', 'prompt': 'synthetic words'})
                pa.download_output(pa.poll_generation(rid), Path(cwd)/'songs'/(OID+'-full.mp3'))
            return subprocess.CompletedProcess(cmd, 0, 'PRIVATE CHILD TEXT', 'PRIVATE STDERR')
        logs = io.StringIO()
        with patch.object(w, 'read_private', return_value=self.store.order()), patch.object(w, 'canonical_order', return_value=self.store.order()):
            with contextlib.redirect_stdout(logs):
                self.assertEqual(w.forge(self.root, child), 0)
                self.assertEqual(w.forge(self.root, child), 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(self.root.rglob('*.mp3')))
        self.assertNotIn('PRIVATE', logs.getvalue())
        public = ''.join(p.read_text() for p in (self.root/'songs').glob('*.json'))
        for forbidden in ('token', 'object_path', 'prompt', 'private_song_access', 'lyrics', 'offline@example.test'):
            self.assertNotIn(forbidden, public)
    def test_confirmed_preflight_failure_retries_without_music_resubmit(self):
        attempts = []
        def failed_child(cmd, cwd, env, **kwargs):
            attempts.append(env['AS_GENERATION_ATTEMPT'])
            return subprocess.CompletedProcess(cmd, 1, '', 'synthetic recoverable vision error')
        with patch.object(w, 'read_private', return_value=self.store.order()), patch.object(w, 'canonical_order', return_value=self.store.order()):
            self.assertEqual(w.forge(self.root, failed_child), 1)
            self.assertEqual(self.store.row['delivery']['private_audio_job']['status'], 'preflight_failed')
            self.assertEqual(w.forge(self.root, failed_child), 1)
        self.assertEqual(len(set(attempts)), 2)
        self.assertFalse(any('/suno-create-music' in r.full_url for r in self.store.calls))
        # A former child's stale reservation cannot spend after a retry reserved.
        with patch.dict(os.environ, AS_GENERATION_ATTEMPT=attempts[0]):
            with self.assertRaisesRegex(pa.PrivateAudioError, 'reservation_unverified'):
                pa.submit_generation(OID, {'model': 'V6'})
    def test_failed_child_after_submission_remains_read_only_resumable(self):
        def failed_child(cmd, cwd, env, **kwargs):
            with patch.dict(os.environ, env): pa.submit_generation(OID, {'model': 'V6'})
            return subprocess.CompletedProcess(cmd, 1, '', 'synthetic poll timeout')
        with patch.object(w, 'read_private', return_value=self.store.order()), patch.object(w, 'canonical_order', return_value=self.store.order()):
            self.assertEqual(w.forge(self.root, failed_child), 1)
            self.assertEqual(self.store.row['delivery']['private_audio_job']['status'], 'submitted')
            self.assertEqual(w.forge(self.root, Mock(side_effect=AssertionError('second child'))), 0)
        self.assertEqual(sum('/suno-create-music' in r.full_url for r in self.store.calls), 1)
    def test_real_forge_script_private_branch_end_to_end_with_provider_doubles(self):
        # Execute the actual top-level forge, not an imitation; only provider-facing
        # vision/composition/HTTP calls are doubles. ffmpeg/ffprobe remain real.
        import runpy
        import shutil
        from types import SimpleNamespace
        import photo_intake
        import grounded_lyrics
        (self.root/'scripts').mkdir()
        shutil.copyfile(ROOT/'scripts'/'forge_order.py', self.root/'scripts'/'forge_order.py')
        order = {**self.store.order(), 'paid_status': 'paid_verified', 'intake': 'photos',
                 'details': 'Synthetic source-backed fixture', 'photo_urls': [OID+'/11111111-1111-4111-8111-111111111111.png']}
        photo = SimpleNamespace(descriptions=['a painted circle'], ocr_texts=[], storage_paths=order['photo_urls'],
            analysis=[{'result': {'observations': ['a painted circle']}}])
        song = SimpleNamespace(lyrics='[Chorus]\nSynthetic source-backed fixture', title='Synthetic test')
        def child(cmd, cwd, env, **kwargs):
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                with patch.dict(os.environ, env), patch.object(photo_intake, 'intake_photos', return_value=photo), \
                     patch.object(grounded_lyrics, 'compose_photo_lyrics', return_value=song), \
                     patch.object(photo_intake, 'persist_composition'), contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(cmd[1], run_name='__main__')
                return subprocess.CompletedProcess(cmd, 0, '', '')
            finally:
                os.chdir(previous)
        with patch.object(w, 'read_private', return_value=order), patch.object(w, 'canonical_order', return_value=order):
            self.assertEqual(w.forge(self.root, child), 0)
        self.assertEqual(self.store.row['delivery']['private_audio_job']['request_id'], 'offline-job-1')
        self.assertEqual(sum('/suno-create-music' in r.full_url for r in self.store.calls), 1)
        self.assertFalse(any((self.root/'songs').glob('*.mp3')))
        self.assertIsNotNone(self.store.manifest)
        post = next(r for r in self.store.calls if r.full_url.endswith('/suno-create-music'))
        body = json.loads(post.data)
        self.assertEqual((body['custom_mode'], body['instrumental'], body['model'], body['duration']), (True, False, 'V6', 210))
        self.assertIn('Synthetic source-backed fixture', body['prompt'])
        marker = json.loads((self.root/'songs'/(OID+'.json')).read_text())
        self.assertIs(marker['private_audio'], True)
    def test_public_marker_private_audio_boolean_only(self):
        # Boolean marker lets the public player route to its own private link; it
        # must survive sanitization yet never carry private values.
        w.write_json(self.root/'songs'/(OID+'.json'), {'order_id': OID, 'status': 'audio_ready',
            'private_audio': True, 'duration_full': 205.4, 'token': 'secret', 'object_path': 'x/y.mp3',
            'song': {'lyrics': 'secret'}, 'note': 'no echo'})
        w.sanitize_public_artifacts(self.root, OID)
        safe = json.loads((self.root/'songs'/(OID+'.json')).read_text())
        self.assertIs(safe['private_audio'], True)
        self.assertEqual(safe['duration_full'], 205.4)
        self.assertEqual(set(safe) - {'order_id', 'status', 'duration_full', 'private_audio'}, set())
        w.write_json(self.root/'songs'/(OID+'.json'), {'order_id': OID, 'status': 'audio_ready',
            'private_audio': 'true', 'sha256_full': 'f'*64})
        w.sanitize_public_artifacts(self.root, OID)
        safe = json.loads((self.root/'songs'/(OID+'.json')).read_text())
        self.assertNotIn('private_audio', safe)  # no string coercion, no fabricated marker
    def test_accepted_request_id_resumes_get_only_on_fresh_runner(self):
        job = pa.reserve_generation(OID)
        with patch.dict(os.environ, AS_GENERATION_ATTEMPT=job['attempt_id']):
            self.assertEqual(pa.submit_generation(OID, {'model': 'V6'}), 'offline-job-1')
        self.store.calls.clear()
        pa.resume_generation(OID, Path(self.tmp.name)/'resumed.mp3')
        self.assertTrue(all(r.get_method() == 'GET' for r in self.store.calls))
        self.assertTrue(any('/predictions/offline-job-1/result' in r.full_url for r in self.store.calls))
    def test_unknown_submission_no_resubmit_or_fallback(self):
        job = pa.reserve_generation(OID)
        def unknown(req, store):
            if '/suno-create-music' in req.full_url: raise TimeoutError('provider accepted maybe')
        self.store.fault = unknown
        with patch.dict(os.environ, AS_GENERATION_ATTEMPT=job['attempt_id']):
            with self.assertRaisesRegex(pa.PrivateAudioError, 'submission_unknown'): pa.submit_generation(OID, {'model': 'V6'})
            with self.assertRaisesRegex(pa.PrivateAudioError, 'reservation_unverified'): pa.submit_generation(OID, {'model': 'V6'})
        self.assertEqual(self.store.row['delivery']['private_audio_job']['status'], 'submitting')
        self.assertEqual(sum('/suno-create-music' in r.full_url for r in self.store.calls), 1)
        with self.assertRaisesRegex(pa.PrivateAudioError, 'reconciliation'): pa.resume_generation(OID, self.root/'never.mp3')
    def test_reserved_unsubmitted_or_bad_job_never_automatically_spends(self):
        for job in ({'status': 'reserved'}, {'status': 'submitting'}, {'status': 'submitted', 'model': 'V6', 'request_id': '../bad'}):
            self.store.row['delivery']['private_audio_job'] = job
            with self.assertRaises(pa.PrivateAudioError): pa.resume_generation(OID, self.root/'never.mp3')
        self.assertTrue(all(r.get_method() == 'GET' for r in self.store.calls))
    def test_submitted_failed_or_pending_never_falls_back(self):
        for result in ({'status': 'failed'}, {'status': 'completed', 'outputs': []}):
            self.store.muapi_result = result
            self.store.row['delivery']['private_audio_job'] = {'status': 'submitted', 'model': 'V6', 'request_id': 'offline-job-1'}
            with self.assertRaises(pa.PrivateAudioError): pa.resume_generation(OID, self.root/'never.mp3')
        with self.assertRaisesRegex(pa.PrivateAudioError, 'poll_pending'): pa.poll_generation('offline-job-1', max_seconds=0)
        self.assertTrue(all(r.get_method() == 'GET' for r in self.store.calls))
    def test_untrusted_output_host_refused_before_download(self):
        for url in ('http://media.example.test/a', 'https://evil.invalid/a', 'https://user:pass@media.example.test/a', 'https://media.example.test:443/a', 'https://media.example.test/a#token'):
            with self.assertRaises(pa.PrivateAudioError): pa.download_output({'outputs': [url]}, self.root/'never.mp3')
        self.assertEqual(self.store.calls, [])
    def test_legacy_complete_or_partial_audio_stops_regeneration(self):
        for suffix in ('-full.mp3', '.mp3', '.json', '.claim.json'):
            p = self.root/'songs'/(OID+suffix); p.write_bytes(b'existing evidence')
            with patch.object(w, 'read_private', return_value=self.store.order()):
                self.assertEqual(w.forge(self.root, Mock(side_effect=AssertionError('spend'))), 1)
            self.assertEqual(p.read_bytes(), b'existing evidence'); p.unlink()
    def test_no_raw_capability_in_logs_outputs_or_public_metadata(self):
        logs = io.StringIO()
        with contextlib.redirect_stdout(logs), contextlib.redirect_stderr(logs):
            manifest, context = pa.register_audio(OID, self.full)
        token = pa.context_token(OID, context)
        self.assertEqual(logs.getvalue(), '')
        payload = {'status': 'audio_ready', 'duration_full': 181, 'url': context['url'],
            'context': context, 'object_path': manifest['object_path'], 'lyrics': 'PRIVATE LYRIC', 'title': 'PRIVATE NAME'}
        w.write_json(self.root/'songs'/(OID+'.json'), payload)
        w.sanitize_public_artifacts(self.root, OID)
        public = (self.root/'songs'/(OID+'.json')).read_text()
        for value in (token, context['url'], manifest['object_path'], 'PRIVATE LYRIC', 'PRIVATE NAME'):
            self.assertNotIn(value, public)
    def test_publish_allowlist_never_stages_mp3_claim_or_context(self):
        for suffix in ('.mp3', '-full.mp3', '.claim.json', '.context.json'):
            (self.root/'songs'/(OID+suffix)).write_text('PRIVATE')
        w.write_json(self.root/'songs'/(OID+'.json'), {'status': 'audio_ready'})
        calls = []
        def git(root, args, check=True):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(w, 'git', side_effect=git): self.assertEqual(w.publish(self.root, OID), 0)
        added = next(c for c in calls if c[:2] == ['add', '-A'])
        self.assertEqual(added[3:], ['songs/'+OID+'.json'])
        self.assertTrue(all(not s.endswith('.mp3') and '.claim.' not in s and '.context.' not in s for c in calls for s in c))
    def test_publish_refuses_preexisting_staged_secret(self):
        def git(root, args, check=True):
            return subprocess.CompletedProcess(args, 0, 'songs/private-context.json\n', '')
        with patch.object(w, 'git', side_effect=git):
            with self.assertRaisesRegex(w.GateError, 'unexpected_staged'): w.publish(self.root, OID)
    def test_staging_inside_checkout_refused(self):
        with patch.dict(os.environ, RUNNER_TEMP=str(self.root/'private')):
            with self.assertRaisesRegex(w.GateError, 'outside_checkout'): w.stage_path(self.root, OID)

class ContractTests(unittest.TestCase):
    def test_exact_private_url_no_public_or_query_fallback(self):
        _, context = pair()
        for oid, ctx in ((OID.lower(), context), (OID, {**context, 'url': context['url'].replace('#', '?')}),
                         (OID, {**context, 'url': context['url']+'&extra=1'}),
                         (OID, {**context, 'url': context['url'].replace('anthemsmith.com', 'evil.invalid')}),
                         (OID, {**context, 'token_sha256': 'f'*64}), (OID, None)):
            with self.assertRaises(pa.PrivateAudioError): pa.context_token(oid, ctx)
    def test_manifest_boundaries_and_expiry(self):
        manifest, _ = pair()
        for key, value in (('byte_size', 0), ('byte_size', pa.MAX_BYTES+1), ('byte_size', True),
            ('duration_seconds', 179.99), ('duration_seconds', 240.001), ('duration_seconds', float('nan')),
            ('object_path', OID+'/../song.mp3'), ('mime_type', 'audio/wav'), ('order_id', 'AS-OTHER-ORDER'),
            ('expires_at', '2099-01-01T00:00:00Z'), ('token_issued_at', '2020-01-01T00:00:00')):
            with self.subTest(key=key, value=value), self.assertRaises(pa.PrivateAudioError):
                pa.validate_manifest(OID, {**manifest, key: value})
    def test_private_transport_status_mime_redirect_and_size(self):
        req = pa.urllib.request.Request(pa.EDGE)
        for status, mime, body in ((206, 'audio/mpeg', b'x'), (200, 'text/html', b'x'), (200, 'audio/mpeg', b'12345')):
            with patch.object(pa, 'open_request', return_value=Response(body, status, mime)):
                with self.assertRaises(pa.PrivateAudioError): pa.request_bytes(req, 4, 'audio/mpeg')
        with self.assertRaisesRegex(pa.PrivateAudioError, 'redirect_refused'): pa.NoRedirect().redirect_request(None, None, None, None, None, None)
    def test_disabled_or_public_bucket_fails_closed(self):
        with patch.dict(os.environ, AS_PRIVATE_AUDIO_ENABLED=''), self.assertRaisesRegex(pa.PrivateAudioError, 'not_configured'):
            pa.require_config()
        for bucket in ({'id': pa.BUCKET, 'public': True, 'file_size_limit': pa.MAX_BYTES, 'allowed_mime_types': ['audio/mpeg']},
                       {'id': pa.BUCKET, 'public': False, 'file_size_limit': None, 'allowed_mime_types': ['audio/mpeg']}):
            with patch.dict(os.environ, AS_PRIVATE_AUDIO_ENABLED='1'), patch.object(pa, 'api', return_value=bucket):
                with self.assertRaisesRegex(pa.PrivateAudioError, 'bucket_not_private'): pa.require_config()
    def test_email_and_sms_exact_private_link_receipts_no_token(self):
        _, ctx = pair()
        for mode in ('email', 'sms'):
            order = {'order_id': OID, 'consent_verified': True, 'delivery_valid': True, 'delivery': mode,
                'email': 'offline@example.test' if mode == 'email' else '', 'sms_to': '+12025550123' if mode == 'sms' else '',
                'private_song_access': ctx}
            calls = []
            def http(req):
                calls.append(req)
                return {'id': '11111111-1111-4111-8111-111111111111'} if mode == 'email' else {'sid': 'SM'+'a'*32, 'status': 'queued'}
            with patch.dict(os.environ, RESEND_KEY='dummy', TWILIO_SID='AC'+'a'*32, TWILIO_TOKEN='dummy', TWILIO_FROM='+12025550111'):
                result = n.send(order, 'attempt', http=http)
            body = json.loads(calls[0].data)['text'] if mode == 'email' else parse_qs(calls[0].data.decode())['Body'][0]
            self.assertIn(ctx['url'], body); self.assertNotIn('/?oid=', body)
            self.assertNotIn(ctx['url'], json.dumps(result))
            with self.assertRaisesRegex(n.NotificationError, 'private_song_access_required'):
                n.send({**order, 'private_song_access': None}, 'attempt', http=Mock(side_effect=AssertionError('send')))
    def test_workflow_private_readiness_no_public_audio_dependency(self):
        text = (ROOT/'.github/workflows/anthemsmith-order.yml').read_text()
        self.assertIn('AS_PRIVATE_AUDIO_ENABLED: ${{ vars.AS_PRIVATE_AUDIO_ENABLED }}', text)
        self.assertIn('cancel-in-progress: false', text)
        # Delivery still requires publish + forge success, but nothing routes through
        # public MP3s or artifacts; private readback happens inside deliver().
        self.assertIn("steps.publish_audio.outcome == 'success'", text)
        self.assertNotIn('upload-artifact', text)
        self.assertNotIn('-full.mp3', text)
        self.assertEqual(w.PUBLIC_SUFFIXES, ('.json', '.blocked.json', '.failed.json', '.delivery.json'))

if __name__ == '__main__': unittest.main()

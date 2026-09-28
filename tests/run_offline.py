"""Run the complete Python suite with empty credentials and denied Python networking.
All temporary outputs stay inside this isolated checkout. ffmpeg fixture subprocesses
use local lavfi inputs; no production entrypoints or provider subprocesses are run.
"""
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
keep = {k: v for k, v in os.environ.items() if k.upper() in
        {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'LANG'}}
os.environ.clear()
os.environ.update(keep)
tmp = ROOT / 'evidence' / 'tmp'
tmp.mkdir(parents=True, exist_ok=True)
os.environ.update(TMP=str(tmp), TEMP=str(tmp), TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE='1')
tempfile.tempdir = str(tmp)
sys.dont_write_bytecode = True

def deny(event, args):
    if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.bind', 'socket.sendto'):
        raise RuntimeError('offline_network_denied')
sys.addaudithook(deny)
# Verify this guard rather than infer it from mocks.
try:
    socket.getaddrinfo('provider.invalid', 443)
except RuntimeError as exc:
    assert str(exc) == 'offline_network_denied'
else:
    raise AssertionError('network guard absent')
print('OFFLINE: environment allowlist; DNS/socket network denied; no provider credentials.', flush=True)
os.chdir(ROOT)
suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())

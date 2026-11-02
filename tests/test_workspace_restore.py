"""Offline encrypted snapshot fixtures, no cloud access."""
import base64
import io
import json
import zipfile

import pytest

pytest.importorskip('cryptography')
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from scripts.restore_workspace import restore


def test_restore_verifies_and_never_overwrites(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('studio.sqlite3', b'offline fixture database')
    salt, nonce = b's' * 16, b'n' * 12
    key = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(b'offline-password')
    def encode(x):
        return base64.b64encode(x).decode()
    state = {'kdf': {'n':32768,'r':8,'p':1}, 'salt':encode(salt), 'nonce':encode(nonce),
             'ciphertext':encode(AESGCM(key).encrypt(nonce, archive.getvalue(), b'frameweave-workspace-v1'))}
    data = {'state':json.dumps(state).encode(), 'resources':json.dumps({'files':[
        {'path':'media/fixture.mp4','storage':'media'}]}).encode(), 'media':b'offline media'}
    class Cloud:
        def __init__(self, root):
            pass
        def verify(self, ref, destination):
            destination.write(data[ref])
    monkeypatch.setattr('scripts.restore_workspace.CosStorage', Cloud)
    ticket = tmp_path/'ticket.json'
    ticket.write_text(json.dumps({'format':'frameweave-workspace-v1','state':'state','resources':'resources'}))
    target = tmp_path/'restored'
    with pytest.raises(Exception):
        restore(ticket, tmp_path, target, 'wrong-password')
    assert not target.exists()
    restore(ticket, tmp_path, target, 'offline-password')
    assert (target/'media/fixture.mp4').read_bytes() == b'offline media'
    with pytest.raises(ValueError):
        restore(ticket, tmp_path, target, 'offline-password')
    assert (target/'studio.sqlite3').read_bytes() == b'offline fixture database'

#!/usr/bin/env python3
"""Restore a verified COS workspace into a new private directory; never overwrite live data."""
from __future__ import annotations

import argparse
import base64
import getpass
import io
import json
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.cloud_storage import CosStorage  # noqa: E402


def restore(ticket_path, data_dir, target, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    ticket = json.loads(ticket_path.read_text())
    if ticket['format'] != 'frameweave-workspace-v1':
        raise ValueError('Unsupported backup format')
    cloud = CosStorage(data_dir)
    def read(ref):
        data = io.BytesIO()
        cloud.verify(ref, data)
        return data.getvalue()
    manifest = json.loads(read(ticket['resources']))
    state = json.loads(read(ticket['state']))
    if state['kdf'] != {'n': 32768, 'r': 8, 'p': 1}:
        raise ValueError('Unsupported encryption parameters')
    key = Scrypt(salt=base64.b64decode(state['salt']), length=32, **state['kdf']).derive(password.encode())
    raw = AESGCM(key).decrypt(base64.b64decode(state['nonce']), base64.b64decode(state['ciphertext']), b'frameweave-workspace-v1')
    target = target.resolve()
    # Stage everything away from the running database and settings.
    if target.exists():
        raise ValueError('目标目录已存在；请选择新的 .local 子目录，不覆盖现有数据')
    target.mkdir(parents=True, mode=0o700)
    def destination(name):
        path = (target / name).resolve()
        if not path.is_relative_to(target) or path == target:
            raise ValueError('Unsafe backup path')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = destination(info.filename)
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
                f.write(archive.read(info))
    for i, item in enumerate(manifest['files'], 1):
        path = destination(item['path'])
        if path.exists():
            raise ValueError('Duplicate backup path')
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
            cloud.verify(item['storage'], f)
        if i % 25 == 0:
            print(f'已校验恢复 {i}/{len(manifest["files"])}', flush=True)
    print(f'恢复完成：{target}。项目已暂停；停止服务后再切换数据目录。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ticket', type=Path)
    parser.add_argument('--data-dir', type=Path, default=Path('.local'))
    parser.add_argument('--target', type=Path, default=Path('.local/restored-workspace'))
    args = parser.parse_args()
    try:
        restore(args.ticket, args.data_dir, args.target, getpass.getpass('备份恢复密码：'))
    except Exception as exc:
        raise SystemExit(f'恢复未完成（{type(exc).__name__}）；原工作区未覆盖。检查配置、密码或目标目录后重试。') from None


if __name__ == '__main__':
    main()

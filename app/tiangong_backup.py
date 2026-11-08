"""Offline, non-overwriting restore of a Tiangong project backup. No provider calls."""
import hashlib
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from .tiangong import validate_manifest


def restore_backup(archive_path, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('恢复目录必须不存在，禁止覆盖已有项目')
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [e.filename for e in entries]
        if len(entries) > 101 or len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('备份目录无效')
        if archive.getinfo('manifest.json').file_size > 2 * 1024 * 1024:
            raise ValueError('项目档案过大')
        manifest = json.loads(archive.read('manifest.json'))
        validate_manifest(manifest)
        assets = manifest.get('assets', [])
        if len(assets) > 100:
            raise ValueError('素材数量超过上限')
        expected = {'manifest.json'}
        for asset in assets:
            aid = asset['id']
            if not isinstance(aid, str) or len(aid) != 32 or any(c not in '0123456789abcdef' for c in aid):
                raise ValueError('素材ID无效')
            name = 'assets/' + aid
            if name in expected or name not in names:
                raise ValueError('素材重复或缺失')
            expected.add(name)
            entry = archive.getinfo(name)
            if not 0 < entry.file_size <= 10 * 1024 * 1024 or entry.file_size != asset['size']:
                raise ValueError('素材大小无效')
            if hashlib.sha256(archive.read(name)).hexdigest() != asset['sha256']:
                raise ValueError('素材校验失败')
        if set(names) != expected:
            raise ValueError('备份包含未知文件')
        destination.mkdir(parents=True, exist_ok=False)
        try:
            folder = destination / 'tiangong_assets'
            folder.mkdir()
            for asset in assets:
                (folder / asset['id']).write_bytes(archive.read('assets/' + asset['id']))
            connection = sqlite3.connect(destination / 'studio.sqlite3')
            try:
                with connection:
                    connection.execute('CREATE TABLE tiangong_manifests (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
                    connection.execute('INSERT INTO tiangong_manifests VALUES (?, ?)', ('pilot', json.dumps(manifest, ensure_ascii=False)))
            finally:
                connection.close()
        except Exception:
            shutil.rmtree(destination)
            raise
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='将天工备份恢复到全新目录，不覆盖已有数据')
    parser.add_argument('archive')
    parser.add_argument('destination')
    args = parser.parse_args()
    print(restore_backup(args.archive, args.destination))

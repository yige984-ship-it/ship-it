import base64
import json
import zipfile
from io import BytesIO
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.tiangong_backup import restore_backup


def test_backup_restore_roundtrip_and_no_overwrite(tmp_path):
    client = TestClient(create_app(tmp_path/'source'))
    character = dict(character_id='lead',name='主角',face='脸',hair='发',costume='衣',left_hand_ring='戒',waist_token='牌',shoes='鞋')
    client.post('/api/tiangong/pilot', json=character)
    asset = client.post('/api/tiangong/pilot/assets',json={'name':'ref','content_base64':base64.b64encode(b'reference').decode()}).json()
    archive = tmp_path/'backup.zip'
    archive.write_bytes(client.get('/api/tiangong/pilot/backup').content)
    target = tmp_path/'restored'
    restore_backup(archive, target)
    restored = TestClient(create_app(target))
    assert restored.get('/api/tiangong/pilot').json() == client.get('/api/tiangong/pilot').json()
    assert restored.get('/api/tiangong/pilot/assets/'+asset['id']).content == b'reference'
    with pytest.raises(ValueError,match='覆盖'):
        restore_backup(archive, target)
    assert restored.get('/api/tiangong/pilot').status_code == 200
    with zipfile.ZipFile(archive,'a') as z:
        z.writestr('../escape', 'bad')
    with pytest.raises(ValueError,match='未知'):
        restore_backup(archive, tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


def test_corrupt_backup_never_creates_destination(tmp_path):
    from app.tiangong import create_pilot_manifest, CharacterBible
    m = create_pilot_manifest([CharacterBible('lead','主角','脸','发','衣','戒','牌','鞋')])
    aid='a'*32
    m['assets']=[dict(id=aid,size=3,sha256='wrong')]
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('manifest.json',json.dumps(m))
        z.writestr('assets/'+aid,b'abc')
    with pytest.raises(ValueError,match='校验'):
        restore_backup(archive,tmp_path/'target')
    assert not (tmp_path/'target').exists()

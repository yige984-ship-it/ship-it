"""FastAPI integration checks for Tiangong pilot assets."""
from fastapi.testclient import TestClient
from app.main import create_app

def test_pilot_assets_roundtrip(tmp_path):
    client = TestClient(create_app(tmp_path))
    character = {
        "character_id": "lead", "name": "待核对主角", "face": "固定脸",
        "hair": "固定发型", "costume": "固定衣服",
        "left_hand_ring": "左手戒指", "waist_token": "腰牌", "shoes": "纹饰鞋"
    }
    response = client.post("/api/tiangong/pilot", json=character)
    assert response.status_code == 201, response.text
    manifest = response.json()
    assert len(manifest["shots"]) == 18
    assert manifest["budget_cny"] == "140"
    assert manifest["paid_api_enabled"] is False
    assert client.post("/api/tiangong/pilot", json=character).status_code == 409
    response = client.put("/api/tiangong/pilot/shots/1", json={
        "prompt": "山门远景", "character_ids": ["lead"]
    })
    assert response.status_code == 200, response.text
    assert client.get("/api/tiangong/pilot").json()["shots"][0]["prompt"] == "山门远景"
    assert client.put("/api/tiangong/pilot/shots/2", json={
        "prompt": "错误角色", "character_ids": ["unknown"]
    }).status_code == 422
    character["face"] = "修订脸部参考"
    response = client.put("/api/tiangong/pilot/characters/lead", json=character)
    assert response.status_code == 200, response.text
    assert response.json()["characters"][0]["version"] == 2
    assert client.get("/api/tiangong/pilot").json()["characters"][0]["face"] == "修订脸部参考"

def test_asset_restart_and_character_invalidation(tmp_path):
    import base64
    client = TestClient(create_app(tmp_path))
    character = dict(character_id='lead', name='主角', face='脸', hair='发', costume='衣',
                     left_hand_ring='戒', waist_token='牌', shoes='鞋')
    assert client.get('/tiangong').status_code == 200
    assert client.post('/api/tiangong/pilot', json=character).status_code == 201
    invalid = dict(character, face='   ')
    assert client.put('/api/tiangong/pilot/characters/lead', json=invalid).status_code == 422
    assert client.post('/api/tiangong/pilot/assets', json={'name':'bad','content_base64':'!!!'}).status_code == 422
    content = b'reference fixture'
    response = client.post('/api/tiangong/pilot/assets', json={
        'name':'reference.png', 'content_base64':base64.b64encode(content).decode()})
    assert response.status_code == 201
    asset = response.json()
    character['reference_asset_ids'] = [asset['id']]
    assert client.put('/api/tiangong/pilot/characters/lead', json=character).status_code == 200
    shot = client.put('/api/tiangong/pilot/shots/1', json={'prompt':'远景','character_ids':['lead']}).json()['shots'][0]
    assert shot['reference_asset_ids'] == [asset['id']]
    assert shot['character_versions'] == {'lead':2}
    restarted = TestClient(create_app(tmp_path))
    assert restarted.get('/api/tiangong/pilot/assets/'+asset['id']).content == content
    assert restarted.get('/api/tiangong/pilot').json()['shots'][0] == shot
    character['face'] = '新脸'
    result = restarted.put('/api/tiangong/pilot/characters/lead', json=character).json()
    assert result['shots'][0]['character_versions'] == {}
    assert result['shots'][0]['reference_asset_ids'] == []
    report = restarted.get('/api/tiangong/pilot/readiness').json()
    assert 'stale_character_version' in report['shot_blockers'][0]['reasons']
    assert result['shots'][0]['approved'] is False
    character['reference_asset_ids'] = ['missing']
    assert restarted.put('/api/tiangong/pilot/characters/lead', json=character).status_code == 422
    assert restarted.get('/api/tiangong/pilot/assets/missing').status_code == 404

def test_large_image_upload_and_validation(tmp_path):
    from io import BytesIO
    from PIL import Image
    client = TestClient(create_app(tmp_path))
    c = dict(character_id='lead',name='主角',face='脸',hair='发',costume='衣',left_hand_ring='戒',waist_token='牌',shoes='鞋')
    client.post('/api/tiangong/pilot', json=c)
    image = BytesIO()
    Image.new('RGB', (300,300)).save(image, format='PNG', compress_level=0)
    content = image.getvalue()
    assert len(content) > 60000
    response = client.post('/api/tiangong/pilot/assets/image?name=reference.png', content=content)
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset['media_type'] == 'image/png'
    assert TestClient(create_app(tmp_path)).get('/api/tiangong/pilot/assets/'+asset['id']).content == content
    assert client.post('/api/tiangong/pilot/assets/image', content=b'fake image').status_code == 422
    assert client.post('/api/tiangong/pilot/assets/image', content=b'x'*(10*1024*1024+1)).status_code == 413

def test_backup_integrity_and_readiness(tmp_path):
    import base64, json, zipfile
    from io import BytesIO
    client = TestClient(create_app(tmp_path))
    character = dict(character_id='lead',name='主角',face='脸',hair='发',costume='衣',left_hand_ring='戒',waist_token='牌',shoes='鞋')
    client.post('/api/tiangong/pilot', json=character)
    report = client.get('/api/tiangong/pilot/readiness').json()
    assert not report['planning_ready'] and len(report['shot_blockers']) == 18
    data = b'reference'
    asset = client.post('/api/tiangong/pilot/assets',json={'name':'ref','content_base64':base64.b64encode(data).decode()}).json()
    backup = client.get('/api/tiangong/pilot/backup')
    assert backup.status_code == 200
    with zipfile.ZipFile(BytesIO(backup.content)) as z:
        assert z.read('assets/'+asset['id']) == data
        assert len(json.loads(z.read('manifest.json'))['shots']) == 18
    path = tmp_path / 'tiangong_assets' / asset['id']
    path.write_bytes(b'corrupt')
    assert client.get('/api/tiangong/pilot/backup').status_code == 409
    assert client.get('/api/tiangong/pilot/readiness').json()['asset_issues'][0]['reason'] == 'checksum_mismatch'
    path.unlink()
    assert client.get('/api/tiangong/pilot/readiness').json()['asset_issues'][0]['reason'] == 'missing'

def test_tiangong_private_endpoints_require_login(tmp_path, monkeypatch):
    monkeypatch.setenv('REQUIRE_AUTH', 'true')
    monkeypatch.setenv('STUDIO_ACCESS_PASSWORD', 'unit-test-password-123')
    client = TestClient(create_app(tmp_path), base_url='https://testserver')
    for path in ('/pilot', '/pilot/backup', '/pilot/readiness', '/pilot/assets/anything', '/storage'):
        assert client.get('/api/tiangong'+path).status_code == 401
    assert client.post('/api/tiangong/pilot/assets/image', content=b'image').status_code == 401
    assert client.post('/api/session', json={'password':'unit-test-password-123'}).status_code == 200
    assert client.get('/api/tiangong/storage').status_code == 200

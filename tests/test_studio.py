import json
import shutil
import subprocess
import pytest
from fastapi.testclient import TestClient
from app.main import create_app

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c

def make(client, **kwargs):
    r = client.post('/api/projects', json={'text': '林玄踏入青云宗。长老传下剑诀！少年御剑而行。', **kwargs})
    assert r.status_code == 201
    return r.json()

def test_plan_and_persistence(client):
    p = make(client)
    assert len(p['shots']) == 12
    assert sum(s['duration'] for s in p['shots']) == 120
    assert p['actual_api_cost_cny'] == 0
    assert '林玄' in ''.join(s['narration'] for s in p['shots'])
    assert client.get('/api/projects/'+p['id']).json() == p
    assert p['characters'][0]['source'].startswith('默认模板')

def test_budget_blocks(client):
    p = make(client, budget_cny=0.01)
    assert not p['budget_ok']
    assert client.post(f"/api/projects/{p['id']}/render", json={}).status_code == 409

def test_input_and_real_calls(client):
    for payload in ({'text':''}, {'text':'x'*20001}, {'text':'a','budget_cny':151}):
        assert client.post('/api/projects', json=payload).status_code == 422
    p = make(client)
    assert client.post(f"/api/projects/{p['id']}/render", json={'mode':'real'}).status_code == 409
    assert client.get(f"/api/projects/{p['id']}/download").status_code == 409
    assert client.get('/api/projects/missing').status_code == 404

def test_limits(client, monkeypatch):
    monkeypatch.setenv('MAX_REQUEST_BYTES', '10')
    assert client.post('/api/projects', content='x'*11).status_code == 413
    monkeypatch.setenv('MAX_REQUEST_BYTES', '100000')
    monkeypatch.setenv('MAX_PROJECTS', '1')
    make(client)
    assert client.post('/api/projects', json={'text':'测试'}).status_code == 429

def test_failure_and_attempt_limit(client, monkeypatch):
    from app.adapters import MockImage
    def fail(*args): raise RuntimeError('test failure')
    monkeypatch.setattr(MockImage, 'generate', fail)
    monkeypatch.setenv('MAX_RENDER_ATTEMPTS', '1')
    p = make(client)
    url = f"/api/projects/{p['id']}/render"
    assert client.post(url, json={}).json()['status'] == 'failed'
    assert client.post(url, json={}).status_code == 429

def test_ffmpeg_end_to_end(client, tmp_path):
    assert shutil.which('ffmpeg'), 'FFmpeg required for integration test'
    p = make(client)
    r = client.post(f"/api/projects/{p['id']}/render", json={}).json()
    assert r['status'] == 'completed_mock', r
    preview = client.get(r['preview_url'], headers={'Range': 'bytes=0-1023'})
    assert preview.status_code == 206
    assert len(preview.content) == 1024
    assert preview.headers['content-type'] == 'video/mp4'
    assert client.get(r['preview_url']).content == client.get(r['download_url']).content
    response = client.get(r['download_url'])
    assert response.status_code == 200
    output = tmp_path / 'verify.mp4'
    output.write_bytes(response.content)
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(output)]))
    assert abs(float(probe['format']['duration'])-120) < 0.2
    assert {s['codec_type'] for s in probe['streams']} == {'video','audio'}
    assert all(s['voice_task'] == 'completed_mock_silence' for s in r['shots'])

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
    e = editable(r)
    e['style'] = 'ink'
    edited = client.put(f"/api/projects/{p['id']}/plan", json=e).json()
    assert edited['status'] == 'planned'
    assert 'download_url' not in edited and 'preview_url' not in edited
    assert edited['shots'][0]['prompt'].startswith('中国水墨')
    assert client.get(r['preview_url']).status_code == 409
    assert client.get(r['download_url']).status_code == 409


def editable(p):
    return dict(revision=p.get('revision', 1), style=p.get('style', 'xianxia'), budget_cny=p['budget_cny'],
                characters=[{k:c[k] for k in ('id','name','appearance','consistency')} for c in p['characters']],
                shots=[{k:s[k] for k in ('id','duration','strategy','narration','prompt')} for s in p['shots']])

def test_edit_reprices_and_conflicts(client):
    p = make(client, style='ink')
    assert '中国水墨' in p['shots'][0]['prompt']
    e = editable(p)
    e['characters'][0]['name'] = '林玄'
    e['shots'][0]['strategy'] = 'low_motion'
    e['shots'][0]['prompt'] = '水墨山门中的林玄'
    e['shots'][0]['duration'] = 15
    e['shots'][1]['duration'] = 5
    e['budget_cny'] = 0.01
    url = f"/api/projects/{p['id']}/plan"
    r = client.put(url, json=e)
    assert r.status_code == 200
    updated = r.json()
    assert updated['estimated_cny'] > p['estimated_cny']
    assert updated['characters'][0]['name'] == '林玄'
    assert not updated['budget_ok']
    assert updated['revision'] == 2
    assert client.put(url, json=e).status_code == 409
    assert client.post(f"/api/projects/{p['id']}/render", json={}).status_code == 409
    assert client.get(f"/api/projects/{p['id']}").json() == updated

def test_reject_invalid_plan(client):
    p = make(client)
    url = f"/api/projects/{p['id']}/plan"
    e = editable(p)
    e['shots'][0]['duration'] = 11
    assert client.put(url, json=e).status_code == 422
    e = editable(p)
    e['shots'][0]['id'] = 2
    assert client.put(url, json=e).status_code == 422
    e = editable(p)
    e['shots'][0]['estimated_cny'] = 0
    assert client.put(url, json=e).status_code == 422
    e = editable(p)
    e['style'] = 'unknown'
    assert client.put(url, json=e).status_code == 422
    assert client.get(f"/api/projects/{p['id']}").json() == p

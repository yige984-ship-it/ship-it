import io
import json
import sqlite3
import subprocess
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.main import create_app
from app.episodes import subtitle_cues,write_srt,recover_episodes
from test_studio import make, editable

@pytest.fixture
def episode_client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c,tmp_path

def seed_image(data,p,shot=1,revision=None):
    jid=('%032x'%shot)
    path=data/'image-assets'/jid/'image.png';path.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(360,640),(40+shot,70,90)).save(path)
    job=dict(id=jid,project_id=p['id'],revision=revision or p['revision'],shot_id=shot,
             status='completed_real_image',asset_url=f'/api/image-jobs/{jid}/asset')
    with sqlite3.connect(data/'images.sqlite3') as c:
        c.execute('CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        c.execute('INSERT OR REPLACE INTO images VALUES(?,?)',(jid,json.dumps(job)))
    return jid

def post_episode(c,p,ids,**kw):
    return c.post(f"/api/projects/{p['id']}/episodes",json=dict(revision=p['revision'],image_job_ids=ids,**kw))

def probe_download(c,j,tmp_path):
    response=c.get(j['download_url']);assert response.status_code==200
    path=tmp_path/'result.mp4';path.write_bytes(response.content)
    result=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)]))
    assert abs(float(result['format']['duration'])-j['duration'])<.25
    assert {s['codec_type'] for s in result['streams']}=={'video','audio'}
    assert c.get(j['preview_url'],headers={'Range':'bytes=0-1023'}).status_code==206


def test_selected_shots_and_subtitles(episode_client,tmp_path):
    c,data=episode_client;p=make(c,budget_cny=0)
    ids=[seed_image(data,p,1),seed_image(data,p,2)]
    r=post_episode(c,p,ids);assert r.status_code==202,r.text
    j=c.get('/api/episodes/'+r.json()['id']).json()
    assert j['status']=='completed_local',j
    assert j['duration']==20 and len(j['timeline'])==2
    assert not j['reused_images'] and j['voice_mode']=='silent'
    assert '林玄' in c.get(j['subtitles_url']).text
    manifest=c.get('/api/episodes/'+j['id']+'/manifest').json()
    assert manifest['video_type']=='local_keyframe_not_ai_video'
    probe_download(c,j,tmp_path)


def test_missing_requires_explicit_reuse_and_full120(episode_client,tmp_path):
    c,data=episode_client;p=make(c,budget_cny=0);jid=seed_image(data,p)
    assert post_episode(c,p,[jid],scope='episode').status_code==409
    r=post_episode(c,p,[jid],scope='episode',allow_reuse=True)
    assert r.status_code==202
    j=c.get('/api/episodes/'+r.json()['id']).json()
    assert j['status']=='completed_local',j
    assert j['duration']==120 and j['reused_images']
    assert sum(s['reused'] for s in j['timeline'])==11
    assert j['api_cost_cny']==0
    probe_download(c,j,tmp_path)


def test_wrong_revision_missing_assets_and_duplicates(episode_client):
    c,data=episode_client;p=make(c);jid=seed_image(data,p,revision=2)
    assert post_episode(c,p,[jid]).status_code==409
    jid=seed_image(data,p)
    assert post_episode(c,p,[jid,jid]).status_code==422
    (data/'image-assets'/jid/'image.png').unlink()
    assert post_episode(c,p,[jid]).status_code==409
    assert post_episode(c,p,[]).status_code==422


def test_unavailable_voice_does_not_fake_success(episode_client,monkeypatch):
    from app import episodes
    c,data=episode_client;p=make(c);jid=seed_image(data,p)
    real=episodes.shutil.which
    monkeypatch.setattr(episodes.shutil,'which',lambda name:None if name=='espeak-ng' else real(name))
    assert not c.get('/api/local-production').json()['offline_voice_available']
    assert post_episode(c,p,[jid],voice_mode='offline_zh').status_code==503


def test_failed_composition_and_attempt_quota(episode_client,monkeypatch):
    c,data=episode_client;p=make(c);jid=seed_image(data,p)
    def fail(*a):raise RuntimeError('private subprocess detail')
    monkeypatch.setattr('app.episodes.ffmpeg',fail)
    monkeypatch.setenv('MAX_EPISODES','1')
    r=post_episode(c,p,[jid]);j=c.get('/api/episodes/'+r.json()['id']).json()
    assert j['status']=='failed' and 'private' not in j['error']
    assert c.get('/api/episodes/'+j['id']+'/download').status_code==409
    assert post_episode(c,p,[jid]).status_code==429


def test_subtitle_escape_and_recovery(tmp_path):
    cues=subtitle_cues('{\\an8}<b>云海</b>',10)
    path=tmp_path/'captions.srt';write_srt(cues,path)
    text=path.read_text();assert '{' not in text and '<b>' not in text
    recover_episodes(tmp_path)
    with sqlite3.connect(tmp_path/'episodes.sqlite3') as c:
        c.execute('INSERT INTO episodes VALUES(?,?)',('test',json.dumps({'id':'test','status':'running'})))
    recover_episodes(tmp_path)
    with sqlite3.connect(tmp_path/'episodes.sqlite3') as c:
        assert json.loads(c.execute('SELECT body FROM episodes').fetchone()[0])['status']=='failed'

def test_overlong_caption_and_cross_project_image(episode_client):
    c,data=episode_client;p=make(c);jid=seed_image(data,p)
    other=make(c)
    assert post_episode(c,other,[jid]).status_code==409
    e=editable(p);e['shots'][0]['narration']='字'*121
    p=c.put(f"/api/projects/{p['id']}/plan",json=e).json()
    jid=seed_image(data,p)
    assert post_episode(c,p,[jid]).status_code==422

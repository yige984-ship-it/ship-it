import json
import logging
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Literal
from .planner import plan, recalculate, STYLES
from .adapters import MockImage, MockVideo, MockVoice, run

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger('xiuxian')

Style = Literal['xianxia', 'ink', 'gufeng']

class ProjectInput(BaseModel):
    style: Style = 'xianxia'
    text: str = Field(min_length=1, max_length=20000)
    budget_cny: float = Field(default=150, gt=0, le=150, allow_inf_nan=False)

class CharacterEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    appearance: str = Field(min_length=1, max_length=1000)
    consistency: str = Field(min_length=1, max_length=1000)

class ShotEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int = Field(ge=1, le=12)
    duration: int = Field(ge=1, le=30, strict=True)
    strategy: Literal['keyframe', 'low_motion']
    narration: str = Field(max_length=20000)
    prompt: str = Field(min_length=1, max_length=4000)

class PlanEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    style: Style
    budget_cny: float = Field(gt=0, le=150, allow_inf_nan=False)
    characters: list[CharacterEdit] = Field(min_length=1, max_length=10)
    shots: list[ShotEdit] = Field(min_length=12, max_length=12)

    @model_validator(mode='after')
    def check_plan(self):
        if sorted(s.id for s in self.shots) != list(range(1, 13)):
            raise ValueError('镜头编号必须为1至12且不能重复')
        if sum(s.duration for s in self.shots) != 120:
            raise ValueError('每集镜头总时长必须为120秒')
        if len({c.id for c in self.characters}) != len(self.characters):
            raise ValueError('角色编号不能重复')
        return self

class RenderInput(BaseModel):
    mode: str = 'mock'


def create_app(data_dir=None):
    data = Path(data_dir or os.getenv('DATA_DIR', 'data')).resolve()
    data.mkdir(parents=True, exist_ok=True)
    db = data / 'studio.sqlite3'
    lock = threading.Lock()
    def conn():
        c = sqlite3.connect(db)
        c.execute('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        return c
    def save(p):
        with conn() as c:
            c.execute('INSERT OR REPLACE INTO projects VALUES(?, ?)', (p['id'], json.dumps(p, ensure_ascii=False)))
    def get(pid):
        with conn() as c:
            row = c.execute('SELECT body FROM projects WHERE id=?', (pid,)).fetchone()
        if not row:
            raise HTTPException(404, '项目不存在')
        return json.loads(row[0])
    @asynccontextmanager
    async def lifespan(app):
        with conn() as c:
            rows = c.execute('SELECT body FROM projects').fetchall()
        for row in rows:
            p = json.loads(row[0])
            if p['status'] == 'rendering_mock':
                p.update(status='failed', error='进程中断，请重新提交模拟合成')
                save(p)
        yield
    app = FastAPI(title='Xiuxian AI Studio', lifespan=lifespan)
    @app.middleware('http')
    async def size_limit(request, call_next):
        limit = int(os.getenv('MAX_REQUEST_BYTES', '100000'))
        if request.method in ('POST', 'PUT', 'PATCH'):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > limit:
                    return JSONResponse({'detail': '请求体超过大小限制'}, 413)
            request._body = bytes(body)
        return await call_next(request)
    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': 'mock', 'paid_calls_enabled': False}
    @app.post('/api/projects', status_code=201)
    def create(payload: ProjectInput):
        if not payload.text.strip() or len(payload.text) > int(os.getenv('MAX_TEXT_CHARS', '20000')):
            raise HTTPException(422, '小说为空或超过字数限制')
        with lock:
            with conn() as c:
                count = c.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            if count >= int(os.getenv('MAX_PROJECTS', '100')):
                raise HTTPException(429, '本地项目额度已用完')
            prices = json.loads(Path(os.getenv('PRICING_FILE', str(ROOT / 'config/pricing.json'))).read_text())
            for key in ('video_per_second', 'keyframe_per_image', 'voice_per_1000_chars', 'reserve_ratio'):
                if not isinstance(prices.get(key), (int, float)) or not 0 <= prices[key] < 100000:
                    raise HTTPException(503, '价格配置无效')
            p = plan(payload.text, prices, min(payload.budget_cny, float(os.getenv('BUDGET_CAP_CNY', '150'))))
            p.update(id=uuid.uuid4().hex, status='planned', mode='mock', attempts=0, error=None, revision=1, style=payload.style)
            for shot in p['shots']:
                shot['prompt'] = STYLES[payload.style] + '，' + shot['prompt']
            save(p)
        return p
    @app.get('/api/projects/{pid}')
    def project(pid: str):
        return get(pid)
    @app.put('/api/projects/{pid}/plan')
    def edit_plan(pid: str, payload: PlanEdit):
        with lock:
            p = get(pid)
            if p['status'] == 'rendering_mock':
                raise HTTPException(409, '生成中不能修改方案')
            if payload.revision != p.get('revision', 1):
                raise HTTPException(409, '方案已更新，请刷新后再编辑')
            previous = {shot['id']: shot for shot in p['shots']}
            p['shots'] = [dict(previous[shot.id], **shot.model_dump()) for shot in sorted(payload.shots, key=lambda s: s.id)]
            for shot in p['shots']:
                if payload.style != p.get('style', 'xianxia'):
                    for style_prefix in STYLES.values():
                        if shot['prompt'].startswith(style_prefix + '，'):
                            shot['prompt'] = shot['prompt'][len(style_prefix) + 1:]
                            break
                    shot['prompt'] = STYLES[payload.style] + '，' + shot['prompt']
                shot.update(image_task='planned_mock', video_task='planned_mock', voice_task='planned_mock')
            p['characters'] = [dict(c.model_dump(), source='人工编辑') for c in payload.characters]
            p.update(style=payload.style, budget_cny=min(payload.budget_cny, float(os.getenv('BUDGET_CAP_CNY', '150'))),
                     revision=p.get('revision', 1)+1, status='planned', error=None)
            p.pop('download_url', None)
            p.pop('preview_url', None)
            recalculate(p)
            save(p)
            return p
    @app.post('/api/projects/{pid}/render')
    def render(pid: str, payload: RenderInput):
        if payload.mode != 'mock' or os.getenv('PROVIDER_MODE', 'mock') != 'mock':
            raise HTTPException(409, 'MVP仅支持模拟模式，真实收费API未接入')
        with lock:
            p = get(pid)
            if not p['budget_ok']:
                raise HTTPException(409, '预算不足，禁止生成')
            if p['status'] == 'rendering_mock':
                raise HTTPException(409, '任务正在运行')
            if p['attempts'] >= int(os.getenv('MAX_RENDER_ATTEMPTS', '3')):
                raise HTTPException(429, '合成次数已达到额度')
            p.update(status='rendering_mock', attempts=p['attempts']+1, error=None)
            p.pop('preview_url', None)
            p.pop('download_url', None)
            save(p)
        # Synchronous MVP: separate worker/queue is a phase-two replacement point.
        folder = data / p['id']
        folder.mkdir(exist_ok=True)
        try:
            image = MockImage().generate('mock', folder / 'keyframe.png')
            for shot in p['shots']:
                MockVideo().generate(image, shot['duration'], folder / f"shot-{shot['id']}.mp4")
                shot.update(image_task='completed_mock', video_task='completed_mock')
                save(p)
            MockVoice().generate('', 120, folder / 'silence.wav')
            playlist = folder / 'concat.txt'
            playlist.write_text(''.join(f"file 'shot-{s['id']}.mp4'\n" for s in p['shots']))
            run(['-f', 'concat', '-safe', '0', '-i', str(playlist), '-i', str(folder / 'silence.wav'),
                 '-c:v', 'copy', '-c:a', 'aac', '-t', '120', '-movflags', '+faststart', str(folder / 'final.mp4')])
            for shot in p['shots']:
                shot['voice_task'] = 'completed_mock_silence'
            p.update(status='completed_mock', download_url=f"/api/projects/{pid}/download", preview_url=f"/api/projects/{pid}/preview")
        except Exception:
            log.exception('Mock composition failed project=%s', p['id'])
            p.update(status='failed', error='模拟合成失败，请检查服务器FFmpeg与日志')
        save(p)
        return p
    @app.get('/api/projects/{pid}/download')
    def download(pid: str):
        p = get(pid)
        if p['status'] != 'completed_mock':
            raise HTTPException(409, '尚无合成结果')
        return FileResponse(data / p['id'] / 'final.mp4', media_type='video/mp4', filename='xiuxian-SIMULATION.mp4')
    @app.get('/api/projects/{pid}/preview')
    def preview(pid: str):
        p = get(pid)
        if p['status'] != 'completed_mock':
            raise HTTPException(409, '尚无合成结果')
        path = data / p['id'] / 'final.mp4'
        if not path.is_file():
            raise HTTPException(404, '视频文件已丢失，请重新合成')
        return FileResponse(path, media_type='video/mp4',
                            content_disposition_type='inline')
    app.mount('/', StaticFiles(directory=ROOT / 'app/static', html=True), name='web')
    return app

app = create_app()

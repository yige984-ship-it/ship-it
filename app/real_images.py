"""Opt-in, single-image billing workflow. POST submissions are never retried."""
import json
import math
import os
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse
import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse
from .image_assets import persist_image
from .adapters import KeyframeAnimation
from pydantic import BaseModel, ConfigDict, Field
from .planner import money

ROOT = Path(__file__).resolve().parent.parent
BASE = 'https://api.atlascloud.ai/api/v1'

class AtlasImages:
    def submit(self, prompt, config):
        # No automatic retries: a timeout may still mean a billable task exists.
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            response = client.post(BASE + '/model/generateImage', headers=self.headers(), json={
                'model': config['model'], 'prompt': prompt,
                'size': config['size'], 'output_format': config['output_format']})
            response.raise_for_status()
            data = response.json()['data']
            if not isinstance(data.get('id'), str) or not data['id']:
                raise ValueError('Missing prediction id')
            return data['id']

    def poll(self, prediction):
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.get(BASE + '/model/prediction/' + quote(prediction, safe=''), headers=self.headers())
            response.raise_for_status()
            return response.json()['data']

    def headers(self):
        return {'Authorization': 'Bearer ' + os.environ['ATLASCLOUD_API_KEY']}

class QuoteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    shot_id: int = Field(ge=1, le=12)

class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: bool
    max_cost_cny: float = Field(gt=0, le=150, allow_inf_nan=False)


def install_image_routes(app, data, get_project, save_project, project_lock):
    db = data / 'images.sqlite3'
    def connect():
        c = sqlite3.connect(db)
        c.execute('CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        return c
    def save(job):
        with connect() as c:
            c.execute('INSERT OR REPLACE INTO images VALUES(?,?)', (job['id'], json.dumps(job, ensure_ascii=False)))
    def get(jid):
        with connect() as c:
            row = c.execute('SELECT body FROM images WHERE id=?', (jid,)).fetchone()
        if not row:
            raise HTTPException(404, '图片任务不存在')
        return json.loads(row[0])
    def all_jobs(pid):
        with connect() as c:
            return [j for row in c.execute('SELECT body FROM images') if (j := json.loads(row[0]))['project_id'] == pid]
    def settings():
        try:
            cfg = json.loads(Path(os.getenv('IMAGE_PROVIDER_FILE', str(ROOT / 'config/image-provider.json'))).read_text())
            price = cfg.get('price_per_image_cny')
            valid = (type(price) in (float, int) and math.isfinite(price) and 0 < price <= 150
                     and bool(cfg.get('price_source')) and bool(cfg.get('price_verified_at')))
            # This adapter intentionally supports only the schema verified in this release.
            valid = valid and cfg.get('model') == 'bytedance/seedream-v5.0-lite' and cfg.get('size') == '1600*2848' and cfg.get('output_format') == 'png'
        except (ValueError, OSError):
            return {}, '图片服务配置文件无法读取'
        if os.getenv('ENABLE_PAID_IMAGES', 'false').lower() != 'true':
            return cfg, '真实图片收费开关未开启'
        if not os.getenv('ATLASCLOUD_API_KEY'):
            return cfg, '服务器尚未配置图片服务密钥'
        if not valid:
            return cfg, '请配置已核实的人民币单图估价、来源与日期'
        return cfg, None
    def ready():
        cfg, reason = settings()
        if reason:
            raise HTTPException(503, reason)
        return cfg

    @app.get('/api/image-provider')
    def provider():
        cfg, reason = settings()
        return {'provider': 'atlas', 'ready': reason is None, 'reason': reason,
                'model': cfg.get('model'), 'price_per_image_cny': cfg.get('price_per_image_cny'),
                'price_source': cfg.get('price_source'), 'price_verified_at': cfg.get('price_verified_at'),
                'notice': '按张确认收费；此处为配置估价，非供应商实际账单。仅生成图片，视频与声音仍为模拟。'}

    @app.get('/api/projects/{pid}/images')
    def list_images(pid: str):
        p = get_project(pid)
        return {'jobs': all_jobs(pid), 'revision': p.get('revision', 1)}

    @app.post('/api/projects/{pid}/image-quotes', status_code=201)
    def image_quote(pid: str, payload: QuoteInput):
        cfg = ready()
        with project_lock:
            p = get_project(pid)
            if p.get('revision', 1) != payload.revision or p['status'] == 'rendering_mock':
                raise HTTPException(409, '方案已变更或正在合成，请刷新')
            with connect() as c:
                count = c.execute('SELECT COUNT(*) FROM images').fetchone()[0]
            if count >= int(os.getenv('MAX_IMAGE_JOBS', '100')):
                raise HTTPException(429, '图片任务额度已用完')
            amount = money(cfg['price_per_image_cny'])
            committed = sum(j['reserved_cny'] for j in all_jobs(pid))
            # Conservative: retain all past reservations, even failures/expired quotes.
            cap = min(p['budget_cny'], float(os.getenv('BUDGET_CAP_CNY', '150')), 150)
            if p['estimated_cny'] + committed + amount > cap:
                raise HTTPException(409, '预算不足：方案估算加历史图片预留及本次费用超过上限')
            shot = next(s for s in p['shots'] if s['id'] == payload.shot_id)
            characters = '\n'.join(f"{c['name']}：{c['appearance']}；{c['consistency']}" for c in p['characters'])
            job = dict(id=uuid.uuid4().hex, project_id=pid, revision=payload.revision,
                       shot_id=payload.shot_id, status='quoted', reserved_cny=amount,
                       config=cfg, prompt=shot['prompt'] + '\n角色设定：' + characters,
                       expires_at=time.time()+600, prediction_id=None, output_url=None,
                       actual_cost_cny=None, error=None)
            save(job)
        return job

    @app.post('/api/image-jobs/{jid}/submit')
    def submit(jid: str, payload: ConfirmInput):
        cfg = ready()
        with project_lock:
            job = get(jid)
            if job['status'] != 'quoted':
                raise HTTPException(409, '任务已提交或状态不明，禁止重复收费提交')
            p = get_project(job['project_id'])
            if p.get('revision', 1) != job['revision'] or p['status'] == 'rendering_mock':
                raise HTTPException(409, '方案已变更或正在合成，请重新获取报价')
            if job['expires_at'] < time.time() or cfg != job['config']:
                raise HTTPException(409, '报价过期或配置变更，请重新获取报价')
            if not payload.confirmed or money(payload.max_cost_cny) != job['reserved_cny']:
                raise HTTPException(422, '需要确认当前单张费用')
            cap = min(p['budget_cny'], float(os.getenv('BUDGET_CAP_CNY', '150')), 150)
            if p['estimated_cny'] + sum(j['reserved_cny'] for j in all_jobs(p['id'])) > cap:
                raise HTTPException(409, '预算上限已改变，禁止提交')
            # Persist before network I/O; a restart cannot trigger another POST.
            job['status'] = 'submitting'
            # Real submission means total spend is unknown until a provider bill exists.
            p['actual_api_cost_cny'] = None
            save_project(p)
            save(job)
        try:
            prediction = AtlasImages().submit(job['prompt'], cfg)
            job.update(status='processing', prediction_id=prediction)
        except Exception:
            # Do not log provider response or exception text: they may contain secrets.
            job.update(status='submission_unknown', error='提交结果不明，可能已计费；禁止自动重试，请核对供应商后台')
        save(job)
        return job

    @app.get('/api/image-jobs/{jid}')
    def image_job(jid: str):
        return get(jid)

    def asset_path(job):
        return data / 'image-assets' / job['id'] / 'image.png'

    @app.post('/api/image-jobs/{jid}/persist')
    def persist(jid: str):
        with project_lock:
            job = get(jid)
            if job['status'] != 'completed_real_image':
                raise HTTPException(409, '真实图片尚未生成完成')
            target = asset_path(job)
            if job.get('asset_url') and target.is_file():
                return job
            try:
                metadata = persist_image(job['output_url'], target)
            except Exception:
                # Keep the remote result: storage errors must never resubmit generation.
                job['asset_error'] = '图片保存失败，请检查下载域名白名单、文件格式和大小；可重试保存，不会重新计费'
                save(job)
                raise HTTPException(502, job['asset_error'])
            job.update(asset_url=f'/api/image-jobs/{jid}/asset',
                       asset_download_url=f'/api/image-jobs/{jid}/asset/download',
                       asset_metadata=metadata, asset_error=None)
            save(job)
            return job

    @app.get('/api/image-jobs/{jid}/asset')
    def asset(jid: str):
        job = get(jid)
        path = asset_path(job)
        if not job.get('asset_url') or not path.is_file():
            raise HTTPException(404, '本地图片素材不存在，请先保存')
        return FileResponse(path, media_type='image/png')

    @app.get('/api/image-jobs/{jid}/asset/download')
    def asset_download(jid: str):
        job = get(jid)
        path = asset_path(job)
        if not job.get('asset_url') or not path.is_file():
            raise HTTPException(404, '本地图片素材不存在，请先保存')
        return FileResponse(path, media_type='image/png', filename=f'xiuxian-shot-{job["shot_id"]}.png')

    @app.post('/api/image-jobs/{jid}/animate')
    def animate(jid: str):
        with project_lock:
            job = get(jid)
            image = asset_path(job)
            if not job.get('asset_url') or not image.is_file():
                raise HTTPException(409, '请先将真实图片保存为本地素材')
            output = image.parent / 'keyframe-preview.mp4'
            if not output.is_file():
                temporary = image.parent / 'keyframe-preview.part.mp4'
                try:
                    KeyframeAnimation().generate(image, temporary)
                    temporary.replace(output)
                except Exception:
                    temporary.unlink(missing_ok=True)
                    raise HTTPException(502, '关键帧动画合成失败，请检查FFmpeg')
            job.update(keyframe_preview_url=f'/api/image-jobs/{jid}/animation',
                       animation_type='local_keyframe_not_ai_video', animation_duration=5)
            save(job)
            return job

    @app.get('/api/image-jobs/{jid}/animation')
    def animation(jid: str):
        job = get(jid)
        output = asset_path(job).parent / 'keyframe-preview.mp4'
        if not job.get('keyframe_preview_url') or not output.is_file():
            raise HTTPException(404, '动画预览尚未合成')
        return FileResponse(output, media_type='video/mp4', content_disposition_type='inline')

    @app.post('/api/image-jobs/{jid}/refresh')
    def refresh(jid: str):
        if not os.getenv('ATLASCLOUD_API_KEY'):
            raise HTTPException(503, '服务器尚未配置图片服务密钥')
        with project_lock:
            job = get(jid)
            if job['status'] != 'processing':
                return job
            try:
                result = AtlasImages().poll(job['prediction_id'])
                status = result.get('status')
                if status in ('completed', 'succeeded'):
                    outputs = result.get('outputs', [])
                    url = outputs[0] if isinstance(outputs, list) and outputs else ''
                    parsed = urlparse(url)
                    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                        raise ValueError('Invalid output URL')
                    job.update(status='completed_real_image', output_url=url, error=None)
                elif status == 'failed':
                    job.update(status='failed', error='供应商图片任务失败；预留保留，实际费用请核对账单')
                else:
                    job['error'] = None
            except Exception:
                job['error'] = '状态查询失败，未重新提交收费请求；可稍后刷新'
            save(job)
        return job

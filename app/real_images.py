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
from .image_assets import persist_image, AssetDownloadError
from .adapters import KeyframeAnimation
from .episodes import install_episode_routes
from pydantic import BaseModel, ConfigDict, Field
from .planner import money
from .image_providers import SiliconImages, AliyunImages

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
    provider_id: str | None = Field(default=None, max_length=80)
    revision: int = Field(ge=1)
    shot_id: int = Field(ge=1, le=12)

class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: bool
    max_cost_cny: float = Field(ge=0, le=150, allow_inf_nan=False)


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
    def profiles():
        return json.loads((ROOT / 'config/image-providers.json').read_text())
    def key_name(cfg):
        return {'atlas':'ATLASCLOUD_API_KEY','siliconflow':'SILICONFLOW_API_KEY','aliyun':'DASHSCOPE_API_KEY'}[cfg['provider']]
    def adapter(cfg):
        return {'atlas':AtlasImages,'siliconflow':SiliconImages,'aliyun':AliyunImages}[cfg['provider']]()
    def settings(provider_id=None):
        try:
            if provider_id is None and os.getenv('IMAGE_PROVIDER_FILE'):
                cfg=json.loads(Path(os.environ['IMAGE_PROVIDER_FILE']).read_text())
            elif provider_id == 'atlas':
                cfg=json.loads((ROOT / 'config/image-provider.json').read_text())
            else:
                cfg=next((p for p in profiles() if p['id']==(provider_id or 'kolors-free')), None)
                if cfg is None: return {}, '未知图片服务选项'
            price=cfg.get('price_per_image_cny')
            valid=(type(price) in (int,float) and math.isfinite(price) and 0 <= price <= 150
                   and bool(cfg.get('price_source')) and bool(cfg.get('price_verified_at')))
            provider=cfg.get('provider')
            if provider=='atlas':
                valid=valid and price != 0 and cfg.get('model')=='bytedance/seedream-v5.0-lite' and cfg.get('size')=='1600*2848' and cfg.get('output_format')=='png'
            elif provider=='siliconflow':
                valid=valid and cfg.get('model') in ('Kwai-Kolors/Kolors','Tongyi-MAI/Z-Image-Turbo') and cfg.get('image_size')=={'Kwai-Kolors/Kolors':'1024x1024','Tongyi-MAI/Z-Image-Turbo':'576x1024'}.get(cfg.get('model'))
                if price==0: valid=valid and cfg.get('model')=='Kwai-Kolors/Kolors'
            elif provider=='aliyun':
                valid=valid and price != 0 and cfg.get('model')=='wanx2.0-t2i-turbo' and cfg.get('size')=='768*1344'
            else: return {}, '不支持的图片供应商'
        except (ValueError,OSError,TypeError):
            return {}, '图片服务配置文件无法读取'
        if not valid: return cfg, '请配置已核实的人民币单图估价、来源与日期及有效模型参数'
        if price>0 and os.getenv('EPHEMERAL_STORAGE','false').lower()=='true':
            return cfg, '临时磁盘部署仅允许免费试用，收费服务需要持久化存储'
        enabled='ENABLE_FREE_IMAGES' if price==0 else 'ENABLE_PAID_IMAGES'
        if os.getenv(enabled,'true' if price==0 else 'false').lower()!='true':
            return cfg, '免费图片开关未开启' if price==0 else '真实图片收费开关未开启'
        if not os.getenv(key_name(cfg)):
            return cfg, '服务器尚未配置图片服务密钥：'+key_name(cfg)
        return cfg,None
    def ready(provider_id=None):
        cfg,reason=settings(provider_id)
        if reason: raise HTTPException(503,reason)
        return cfg
    def summary(provider_id=None):
        cfg,reason=settings(provider_id)
        return dict(provider_id=provider_id or cfg.get('id','atlas'), provider=cfg.get('provider'),
                    ready=reason is None,reason=reason,model=cfg.get('model'),
                    price_per_image_cny=cfg.get('price_per_image_cny'),price_source=cfg.get('price_source'),
                    price_verified_at=cfg.get('price_verified_at'),
                    notice='默认免费优先，不自动切换付费模型；配置估价非实际账单。视频与声音仍为模拟。')
    @app.get('/api/image-provider')
    def provider(provider_id: str | None=None):
        return summary(provider_id)
    @app.get('/api/image-providers')
    def providers():
        items=[summary(p['id']) for p in profiles()]+[summary('atlas')]
        items.sort(key=lambda p: float('inf') if p['price_per_image_cny'] is None else p['price_per_image_cny'])
        return {'default_provider_id':'kolors-free','automatic_paid_fallback':False,'providers':items}

    @app.get('/api/projects/{pid}/images')
    def list_images(pid: str):
        p = get_project(pid)
        return {'jobs': all_jobs(pid), 'revision': p.get('revision', 1)}

    @app.post('/api/projects/{pid}/image-quotes', status_code=201)
    def image_quote(pid: str, payload: QuoteInput):
        cfg = ready(payload.provider_id)
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
            if amount > 0 and p['estimated_cny'] + committed + amount > cap:
                raise HTTPException(409, '预算不足：方案估算加历史图片预留及本次费用超过上限')
            shot = next(s for s in p['shots'] if s['id'] == payload.shot_id)
            characters = '\n'.join(f"{c['name']}：{c['appearance']}；{c['consistency']}" for c in p['characters'])
            job = dict(id=uuid.uuid4().hex, project_id=pid, revision=payload.revision,
                       shot_id=payload.shot_id, status='quoted', reserved_cny=amount,
                       config=cfg, provider_id=payload.provider_id, prompt=shot['prompt'] + '\n角色设定：' + characters,
                       expires_at=time.time()+600, prediction_id=None, output_url=None,
                       actual_cost_cny=None, error=None)
            save(job)
        return job

    @app.post('/api/image-jobs/{jid}/submit')
    def submit(jid: str, payload: ConfirmInput):
        initial = get(jid)
        cfg = ready(initial.get('provider_id'))
        selected_adapter = adapter(cfg)
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
            if job['reserved_cny'] > 0 and p['estimated_cny'] + sum(j['reserved_cny'] for j in all_jobs(p['id'])) > cap:
                raise HTTPException(409, '预算上限已改变，禁止提交')
            if len(job['prompt']) > cfg.get('prompt_limit', 12000):
                raise HTTPException(422, '提示词与角色设定合计超过当前模型限制，请精简方案后重新报价')
            if hasattr(selected_adapter,'preflight'):
                try:
                    selected_adapter.preflight(cfg)
                except Exception:
                    raise HTTPException(503, '图片价格或账户模型可用性未能核实，未提交生成；不会切换收费服务')
            # Persist before network I/O; a restart cannot trigger another POST.
            job['status'] = 'submitting'
            # Real submission means total spend is unknown until a provider bill exists.
            p['actual_api_cost_cny'] = None
            save_project(p)
            save(job)
        try:
            prediction = selected_adapter.submit(job['prompt'], cfg)
            if isinstance(prediction,dict):
                url=prediction['outputs'][0]
                parsed=urlparse(url)
                if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('Invalid output URL')
                job.update(status='completed_real_image', output_url=url)
            else:
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
            except Exception as error:
                # Only trusted validation messages/status codes are shown; never provider bodies.
                if isinstance(error, AssetDownloadError):
                    reason = str(error)
                elif isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (403, 404, 410):
                    reason = '供应商临时图片链接可能已过期或拒绝访问'
                else:
                    reason = '图片下载或校验失败，请稍后重试'
                job['asset_error'] = '保存未成功：' + reason + '。重试保存不会重新提交图片生成。'
                if not target.is_file():
                    job.pop('asset_url', None)
                    job.pop('asset_download_url', None)
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
        with project_lock:
            job = get(jid)
            if not os.getenv(key_name(job['config'])):
                raise HTTPException(503, '服务器尚未配置对应图片服务密钥')
            if job['status'] != 'processing':
                return job
            try:
                result = adapter(job['config']).poll(job['prediction_id'])
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

    install_episode_routes(app, data, get_project, get, asset_path, project_lock)

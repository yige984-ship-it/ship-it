"""Local still-image timeline, subtitles and optional offline mechanical Mandarin."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import textwrap
import time
import uuid
import wave
from typing import Literal
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from .adapters import MockVoice

class EpisodeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    image_job_ids: list[str] = Field(min_length=1, max_length=12)
    scope: Literal['selected', 'episode'] = 'selected'
    allow_reuse: bool = False
    voice_mode: Literal['silent', 'offline_zh'] = 'silent'


def connection(data):
    c=sqlite3.connect(data / 'episodes.sqlite3')
    c.execute('CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    return c


def recover_episodes(data):
    with connection(data) as c:
        for eid, body in c.execute('SELECT id,body FROM episodes').fetchall():
            job=json.loads(body)
            if job['status'] in ('queued','running'):
                job.update(status='failed',error='服务重启中断本地合成，请检查素材后重新制作')
                c.execute('UPDATE episodes SET body=? WHERE id=?',(json.dumps(job,ensure_ascii=False),eid))


def ffmpeg(args, folder):
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args],cwd=folder,
                   capture_output=True,check=True,timeout=180)


def timestamp(seconds):
    ms=round(seconds*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def subtitle_cues(text, duration):
    # Display text, never interpret author-provided HTML/ASS formatting.
    safe=text.replace('\\','／').replace('{','｛').replace('}','｝').replace('<','＜').replace('>','＞')
    safe=' '.join(safe.split())
    chunks=[safe[i:i+36] for i in range(0,len(safe),36)]
    return [(i*duration/len(chunks),(i+1)*duration/len(chunks),'\n'.join(textwrap.wrap(chunk,18)))
            for i,chunk in enumerate(chunks)]


def write_srt(cues,path):
    path.write_text(''.join(f'{i+1}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n\n'
                           for i,(start,end,text) in enumerate(cues)),encoding='utf-8')


class OfflineMandarin:
    """eSpeak NG mechanical Mandarin. No API, no natural-voice claim."""
    def generate(self,text,duration,output):
        executable=shutil.which('espeak-ng')
        if not executable: raise ValueError('服务器未安装离线中文配音，请选择静音或安装eSpeak NG')
        folder=output.parent
        source=output.with_suffix('.txt');source.write_text(text,encoding='utf-8')
        raw=output.with_suffix('.raw.wav')
        subprocess.run([executable,'-v','cmn','-s','175','-f',str(source),'-w',str(raw)],
                       check=True,capture_output=True,timeout=40)
        with wave.open(str(raw),'rb') as audio:
            length=audio.getnframes()/audio.getframerate()
        if length > duration*1.3:
            raise ValueError('旁白过长，离线声音无法在镜头内自然读完，请缩短旁白或调整镜头时长')
        tempo=max(1.0,length/duration)
        ffmpeg(['-i',str(raw),'-af',f'atempo={tempo:.6f},apad','-t',str(duration),
                '-ar','24000','-ac','1',str(output)],folder)
        return output


def install_episode_routes(app,data,get_project,get_image,asset_path,project_lock):
    def save(job):
        with connection(data) as c:
            c.execute('INSERT OR REPLACE INTO episodes VALUES(?,?)',(job['id'],json.dumps(job,ensure_ascii=False)))
    def get(eid):
        with connection(data) as c:
            row=c.execute('SELECT body FROM episodes WHERE id=?',(eid,)).fetchone()
        if not row: raise HTTPException(404,'本地剪辑任务不存在')
        return json.loads(row[0])
    def jobs(pid=None):
        with connection(data) as c:
            result=[json.loads(row[0]) for row in c.execute('SELECT body FROM episodes')]
        return [j for j in result if pid is None or j['project_id']==pid]
    def folder(job):
        return data / 'episodes' / job['id']

    def compose(eid):
        job=get(eid);work=folder(job);work.mkdir(parents=True,exist_ok=True)
        job['status']='running';save(job)
        try:
            full_cues=[];offset=0
            for index,shot in enumerate(job['timeline']):
                duration=shot['duration'];text=shot['narration']
                cues=subtitle_cues(text,duration)
                write_srt(cues,work/f'subtitle-{index}.srt')
                full_cues.extend((a+offset,b+offset,t) for a,b,t in cues);offset+=duration
                audio=work/f'voice-{index}.wav'
                if job['voice_mode']=='offline_zh' and text.strip():
                    OfflineMandarin().generate(text,duration,audio)
                else:
                    MockVoice().generate('',duration,audio)
                filters=("scale=360:640:force_original_aspect_ratio=increase,crop=360:640,"
                         f"zoompan=z='min(zoom+0.0006,1.08)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={duration*12}:s=360x640:fps=12,"
                         "drawtext=text='LOCAL KEYFRAME - NO AI VIDEO':fontcolor=white:fontsize=14:x=(w-tw)/2:y=30")
                if cues:
                    filters+=f",subtitles=subtitle-{index}.srt:force_style='FontName=Noto Sans CJK SC,FontSize=12,MarginV=25,Outline=1'"
                image=asset_path(get_image(shot['image_job_id']))
                ffmpeg(['-i',str(image),'-i',str(audio),'-vf',filters,'-t',str(duration),
                        '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-threads','1',
                        '-c:a','aac',str(work/f'clip-{index}.mp4')],work)
                job['completed_shots']=index+1;save(job)
            write_srt(full_cues,work/'subtitles.srt')
            (work/'concat.txt').write_text(''.join(f"file 'clip-{i}.mp4'\n" for i in range(len(job['timeline']))))
            ffmpeg(['-f','concat','-safe','0','-i','concat.txt','-c','copy','-movflags','+faststart','final.part.mp4'],work)
            result=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(work/'final.part.mp4')],timeout=20))
            if abs(float(result['format']['duration'])-job['duration'])>.25 or {s['codec_type'] for s in result['streams']}!={'video','audio'}:
                raise ValueError('成片时长或音视频轨道验证失败')
            (work/'final.part.mp4').replace(work/'final.mp4')
            job.update(status='completed_local',error=None,
                       preview_url=f'/api/episodes/{eid}/preview',download_url=f'/api/episodes/{eid}/download',
                       subtitles_url=f'/api/episodes/{eid}/subtitles')
            (work/'manifest.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))
        except Exception as error:
            message=str(error) if isinstance(error,ValueError) else '本地剪辑失败，请检查FFmpeg、字幕字体和素材；未调用收费服务'
            job.update(status='failed',error=message)
        save(job)

    @app.get('/api/local-production')
    def capabilities():
        return {'ffmpeg':bool(shutil.which('ffmpeg')),'offline_voice_available':bool(shutil.which('espeak-ng')),
                'voice_label':'离线机械中文声（eSpeak NG），不是自然人声或云端AI配音',
                'api_cost_cny':0,'video_type':'local_keyframe_not_ai_video'}

    @app.get('/api/projects/{pid}/episodes')
    def list_episodes(pid:str):
        get_project(pid)
        return {'jobs':jobs(pid)}

    @app.post('/api/projects/{pid}/episodes',status_code=202)
    def create_episode(pid:str,payload:EpisodeInput,background:BackgroundTasks):
        with project_lock:
            project=get_project(pid)
            if payload.revision!=project.get('revision',1): raise HTTPException(409,'方案已更新，请刷新后制作')
            if payload.voice_mode=='offline_zh' and not shutil.which('espeak-ng'):
                raise HTTPException(503,'离线中文配音未安装，请选择静音字幕模式')
            if len(set(payload.image_job_ids))!=len(payload.image_job_ids): raise HTTPException(422,'图片任务不能重复选择')
            selected={}
            for jid in payload.image_job_ids:
                image=get_image(jid)
                if image['project_id']!=pid or image['revision']!=payload.revision:
                    raise HTTPException(409,'图片不属于当前项目/方案版本，请使用当前版本素材')
                if not image.get('asset_url') or not asset_path(image).is_file():
                    raise HTTPException(409,'所选图片尚未保存到项目，请先保存')
                if image['shot_id'] in selected: raise HTTPException(422,'每个镜头只能选择一张图片')
                selected[image['shot_id']]=image
            requested=project['shots'] if payload.scope=='episode' else [s for s in project['shots'] if s['id'] in selected]
            missing=[s['id'] for s in requested if s['id'] not in selected]
            if missing and not payload.allow_reuse:
                raise HTTPException(409,'缺少镜头图片：'+','.join(map(str,missing))+'；请补图，或明确勾选允许复用现有图片')
            timeline=[]
            for shot in requested:
                image=selected.get(shot['id'])
                if image is None:
                    previous=[sid for sid in selected if sid<shot['id']]
                    image=selected[max(previous) if previous else min(selected)]
                if len(shot['narration']) > shot['duration']*12:
                    raise HTTPException(422,f"镜头{shot['id']}字幕/旁白过长，请先缩短文字")
                timeline.append(dict(shot_id=shot['id'],duration=shot['duration'],narration=shot['narration'],
                                     image_job_id=image['id'],reused=image['shot_id']!=shot['id']))
            if payload.voice_mode=='offline_zh' and not any(s['narration'].strip() for s in timeline):
                raise HTTPException(422,'所选镜头没有旁白文字，请先填写旁白或选择静音模式')
            existing=jobs()
            if any(j['status'] in ('queued','running') for j in existing):
                raise HTTPException(409,'已有本地剪辑任务正在运行，请稍后再试')
            if len(jobs(pid))>=int(os.getenv('MAX_EPISODES','10')): raise HTTPException(429,'本项目剪辑次数已达上限')
            eid=uuid.uuid4().hex
            job=dict(id=eid,project_id=pid,revision=payload.revision,status='queued',timeline=timeline,
                     duration=sum(s['duration'] for s in timeline),voice_mode=payload.voice_mode,
                     scope=payload.scope,completed_shots=0,created_at=time.time(),error=None,
                     voice_label='离线机械中文声（eSpeak NG）' if payload.voice_mode=='offline_zh' else '静音',
                     voiced_shots=sum(bool(s['narration'].strip()) for s in timeline) if payload.voice_mode=='offline_zh' else 0,
                     reused_images=any(s['reused'] for s in timeline),api_cost_cny=0,
                     cost_notice='仅本地剪辑无模型费用，不包含此前图片、服务器、存储和流量费用',
                     video_type='local_keyframe_not_ai_video')
            save(job)
            background.add_task(compose,eid)
            return job

    @app.get('/api/episodes/{eid}')
    def episode(eid:str):
        return get(eid)

    def result_file(eid,name):
        job=get(eid)
        path=folder(job)/name
        if job['status']!='completed_local' or not path.is_file(): raise HTTPException(409,'剪辑尚未完成或文件已丢失')
        return path

    @app.get('/api/episodes/{eid}/preview')
    def preview(eid:str):
        return FileResponse(result_file(eid,'final.mp4'),media_type='video/mp4')
    @app.get('/api/episodes/{eid}/download')
    def download(eid:str):
        return FileResponse(result_file(eid,'final.mp4'),media_type='video/mp4',filename='xiuxian-local-keyframes.mp4')
    @app.get('/api/episodes/{eid}/subtitles')
    def subtitles(eid:str):
        return FileResponse(result_file(eid,'subtitles.srt'),media_type='text/plain',filename='xiuxian-subtitles.srt')
    @app.get('/api/episodes/{eid}/manifest')
    def manifest(eid:str):
        return FileResponse(result_file(eid,'manifest.json'),media_type='application/json',filename='xiuxian-timeline.json')

"""Source-attributed v23 chat transcription. Original JSON/assets not verified."""
import json
from pathlib import Path
from .tiangong import CharacterBible, create_pilot_manifest, validate_manifest


def production_manifest():
    source = json.loads((Path(__file__).resolve().parent.parent / 'config/tiangong/storyboard-production-v4.json').read_text())
    m = create_pilot_manifest([CharacterBible(**c) for c in source['characters']])
    m['storyboard_version'] = source['version']
    m['reference_requirements'] = source['reference_requirements']
    for shot, original in zip(m['shots'], source['shots'], strict=True):
        if shot['id'] != original['id']:
            raise ValueError('storyboard ids mismatch')
        shot.update(original)
        shot['character_versions'] = {cid: 1 for cid in shot['character_ids']}
    validate_manifest(m)
    return m


def v23_manifest():
    source = json.loads((Path(__file__).resolve().parent.parent / 'config/tiangong/storyboard-v23.json').read_text())
    m = create_pilot_manifest([CharacterBible(**source['character'])])
    m['storyboard_version'] = 23
    m['storyboard_source'] = source['source']
    m['source_review_notes'] = ['从聊天展示文本转录，未取得第23版原始JSON和关键帧文件',
                              '另有本地90s-review-v1版本，人物服饰及镜头次序不同，未混入本版']
    for shot, original in zip(m['shots'], source['shots'], strict=True):
        if shot['id'] != original['id']:
            raise ValueError('storyboard ids mismatch')
        shot.update(original)
        shot['character_ids'] = ['shen_zhixian']
        shot['character_versions'] = {'shen_zhixian': 1}
        shot['prompt'] = '电影级3D国漫仙侠，竖屏9:16，5秒。画面：'+original['scene']+' 运镜：'+original['camera']+' 动作：'+original['action']
        shot['unresolved_characters'] = [name for name in ('王老头','摊主','青衣修士','灰袍执事','周有余','许管事','黑袍人影','灯中人影','无面人影') if name in original['scene']+original['action']]
    validate_manifest(m)
    return m

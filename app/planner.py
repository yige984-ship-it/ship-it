import re
from decimal import Decimal, ROUND_UP

def money(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_UP))

def plan(text, prices, budget):
    sentences = [s.strip() for s in re.split(r'(?<=[。！？!?])|\n+', text) if s.strip()]
    # Group the complete input across twelve slots, preserving source order.
    groups = [[] for _ in range(12)]
    for i, sentence in enumerate(sentences):
        groups[min(11, i * 12 // len(sentences))].append(sentence)
    shots = []
    for i, group in enumerate(groups):
        excerpt = ''.join(group) or '环境空镜：山门云海，延续上一镜情绪。'
        kind = 'low_motion' if i % 4 == 3 else 'keyframe'
        visual = money(10 * prices['video_per_second'] if kind == 'low_motion' else prices['keyframe_per_image'])
        voice = money(len(''.join(group)) / 1000 * prices['voice_per_1000_chars'])
        shots.append(dict(id=i+1, duration=10, strategy=kind, narration=''.join(group),
                          source=excerpt, prompt=f'中国修仙国漫，竖屏9:16，统一青金色调，角色遵循角色档案，低动作，{excerpt}',
                          image_task='planned_mock', video_task='planned_mock', voice_task='planned_mock',
                          estimated_cny=money(visual + voice)))
    subtotal = money(sum(s['estimated_cny'] for s in shots))
    total = money(subtotal * (1 + prices['reserve_ratio']))
    return dict(duration=120, planner='local_rules_v1',
                warning='规则分镜草案，需人工审稿；未调用大模型，未生成真实人物、视频或配音。',
                characters=[dict(id='lead', name='主角（待人工命名）', appearance='青年修士，青色道袍，黑发束冠',
                                 consistency='固定服饰、发型和面部；真实生成前需制作参考图', source='默认模板，非小说角色识别')],
                shots=shots, pricing=prices, subtotal_cny=subtotal, estimated_cny=total,
                budget_cny=budget, budget_ok=total <= budget, actual_api_cost_cny=0,
                budget_message='估算在预算内；不是供应商报价或成本保证' if total <= budget else '预算不足：请减少动态镜头或调整配置，禁止提交生成')

STYLES = {'xianxia': '仙侠国漫，青金色调', 'ink': '中国水墨，留白意境', 'gufeng': '古风工笔，细腻设色'}

def recalculate(project):
    prices = project['pricing']
    for shot in project['shots']:
        visual = money(shot['duration'] * prices['video_per_second'] if shot['strategy'] == 'low_motion' else prices['keyframe_per_image'])
        voice = money(len(shot['narration']) / 1000 * prices['voice_per_1000_chars'])
        shot['estimated_cny'] = money(visual + voice)
    project['duration'] = sum(s['duration'] for s in project['shots'])
    project['subtotal_cny'] = money(sum(s['estimated_cny'] for s in project['shots']))
    project['estimated_cny'] = money(project['subtotal_cny'] * (1 + prices['reserve_ratio']))
    project['budget_ok'] = project['estimated_cny'] <= project['budget_cny']
    project['budget_message'] = ('估算在预算内；不是供应商报价或成本保证' if project['budget_ok'] else '预算不足：请减少动态镜头或调整配置，禁止提交生成')
    return project

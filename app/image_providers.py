"""Explicit provider selection. Never fall back to a different model/provider."""
import re
from html.parser import HTMLParser
import os
from urllib.parse import quote
import httpx

class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip=0; self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip+=1
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
    def handle_data(self, text):
        if not self.skip: self.parts.append(text)

def verify_free_price():
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response=client.get('https://www.siliconflow.cn/pricing')
        response.raise_for_status()
    parser=VisibleText(); parser.feed(response.text)
    text=' '.join(' '.join(parser.parts).split())
    if not re.search(r'Kwai-Kolors/Kolors\s+生成图片\s+每图片\s+免费(?:\s|$)', text):
        raise ValueError('无法从官方页面确认Kolors仍然免费，禁止提交')

class SiliconImages:
    base='https://api.siliconflow.cn/v1'
    def headers(self):
        return {'Authorization':'Bearer '+os.environ['SILICONFLOW_API_KEY']}
    def preflight(self, config):
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response=client.get(self.base+'/models',headers=self.headers())
            response.raise_for_status()
            models=response.json()['data']
            if not any(m.get('id')==config['model'] for m in models):
                raise ValueError('当前账户模型列表中没有所选模型')
        if config['price_per_image_cny']==0:
            verify_free_price()
    def submit(self, prompt, config):
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            response=client.post(self.base+'/images/generations',headers=self.headers(),json={
                'model':config['model'],'prompt':prompt, 'image_size':config['image_size']})
            response.raise_for_status()
            return {'status':'completed', 'outputs':[response.json()['images'][0]['url']]}

class AliyunImages:
    # Beijing legacy endpoint remains supported according to the official documentation.
    base='https://dashscope.aliyuncs.com/api/v1'
    def headers(self):
        return {'Authorization':'Bearer '+os.environ['DASHSCOPE_API_KEY']}
    def submit(self, prompt, config):
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            response=client.post(self.base+'/services/aigc/text2image/image-synthesis',
                headers=dict(self.headers(), **{'X-DashScope-Async':'enable'}), json={
                    'model':config['model'], 'input':{'prompt':prompt},
                    'parameters':{'size':config['size'],'n':1,'prompt_extend':False}})
            response.raise_for_status()
            prediction=response.json()['output']['task_id']
            if not isinstance(prediction,str) or not prediction: raise ValueError('Missing task id')
            return prediction
    def poll(self, prediction):
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response=client.get(self.base+'/tasks/'+quote(prediction,safe=''),headers=self.headers())
            response.raise_for_status()
            output=response.json()['output']
            status=output['task_status']
            return {'status':{'SUCCEEDED':'completed','FAILED':'failed','CANCELED':'failed'}.get(status,'processing'),
                    'outputs':[r['url'] for r in output.get('results',[]) if r.get('url')]}

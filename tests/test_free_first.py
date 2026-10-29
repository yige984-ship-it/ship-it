import httpx
import pytest
from test_studio import client, make
from app.image_providers import SiliconImages, AliyunImages, verify_free_price

@pytest.fixture
def free_setup(monkeypatch):
    monkeypatch.delenv('IMAGE_PROVIDER_FILE',raising=False)
    monkeypatch.setenv('SILICONFLOW_API_KEY','test-silicon-key')
    monkeypatch.setenv('ENABLE_FREE_IMAGES','true')
    monkeypatch.setenv('ENABLE_PAID_IMAGES','false')
    monkeypatch.setattr(SiliconImages,'preflight',lambda *a:None)

def free_quote(client,p,provider='kolors-free'):
    return client.post(f"/api/projects/{p['id']}/image-quotes",json={'revision':1,'shot_id':1,'provider_id':provider})

def test_free_is_default_and_sorted(client,free_setup):
    data=client.get('/api/image-providers').json()
    assert data['default_provider_id']=='kolors-free'
    assert not data['automatic_paid_fallback']
    assert [p['price_per_image_cny'] for p in data['providers'][:3]]==[0,0.04,0.1]
    assert client.get('/api/image-provider').json()['model']=='Kwai-Kolors/Kolors'
    assert client.get('/api/image-provider').json()['ready']
    p=make(client)
    assert free_quote(client,p,'wanx-budget').status_code==503

def test_free_submit(client,free_setup,monkeypatch):
    calls=[]
    def submit(self,prompt,cfg):
        calls.append(cfg['model'])
        return {'status':'completed','outputs':['https://cdn.example.com/real.png']}
    monkeypatch.setattr(SiliconImages,'submit',submit)
    p=make(client);q=free_quote(client,p).json()
    assert q['reserved_cny']==0
    url=f"/api/image-jobs/{q['id']}/submit"
    assert client.post(url,json={'confirmed':False,'max_cost_cny':0}).status_code==422
    r=client.post(url,json={'confirmed':True,'max_cost_cny':0}).json()
    assert r['status']=='completed_real_image'
    assert calls==['Kwai-Kolors/Kolors']
    assert client.post(url,json={'confirmed':True,'max_cost_cny':0}).status_code==409

def test_preflight_failure_does_not_generate(client,free_setup,monkeypatch):
    def fail(*a):raise ValueError('model unavailable or no longer free')
    monkeypatch.setattr(SiliconImages,'preflight',fail)
    def never(*a):pytest.fail('No generation or fallback allowed')
    monkeypatch.setattr(SiliconImages,'submit',never)
    monkeypatch.setattr(AliyunImages,'submit',never)
    p=make(client);q=free_quote(client,p).json()
    r=client.post(f"/api/image-jobs/{q['id']}/submit",json={'confirmed':True,'max_cost_cny':0})
    assert r.status_code==503
    assert client.get(f"/api/image-jobs/{q['id']}").json()['status']=='quoted'

def test_free_timeout_no_paid_fallback(client,free_setup,monkeypatch):
    def fail(*a):raise httpx.ReadTimeout('maybe completed')
    monkeypatch.setattr(SiliconImages,'submit',fail)
    monkeypatch.setattr(AliyunImages,'submit',lambda *a:pytest.fail('Paid fallback forbidden'))
    p=make(client);q=free_quote(client,p).json();url=f"/api/image-jobs/{q['id']}/submit"
    assert client.post(url,json={'confirmed':True,'max_cost_cny':0}).json()['status']=='submission_unknown'
    assert client.post(url,json={'confirmed':True,'max_cost_cny':0}).status_code==409

def test_free_price_parser(monkeypatch):
    actual=httpx.Client
    for text,works in [('<p>Kwai-Kolors/Kolors</p><p>生成图片</p><p>每图片</p><p>免费</p>',True),
                       ('Kwai-Kolors/Kolors 生成图片 每图片 ¥ 0.10 免费试用',False),
                       ('<script>Kwai-Kolors/Kolors 生成图片 每图片 免费</script>',False)]:
        monkeypatch.setattr('app.image_providers.httpx.Client',lambda **kw:actual(transport=httpx.MockTransport(lambda req:httpx.Response(200,text=text)),**kw))
        if works:verify_free_price()
        else:
            with pytest.raises(ValueError):verify_free_price()

def test_silicon_http_contract(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','test')
    actual=httpx.Client;calls=[]
    def handler(req):
        calls.append(req)
        if req.url.path=='/v1/models':return httpx.Response(200,json={'data':[{'id':'Kwai-Kolors/Kolors'}]})
        if req.method=='GET':return httpx.Response(200,text='Kwai-Kolors/Kolors 生成图片 每图片 免费')
        import json
        body=json.loads(req.content)
        assert body=={'model':'Kwai-Kolors/Kolors','prompt':'国风','image_size':'1024x1024'}
        return httpx.Response(200,json={'images':[{'url':'https://cdn.example.com/a.png'}]})
    monkeypatch.setattr('app.image_providers.httpx.Client',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    cfg={'model':'Kwai-Kolors/Kolors','price_per_image_cny':0,'image_size':'1024x1024'}
    a=SiliconImages();a.preflight(cfg)
    assert a.submit('国风',cfg)['outputs']==['https://cdn.example.com/a.png']
    assert len(calls)==3

def test_aliyun_contract(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','test')
    actual=httpx.Client
    def handler(req):
        if req.method=='POST':
            import json
            body=json.loads(req.content)
            assert req.headers['X-DashScope-Async']=='enable'
            assert body['parameters']=={'size':'768*1344','n':1,'prompt_extend':False}
            return httpx.Response(200,json={'output':{'task_id':'task1'}})
        return httpx.Response(200,json={'output':{'task_status':'SUCCEEDED','results':[{'url':'https://cdn.example.com/b.png'}]}})
    monkeypatch.setattr('app.image_providers.httpx.Client',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    a=AliyunImages();assert a.submit('国风',{'model':'wanx2.0-t2i-turbo','size':'768*1344'})=='task1'
    assert a.poll('task1')['status']=='completed'

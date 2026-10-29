import json
import httpx
import pytest
from app.real_images import AtlasImages
from test_studio import client, make, editable

@pytest.fixture
def configured(monkeypatch, tmp_path):
    cfg = json.loads(open('config/image-provider.json').read())
    cfg.update(price_per_image_cny=1.25, price_source='TEST ONLY', price_verified_at='2026-10-29')
    path = tmp_path / 'image.json'
    path.write_text(json.dumps(cfg))
    monkeypatch.setenv('IMAGE_PROVIDER_FILE', str(path))
    monkeypatch.setenv('ENABLE_PAID_IMAGES', 'true')
    monkeypatch.setenv('ATLASCLOUD_API_KEY', 'unit-test-not-a-real-key')
    return cfg

def quote(client, p):
    return client.post(f"/api/projects/{p['id']}/image-quotes", json={'revision':p.get('revision', 1),'shot_id':1})

def test_disabled_no_key_and_unpriced(client, monkeypatch, configured):
    monkeypatch.setenv('ENABLE_PAID_IMAGES', 'false')
    p = make(client)
    assert not client.get('/api/image-provider').json()['ready']
    assert quote(client,p).status_code == 503
    monkeypatch.setenv('ENABLE_PAID_IMAGES','true')
    monkeypatch.delenv('ATLASCLOUD_API_KEY')
    assert quote(client,p).status_code == 503
    monkeypatch.setenv('ATLASCLOUD_API_KEY','test')
    monkeypatch.setenv('IMAGE_PROVIDER_FILE','config/image-provider.json')
    assert quote(client,p).status_code == 503

def test_confirmation_and_no_duplicate(client, configured, monkeypatch):
    calls=[]
    def submit(self,prompt,cfg):
        calls.append(prompt)
        return 'prediction-1'
    monkeypatch.setattr(AtlasImages,'submit',submit)
    monkeypatch.setattr(AtlasImages,'poll',lambda *a: {'status':'completed','outputs':['https://cdn.example.com/image.png']})
    p=make(client)
    q=quote(client,p).json()
    assert not calls
    url=f"/api/image-jobs/{q['id']}"
    assert client.post(url+'/submit',json={'confirmed':False,'max_cost_cny':1.25}).status_code == 422
    assert client.post(url+'/submit',json={'confirmed':True,'max_cost_cny':1}).status_code == 422
    r=client.post(url+'/submit',json={'confirmed':True,'max_cost_cny':1.25})
    assert r.json()['status']=='processing'
    assert len(calls)==1 and '角色设定' in calls[0]
    assert client.get(f"/api/projects/{p['id']}").json()['actual_api_cost_cny'] is None
    assert client.post(url+'/submit',json={'confirmed':True,'max_cost_cny':1.25}).status_code==409
    result=client.post(url+'/refresh').json()
    assert result['status']=='completed_real_image'
    assert result['actual_cost_cny'] is None
    assert 'unit-test-not-a-real-key' not in json.dumps(result)

def test_unknown_submission_never_retried(client, configured, monkeypatch):
    calls=[]
    def fail(*args):
        calls.append(1)
        raise httpx.ReadTimeout('do not leak provider secret')
    monkeypatch.setattr(AtlasImages,'submit',fail)
    p=make(client);q=quote(client,p).json();url=f"/api/image-jobs/{q['id']}/submit"
    args={'confirmed':True,'max_cost_cny':1.25}
    r=client.post(url,json=args).json()
    assert r['status']=='submission_unknown'
    assert 'secret' not in r['error']
    assert client.post(url,json=args).status_code==409
    assert len(calls)==1

def test_budget_and_revision(client, configured):
    p=make(client,budget_cny=0.01)
    assert quote(client,p).status_code==409
    p=make(client)
    q=quote(client,p).json()
    e=editable(p)
    client.put(f"/api/projects/{p['id']}/plan",json=e)
    assert client.post(f"/api/image-jobs/{q['id']}/submit",json={'confirmed':True,'max_cost_cny':1.25}).status_code==409
    assert quote(client,p).status_code==409

def test_atlas_http_contract(monkeypatch, configured):
    requests=[]
    def handler(req):
        requests.append(req)
        if req.method=='POST':
            assert req.url.path=='/api/v1/model/generateImage'
            body=json.loads(req.content)
            assert body['size']==configured['size'] and body['prompt']=='国风山水'
            return httpx.Response(200,json={'data':{'id':'prediction1'}})
        assert req.url.path=='/api/v1/model/prediction/prediction1'
        return httpx.Response(200,json={'data':{'status':'processing'}})
    real_client=httpx.Client
    monkeypatch.setattr('app.real_images.httpx.Client',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    a=AtlasImages()
    assert a.submit('国风山水',configured)=='prediction1'
    assert a.poll('prediction1')['status']=='processing'
    assert len(requests)==2

def test_price_changes_and_quota(client, configured, monkeypatch):
    p=make(client)
    q=quote(client,p).json()
    monkeypatch.setenv('MAX_IMAGE_JOBS','1')
    assert quote(client,p).status_code==429
    path=__import__('os').environ['IMAGE_PROVIDER_FILE']
    config=dict(configured,price_per_image_cny=2)
    open(path,'w').write(json.dumps(config))
    assert client.post(f"/api/image-jobs/{q['id']}/submit",json={'confirmed':True,'max_cost_cny':1.25}).status_code==409

def test_refresh_failure_does_not_resubmit(client, configured, monkeypatch):
    monkeypatch.setattr(AtlasImages,'submit',lambda *a:'known-id')
    def fail(*a): raise httpx.ReadTimeout('sensitive provider error')
    monkeypatch.setattr(AtlasImages,'poll',fail)
    p=make(client);q=quote(client,p).json();url=f"/api/image-jobs/{q['id']}"
    client.post(url+'/submit',json={'confirmed':True,'max_cost_cny':1.25})
    j=client.post(url+'/refresh').json()
    assert j['status']=='processing'
    assert 'sensitive' not in j['error']
    assert j['prediction_id']=='known-id'

import io
import json
import socket
import subprocess
import httpx
import pytest
from PIL import Image
from app import image_assets
from app.real_images import AtlasImages
from test_studio import client, make
from test_real_images import configured, quote

@pytest.fixture
def image_bytes():
    b=io.BytesIO()
    Image.new('RGB',(360,640),(35,80,95)).save(b,format='PNG')
    return b.getvalue()

@pytest.fixture
def download_transport(monkeypatch,image_bytes,request):
    monkeypatch.setenv('IMAGE_DOWNLOAD_HOSTS','cdn.example.com')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    actual=httpx.Client
    calls=[]
    content_type=getattr(request,'param','image/png')
    def handler(request):
        calls.append(request)
        assert 'authorization' not in request.headers
        return httpx.Response(200,headers={} if content_type is None else {'content-type':content_type},content=image_bytes)
    monkeypatch.setattr(image_assets.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    return calls

def test_download_normalizes_image(tmp_path, download_transport):
    dest=tmp_path/'image.png'
    metadata=image_assets.persist_image('https://cdn.example.com/test.png',dest)
    assert metadata['width']==360 and metadata['height']==640
    assert len(download_transport)==1
    with Image.open(dest) as im:
        assert im.format=='PNG'

def test_blocked_addresses(monkeypatch):
    monkeypatch.setenv('IMAGE_DOWNLOAD_HOSTS','cdn.example.com,localhost')
    for url in ('http://cdn.example.com/a','https://evil.example/a','https://user:pass@cdn.example.com/a','https://cdn.example.com:8443/a'):
        with pytest.raises(ValueError): image_assets.checked_url(url)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError): image_assets.checked_url('https://localhost/a')

def test_reject_non_image_and_oversize(tmp_path,monkeypatch,image_bytes):
    monkeypatch.setattr(image_assets,'checked_url',lambda url:url)
    actual=httpx.Client
    for content_type,content in [('text/html',b'<html>'),('image/png',b'not really png'),('image/png',image_bytes)]:
        monkeypatch.setattr(image_assets,'MAX_BYTES',10 if content==image_bytes else 1024)
        monkeypatch.setattr(image_assets.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':content_type},content=content)),**kw))
        with pytest.raises(Exception): image_assets.persist_image('https://cdn.example.com/a',tmp_path/'image.png')
        assert not (tmp_path/'image.png').exists()

@pytest.mark.parametrize('download_transport',['image/png','application/octet-stream'],indirect=True)
def test_saved_image_and_animation(client,configured,monkeypatch,download_transport,tmp_path):
    monkeypatch.setattr(AtlasImages,'submit',lambda *a:'id1')
    monkeypatch.setattr(AtlasImages,'poll',lambda *a:{'status':'completed','outputs':['https://cdn.example.com/image.png']})
    p=make(client);q=quote(client,p).json();url=f"/api/image-jobs/{q['id']}"
    assert client.post(url+'/persist').status_code==409
    client.post(url+'/submit',json={'confirmed':True,'max_cost_cny':1.25})
    client.post(url+'/refresh')
    job=client.post(url+'/persist').json()
    assert job['asset_metadata']['width']==360
    assert len(download_transport)==1
    client.post(url+'/persist')
    assert len(download_transport)==1
    assert client.get(job['asset_url']).headers['content-type']=='image/png'
    assert 'attachment' in client.get(job['asset_download_url']).headers['content-disposition']
    result=client.post(url+'/animate')
    assert result.status_code==200,result.text
    job=result.json()
    assert job['animation_type']=='local_keyframe_not_ai_video'
    response=client.get(job['keyframe_preview_url'])
    path=tmp_path/'preview.mp4';path.write_bytes(response.content)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)]))
    assert abs(float(probe['format']['duration'])-5)<0.2
    assert {s['codec_type'] for s in probe['streams']}=={'video','audio'}
    assert client.get(job['keyframe_preview_url'],headers={'Range':'bytes=0-127'}).status_code==206
    assert len(download_transport)==1

def test_storage_failure_keeps_generated_result(client,configured,monkeypatch):
    calls=[]
    monkeypatch.setattr(AtlasImages,'submit',lambda *a:calls.append(1) or 'id1')
    monkeypatch.setattr(AtlasImages,'poll',lambda *a:{'status':'completed','outputs':['https://cdn.example.com/image.png']})
    monkeypatch.setenv('IMAGE_DOWNLOAD_HOSTS','')
    p=make(client);q=quote(client,p).json();url=f"/api/image-jobs/{q['id']}"
    client.post(url+'/submit',json={'confirmed':True,'max_cost_cny':1.25})
    client.post(url+'/refresh')
    assert client.post(url+'/persist').status_code==502
    job=client.get(url).json()
    assert job['status']=='completed_real_image'
    assert job['output_url'] and job['asset_error']
    assert len(calls)==1


def test_redirect_not_followed(tmp_path,monkeypatch):
    monkeypatch.setattr(image_assets,'checked_url',lambda url:url)
    actual=httpx.Client
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    monkeypatch.setattr(image_assets.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(httpx.HTTPStatusError):
        image_assets.persist_image('https://cdn.example.com/a',tmp_path/'image.png')
    assert len(calls)==1
    assert not (tmp_path/'image.png').exists()

def test_default_silicon_cdn_is_exact_and_overridable(monkeypatch):
    monkeypatch.delenv('IMAGE_DOWNLOAD_HOSTS',raising=False)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    assert image_assets.checked_url('https://s3.siliconflow.cn/temporary/image.png')
    for url in ('https://evil.s3.siliconflow.cn/a','https://s3.siliconflow.cn.evil.example/a','http://s3.siliconflow.cn/a'):
        with pytest.raises(ValueError):image_assets.checked_url(url)
    monkeypatch.setenv('IMAGE_DOWNLOAD_HOSTS','')
    with pytest.raises(ValueError):image_assets.checked_url('https://s3.siliconflow.cn/a')


@pytest.mark.parametrize('download_transport',['application/octet-stream','binary/octet-stream',None,'text/plain'],indirect=True)
def test_image_bytes_accepted_despite_cdn_mime(tmp_path,download_transport):
    path=tmp_path/'saved.png'
    metadata=image_assets.persist_image('https://cdn.example.com/image.png',path)
    assert metadata['width']==360 and metadata['height']==640
    with Image.open(path) as decoded:
        assert decoded.format=='PNG'
        decoded.load()

@pytest.mark.parametrize('content_type',['application/octet-stream','image/png',None])
def test_invalid_bytes_rejected_regardless_of_mime(tmp_path,monkeypatch,content_type):
    monkeypatch.setattr(image_assets,'checked_url',lambda url:url)
    actual=httpx.Client
    headers={} if content_type is None else {'content-type':content_type}
    monkeypatch.setattr(image_assets.httpx,'Client',lambda **kw:actual(transport=httpx.MockTransport(lambda req:httpx.Response(200,headers=headers,content=b'<html>expired or invalid content</html>')),**kw))
    with pytest.raises(image_assets.AssetDownloadError,match='不是有效'):
        image_assets.persist_image('https://cdn.example.com/a',tmp_path/'image.png')
    assert not (tmp_path/'image.png').exists()

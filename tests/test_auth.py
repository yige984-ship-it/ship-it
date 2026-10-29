import pytest
from fastapi.testclient import TestClient
from app.main import create_app

@pytest.fixture
def secured(tmp_path,monkeypatch):
    monkeypatch.setenv('REQUIRE_AUTH','true')
    monkeypatch.setenv('STUDIO_ACCESS_PASSWORD','unit-test-password-123')
    with TestClient(create_app(tmp_path),base_url='https://testserver') as client:
        yield client

def test_login_protects_generation_and_files(secured):
    assert secured.get('/api/health').status_code==200
    assert secured.get('/').status_code==200
    for url in ('/api/image-providers','/api/projects/anything/download','/api/image-jobs/anything/asset','/docs','/openapi.json'):
        assert secured.get(url).status_code==401
    assert secured.post('/api/projects',json={'text':'国风山水'}).status_code==401
    r=secured.post('/api/session',json={'password':'unit-test-password-123'})
    assert r.status_code==200
    cookie=r.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie
    assert secured.get('/api/image-providers').status_code==200
    assert secured.get('/api/session').json()['authenticated']
    assert secured.delete('/api/session').status_code==200
    assert secured.get('/api/image-providers').status_code==401

def test_missing_password_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('REQUIRE_AUTH','true')
    monkeypatch.delenv('STUDIO_ACCESS_PASSWORD',raising=False)
    with TestClient(create_app(tmp_path)) as c:
        assert c.get('/api/health').status_code==200
        assert not c.get('/api/session').json()['configured']
        assert c.post('/api/session',json={'password':'x'}).status_code==503
        assert c.get('/api/image-providers').status_code==503

def test_failed_login_rate_limit(secured):
    for _ in range(5):
        assert secured.post('/api/session',json={'password':'wrong'}).status_code==401
    assert secured.post('/api/session',json={'password':'unit-test-password-123'}).status_code==429

def test_forged_cookie(secured):
    secured.cookies.set('studio_session','forged')
    assert not secured.get('/api/session').json()['authenticated']
    assert secured.get('/api/image-providers').status_code==401

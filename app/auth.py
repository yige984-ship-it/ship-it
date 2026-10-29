"""Single-user deployment gate. Password only comes from server environment."""
import hashlib
import hmac
import os
import secrets
import threading
import time
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

class LoginInput(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def install_auth(app):
    required = os.getenv('REQUIRE_AUTH', 'false').lower() == 'true'
    password = os.getenv('STUDIO_ACCESS_PASSWORD', '')
    configured = len(password) >= 12
    cookie_secure = os.getenv('COOKIE_SECURE', 'true').lower() == 'true'
    sessions = {}
    failures = []
    lock = threading.Lock()
    def session_key(request):
        token = request.cookies.get('studio_session', '')
        return hashlib.sha256(token.encode()).hexdigest() if token else None
    def authenticated(request):
        if not required:
            return True
        key = session_key(request)
        with lock:
            return bool(configured and key and sessions.get(key, 0) > time.time())

    @app.middleware('http')
    async def protect_api(request: Request, call_next):
        public = request.url.path in ('/api/health', '/api/session')
        protected = request.url.path.startswith('/api/') or request.url.path in ('/docs', '/redoc', '/openapi.json')
        if required and protected and not public:
            if not configured:
                return JSONResponse({'detail': '服务器登录密码未配置或不足12位，接口保持锁定'}, 503)
            if not authenticated(request):
                return JSONResponse({'detail': '请先登录工作室'}, 401)
        return await call_next(request)

    @app.get('/api/session')
    def session(request: Request):
        return {'auth_required': required, 'configured': configured if required else True,
                'authenticated': authenticated(request),
                'storage_ephemeral': os.getenv('EPHEMERAL_STORAGE','false').lower()=='true'}

    @app.post('/api/session')
    def login(payload: LoginInput, request: Request):
        if not required:
            return {'authenticated': True}
        if not configured:
            raise HTTPException(503, '请在服务器配置至少12位STUDIO_ACCESS_PASSWORD')
        now = time.time()
        with lock:
            failures[:] = [t for t in failures if t > now - 60]
            if len(failures) >= 5:
                raise HTTPException(429, '登录尝试过多，请一分钟后再试')
            if not hmac.compare_digest(payload.password.encode(), password.encode()):
                failures.append(now)
                raise HTTPException(401, '工作室密码不正确')
            for key, expiry in list(sessions.items()):
                if expiry <= now:
                    sessions.pop(key, None)
            # At most five active browser sessions for this single-user application.
            if len(sessions) >= 5:
                sessions.pop(next(iter(sessions)))
            token = secrets.token_urlsafe(32)
            sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 8 * 3600
        response = JSONResponse({'authenticated': True})
        response.set_cookie('studio_session', token, httponly=True, secure=cookie_secure,
                            samesite='strict', max_age=8*3600, path='/')
        return response

    @app.delete('/api/session')
    def logout(request: Request):
        with lock:
            sessions.pop(session_key(request), None)
        response = JSONResponse({'authenticated': False if required else True})
        response.delete_cookie('studio_session', path='/', secure=cookie_secure, httponly=True, samesite='strict')
        return response

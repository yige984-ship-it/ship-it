"""Bounded, allowlisted image ingestion; no provider credentials sent to image hosts."""
import io
import ipaddress
import os
import socket
import time
import warnings
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from PIL import Image

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def checked_url(url):
    parsed = urlsplit(url)
    allowed = {h.strip().lower() for h in os.getenv('IMAGE_DOWNLOAD_HOSTS', '').split(',') if h.strip()}
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.hostname.lower() not in allowed):
        raise ValueError('图片地址不在管理员配置的HTTPS下载域名白名单中')
    answers = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
        raise ValueError('禁止访问内部或保留网络地址')
    return url


def persist_image(url: str, destination: Path):
    checked_url(url)
    content = bytearray()
    deadline = time.monotonic() + 60
    # Independent client; never forward Atlas Authorization headers or redirects.
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        with client.stream('GET', url) as response:
            response.raise_for_status()
            if response.headers.get('content-type', '').split(';')[0].lower() not in ('image/png', 'image/jpeg', 'image/webp'):
                raise ValueError('响应不是支持的图片格式')
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    raise TimeoutError('图片下载超过总时限')
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError('图片超过20MB限制')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in ('PNG', 'JPEG', 'WEBP') or original.width * original.height > MAX_PIXELS:
                raise ValueError('图片格式或像素数超限')
            original.load()
            normalized = original.convert('RGB')
            # Strip remote metadata and normalize to a single PNG frame.
            output = io.BytesIO()
            normalized.save(output, format='PNG')
            if output.tell() > MAX_BYTES:
                raise ValueError('标准化后图片超过20MB限制')
            metadata = {'width': normalized.width, 'height': normalized.height,
                        'bytes': output.tell(), 'format': 'PNG'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.part')
    try:
        temporary.write_bytes(output.getvalue())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return metadata

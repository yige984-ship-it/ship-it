"""Persistent Tiangong pilot asset API. No video generation or paid calls."""
from io import BytesIO
from PIL import Image, UnidentifiedImageError
import os
import base64
import hashlib
import uuid
import json
import sqlite3
import threading
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
from .tiangong import CharacterBible, create_pilot_manifest, validate_manifest

class CharacterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    character_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    face: str = Field(min_length=1, max_length=1000)
    hair: str = Field(min_length=1, max_length=1000)
    costume: str = Field(min_length=1, max_length=1000)
    left_hand_ring: str = Field(min_length=1, max_length=1000)
    waist_token: str = Field(min_length=1, max_length=1000)
    shoes: str = Field(min_length=1, max_length=1000)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=20)

class AssetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    content_base64: str = Field(min_length=1, max_length=80000)

class ShotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(max_length=4000)
    character_ids: list[str] = Field(default_factory=list, max_length=20)

def install_tiangong_routes(app, data_dir):
    router = APIRouter(prefix="/api/tiangong", tags=["tiangong"])
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    db = Path(data_dir) / "studio.sqlite3"
    lock = threading.RLock()
    def connection():
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE IF NOT EXISTS tiangong_manifests (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        return c
    def load():
        with connection() as c:
            row = c.execute("SELECT body FROM tiangong_manifests WHERE id='pilot'").fetchone()
        if row is None:
            raise HTTPException(404, "请先初始化试点项目")
        return json.loads(row[0])
    def save(m):
        validate_manifest(m)
        with connection() as c:
            c.execute("INSERT OR REPLACE INTO tiangong_manifests (id,body) VALUES ('pilot',?)",
                      (json.dumps(m, ensure_ascii=False),))
        return m
    @router.post("/pilot", status_code=201)
    def initialize(character: CharacterPayload):
        with lock:
            with connection() as c:
                if c.execute("SELECT 1 FROM tiangong_manifests WHERE id='pilot'").fetchone():
                    raise HTTPException(409, "试点项目已存在，禁止覆盖")
            if character.reference_asset_ids:
                raise HTTPException(422, "初始化后再上传并绑定参考素材")
            return save(create_pilot_manifest([CharacterBible(**character.model_dump())]))
    @router.get("/pilot")
    def get_pilot():
        return load()
    @router.put("/pilot/characters/{character_id}")
    def update_character(character_id: str, payload: CharacterPayload):
        if character_id != payload.character_id:
            raise HTTPException(422, "角色ID不可变更")
        with lock:
            m = load()
            if set(payload.reference_asset_ids) - {a["id"] for a in m.get("assets", [])}:
                raise HTTPException(422, "参考素材不存在")
            for i, c in enumerate(m["characters"]):
                if c["character_id"] == character_id:
                    updated = dict(c, **payload.model_dump(), version=c["version"] + 1)
                    CharacterBible(**updated).validate()
                    m["characters"][i] = updated
                    for shot in m["shots"]:
                        if character_id in shot["character_ids"]:
                            shot.update(status="draft", approved=False, video_asset_id=None, provider_job_id=None, character_versions={})
                    return save(m)
            raise HTTPException(404, "角色不存在")
    @router.put("/pilot/shots/{shot_id}")
    def update_shot(shot_id: int, payload: ShotPayload):
        if not 1 <= shot_id <= 18:
            raise HTTPException(404, "镜头不存在")
        with lock:
            m = load()
            unknown = set(payload.character_ids) - {c["character_id"] for c in m["characters"]}
            if unknown:
                raise HTTPException(422, "引用了未知角色")
            s = m["shots"][shot_id - 1]
            s["reference_asset_ids"] = sorted({a for c in m["characters"] if c["character_id"] in payload.character_ids for a in c.get("reference_asset_ids", [])})
            s.update(prompt=payload.prompt, character_ids=payload.character_ids,
                     status="draft", approved=False, video_asset_id=None, provider_job_id=None, character_versions={})
            s["character_versions"] = {c["character_id"]: c["version"] for c in m["characters"] if c["character_id"] in payload.character_ids}
            return save(m)
    @router.get("/storage")
    def storage():
        return {"ephemeral": os.getenv("EPHEMERAL_STORAGE", "false").lower() == "true",
                "paid_api_enabled": False, "budget_cny": 140}

    @router.post("/pilot/assets", status_code=201)
    def upload_asset(payload: AssetPayload):
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except ValueError:
            raise HTTPException(422, "素材编码无效")
        if not content or len(content) > 60000:
            raise HTTPException(422, "素材须为1至60000字节；大文件上传尚未开放")
        with lock:
            m = load()
            asset_id = uuid.uuid4().hex
            folder = Path(data_dir) / "tiangong_assets"
            folder.mkdir(exist_ok=True)
            (folder / asset_id).write_bytes(content)
            asset = {"id": asset_id, "name": payload.name, "sha256": hashlib.sha256(content).hexdigest(),
                     "size": len(content), "kind": "reference", "provenance": "user_upload_unverified"}
            m.setdefault("assets", []).append(asset)
            save(m)
            return asset

    @router.post("/pilot/assets/image", status_code=201)
    async def upload_image(request: Request, name: str = "reference"):
        content = await request.body()
        if not content or len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "参考图片须为1字节至10 MB")
        if not name.strip() or len(name) > 100:
            raise HTTPException(422, "素材名称须为1至100字")
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP") or image.width * image.height > 25_000_000:
                    raise HTTPException(422, "仅支持2500万像素以内PNG、JPEG、WebP")
                media_type = Image.MIME[image.format]
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(422, "图片文件损坏或格式无效")
        with lock:
            m = load()
            if len(m.get("assets", [])) >= 100:
                raise HTTPException(409, "试点素材上限100个")
            asset_id = uuid.uuid4().hex
            folder = Path(data_dir) / "tiangong_assets"
            folder.mkdir(exist_ok=True)
            path = folder / asset_id
            path.write_bytes(content)
            asset = {"id": asset_id, "name": name.strip(), "sha256": hashlib.sha256(content).hexdigest(),
                     "size": len(content), "kind": "reference", "media_type": media_type,
                     "provenance": "user_upload_unverified"}
            m.setdefault("assets", []).append(asset)
            try:
                save(m)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return asset

    @router.get("/pilot/assets/{asset_id}")
    def download_asset(asset_id: str):
        m = load()
        asset = next((a for a in m.get("assets", []) if a["id"] == asset_id), None)
        if asset is None:
            raise HTTPException(404, "素材不存在")
        path = Path(data_dir) / "tiangong_assets" / asset["id"]
        if not path.is_file():
            raise HTTPException(404, "素材文件已丢失")
        return FileResponse(path, media_type="application/octet-stream", filename="reference.bin")

    @app.get("/tiangong")
    def workbench():
        return FileResponse(Path(__file__).parent / "static" / "tiangong.html")

    app.include_router(router)

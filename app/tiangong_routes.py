"""Persistent Tiangong pilot asset API. No video generation or paid calls."""
import json
import sqlite3
import threading
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from .tiangong import CharacterBible, create_pilot_manifest, validate_manifest

class CharacterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    face: str = Field(min_length=1, max_length=1000)
    hair: str = Field(min_length=1, max_length=1000)
    costume: str = Field(min_length=1, max_length=1000)
    left_hand_ring: str = Field(min_length=1, max_length=1000)
    waist_token: str = Field(min_length=1, max_length=1000)
    shoes: str = Field(min_length=1, max_length=1000)

class ShotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(max_length=4000)
    character_ids: list[str] = Field(default_factory=list, max_length=20)

def install_tiangong_routes(app, data_dir):
    router = APIRouter(prefix="/api/tiangong", tags=["tiangong"])
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
            for i, c in enumerate(m["characters"]):
                if c["character_id"] == character_id:
                    updated = dict(c, **payload.model_dump(), version=c["version"] + 1)
                    CharacterBible(**updated).validate()
                    m["characters"][i] = updated
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
            s.update(prompt=payload.prompt, character_ids=payload.character_ids,
                     status="draft", approved=False, video_asset_id=None, provider_job_id=None)
            return save(m)
    app.include_router(router)

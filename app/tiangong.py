"""Tiangong phase-one manifest and consistency validation. No paid API calls."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Any
import json

REQUIRED_LOCKS = ("face", "hair", "costume", "left_hand_ring", "waist_token", "shoes")
PILOT_SHOTS = 18
PILOT_SECONDS = 90

@dataclass(frozen=True)
class CharacterBible:
    character_id: str
    name: str
    face: str
    hair: str
    costume: str
    left_hand_ring: str
    waist_token: str
    shoes: str
    reference_asset_ids: tuple[str, ...] = ()
    version: int = 1

    def validate(self) -> None:
        if not self.character_id.strip() or not self.name.strip():
            raise ValueError("character id and name required")
        for field in REQUIRED_LOCKS:
            if not getattr(self, field).strip():
                raise ValueError(f"missing character lock: {field}")
        if self.version < 1:
            raise ValueError("version must be positive")

def create_pilot_manifest(characters: list[CharacterBible], *, budget_cny: str = "140") -> dict[str, Any]:
    if not characters:
        raise ValueError("at least one character required")
    ids = [c.character_id for c in characters]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate character id")
    for character in characters:
        character.validate()
    budget = Decimal(budget_cny)
    if not budget.is_finite() or budget < 0 or budget > 140:
        raise ValueError("pilot budget must be between 0 and 140 CNY")
    shots = [
        {"id": i, "duration_s": 5, "character_ids": [], "prompt": "",
         "reference_asset_ids": [], "status": "draft", "approved": False,
         "video_asset_id": None, "provider_job_id": None}
        for i in range(1, PILOT_SHOTS + 1)
    ]
    return {
        "schema_version": 1, "project": "镇天执灯人",
        "production_mode": "cinematic_quality_first",
        "aspect_ratio": "9:16", "duration_s": PILOT_SECONDS,
        "budget_cny": str(budget), "paid_api_enabled": False,
        "characters": [asdict(c) for c in characters], "shots": shots,
        "render_mode": "not_started",
    }

def validate_manifest(manifest: dict[str, Any], *, final_export: bool = False) -> None:
    budget = Decimal(str(manifest["budget_cny"]))
    if not budget.is_finite() or not 0 <= budget <= 140:
        raise ValueError("pilot budget must be between 0 and 140 CNY")
    for c in manifest["characters"]:
        CharacterBible(**c).validate()
    shots = manifest["shots"]
    if len(shots) != PILOT_SHOTS or [s["id"] for s in shots] != list(range(1, PILOT_SHOTS + 1)):
        raise ValueError("pilot requires exactly 18 sequential shots")
    if sum(s["duration_s"] for s in shots) != PILOT_SECONDS or any(s["duration_s"] != 5 for s in shots):
        raise ValueError("pilot requires 18 five-second shots")
    ids = {c["character_id"] for c in manifest["characters"]}
    if any(set(s["character_ids"]) - ids for s in shots):
        raise ValueError("unknown character reference")
    if manifest["paid_api_enabled"]:
        raise ValueError("phase one must not enable paid API")
    if final_export and any(not (s["approved"] and s["video_asset_id"] and s["provider_job_id"]) for s in shots):
        raise ValueError("all shots require approved real video assets and provider provenance")

def manifest_json(manifest: dict[str, Any]) -> str:
    validate_manifest(manifest)
    return json.dumps(manifest, ensure_ascii=False, indent=2)

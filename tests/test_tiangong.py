import pytest
from app.tiangong import CharacterBible, create_pilot_manifest, validate_manifest, manifest_json

def sample():
    return CharacterBible("lead", "待按原著核对", "固定面部参考", "固定发型", "固定衣装", "左手戒指固定", "腰牌固定", "纹饰鞋固定")

def test_manifest_pilot():
    m = create_pilot_manifest([sample()])
    validate_manifest(m)
    assert len(m["shots"]) == 18
    assert sum(s["duration_s"] for s in m["shots"]) == 90
    assert m["budget_cny"] == "140"
    assert not m["paid_api_enabled"]
    assert "镇天执灯人" in manifest_json(m)

def test_character_lock_required():
    with pytest.raises(ValueError, match="left_hand_ring"):
        create_pilot_manifest([CharacterBible("lead", "主角", "脸", "发", "衣", "", "腰", "鞋")])

def test_reject_invalid_budget():
    for value in ("141", "-1", "NaN"):
        with pytest.raises(ValueError):
            create_pilot_manifest([sample()], budget_cny=value)

def test_reject_missing_provenance():
    m = create_pilot_manifest([sample()])
    with pytest.raises(ValueError, match="approved real video"):
        validate_manifest(m, final_export=True)

def test_reject_wrong_duration_and_unknown_character():
    m = create_pilot_manifest([sample()])
    m["shots"][0]["duration_s"] = 6
    with pytest.raises(ValueError, match="five-second"):
        validate_manifest(m)
    m["shots"][0]["duration_s"] = 5
    m["shots"][0]["character_ids"] = ["nonexistent"]
    with pytest.raises(ValueError, match="unknown"):
        validate_manifest(m)

def test_reject_paid_switch():
    m = create_pilot_manifest([sample()])
    m["paid_api_enabled"] = True
    with pytest.raises(ValueError, match="paid"):
        validate_manifest(m)

"""FastAPI integration checks for Tiangong pilot assets."""
from fastapi.testclient import TestClient
from app.main import create_app

def test_pilot_assets_roundtrip(tmp_path):
    client = TestClient(create_app(tmp_path))
    character = {
        "character_id": "lead", "name": "待核对主角", "face": "固定脸",
        "hair": "固定发型", "costume": "固定衣服",
        "left_hand_ring": "左手戒指", "waist_token": "腰牌", "shoes": "纹饰鞋"
    }
    response = client.post("/api/tiangong/pilot", json=character)
    assert response.status_code == 201, response.text
    manifest = response.json()
    assert len(manifest["shots"]) == 18
    assert manifest["budget_cny"] == "140"
    assert manifest["paid_api_enabled"] is False
    assert client.post("/api/tiangong/pilot", json=character).status_code == 409
    response = client.put("/api/tiangong/pilot/shots/1", json={
        "prompt": "山门远景", "character_ids": ["lead"]
    })
    assert response.status_code == 200, response.text
    assert client.get("/api/tiangong/pilot").json()["shots"][0]["prompt"] == "山门远景"
    assert client.put("/api/tiangong/pilot/shots/2", json={
        "prompt": "错误角色", "character_ids": ["unknown"]
    }).status_code == 422
    character["face"] = "修订脸部参考"
    response = client.put("/api/tiangong/pilot/characters/lead", json=character)
    assert response.status_code == 200, response.text
    assert response.json()["characters"][0]["version"] == 2
    assert client.get("/api/tiangong/pilot").json()["characters"][0]["face"] == "修订脸部参考"

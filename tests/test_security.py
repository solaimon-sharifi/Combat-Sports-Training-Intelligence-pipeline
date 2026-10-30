import io
import sqlite3
from datetime import datetime, timedelta, timezone

from conftest import FakeResponse, TEST_TOKEN, valid_report_text
import pytest
from test_videos import make_clip

HEADERS = {"X-Film-Room": "1"}


def upload_clip(client, tmp_path, name="keep.mp4"):
    data = make_clip(tmp_path / name, "mp4v", seconds=3).read_bytes()
    return client.post("/api/videos", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def protected_routes():
    return [
        ("GET", "/api/session", None),
        ("GET", "/api/config", None),
        ("GET", "/api/reviews", None),
        ("GET", "/api/videos", None),
        ("GET", "/api/videos/1/media", None),
        ("DELETE", "/api/videos/1", None),
        ("DELETE", "/api/reviews/1", None),
        ("DELETE", "/api/reviews", {"confirm": "DELETE ALL REPORTS"}),
        ("POST", "/api/review", {"mode": "upload"}),
        ("POST", "/api/live-cue", {"frames": []}),
    ]


def test_every_protected_route_refuses_after_logout(server, frame, fake_claude, tmp_path):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.raw_client()
    client.post("/api/login", json={"code": TEST_TOKEN}, headers=HEADERS)
    token = client.get_cookie("fr_session").value
    client.post("/api/logout", headers=HEADERS)
    client.set_cookie("fr_session", token)
    for method, path, body in protected_routes():
        kwargs = {"headers": HEADERS}
        if body is not None:
            kwargs["json"] = body
        response = client.open(path, method=method, **kwargs)
        assert response.status_code == 401, f"{method} {path} returned {response.status_code} after logout"


def test_media_is_refused_once_the_session_has_expired(server, tmp_path):
    client = server.raw_client()
    client.post("/api/login", json={"code": TEST_TOKEN}, headers=HEADERS)
    authed = server.app.test_client()
    video_id = upload_clip(authed, tmp_path).get_json()["id"]
    assert client.get(f"/api/videos/{video_id}/media").status_code == 200
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE auth_sessions SET expires_at = '2020-01-01T00:00:00+00:00'")
    assert client.get(f"/api/videos/{video_id}/media").status_code == 401


def test_upload_rate_limit_applies_to_every_attempt(server, tmp_path):
    client = server.app.test_client()
    statuses = []
    for i in range(11):
        response = client.post("/api/videos", data={"file": (io.BytesIO(b"not video" * 20), f"x{i}.mp4")},
                               content_type="multipart/form-data")
        statuses.append(response.status_code)
    assert statuses[:10] == [422] * 10
    assert statuses[10] == 429


def test_media_cannot_be_reached_by_storage_name_or_path_tricks(server, tmp_path):
    client = server.app.test_client()
    upload_clip(client, tmp_path)
    with sqlite3.connect(server.DB_PATH) as conn:
        storage_name = conn.execute("SELECT storage_name FROM videos").fetchone()[0]
    assert client.get(f"/api/videos/{storage_name}/media").status_code == 404
    assert client.get("/api/videos/..%2F..%2Fdb%2Ftraining.db/media").status_code == 404
    assert client.get(f"/{storage_name}").status_code == 404
    assert client.get("/storage/videos/" + storage_name).status_code == 404


def test_stored_files_live_outside_the_static_web_root(server, tmp_path):
    client = server.app.test_client()
    upload_clip(client, tmp_path)
    with sqlite3.connect(server.DB_PATH) as conn:
        storage_name = conn.execute("SELECT storage_name FROM videos").fetchone()[0]
    path = server.storage.video_path(storage_name)
    static_root = server.STATIC_DIR.resolve()
    assert static_root not in path.resolve().parents


def test_resolution_above_the_limit_is_refused(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server.storage, "MAX_DIMENSION", 64)
    response = upload_clip(server.app.test_client(), tmp_path)
    assert response.status_code == 422
    assert "Resize it" in response.get_json()["error"]


def test_retention_sweep_removes_only_expired_videos(server, tmp_path):
    client = server.app.test_client()
    upload_clip(client, tmp_path, "fresh.mp4")
    upload_clip(client, tmp_path, "stale.mp4")
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE videos SET expires_at = ? WHERE display_name = 'stale.mp4'", (past,))
        conn.commit()
    assert server.sweep_expired() == 1
    names = [r["display_name"] for r in client.get("/api/videos").get_json()["videos"]]
    assert names == ["fresh.mp4"]


def test_each_video_expires_exactly_seven_days_after_upload(server, tmp_path):
    upload_clip(server.app.test_client(), tmp_path)
    with sqlite3.connect(server.DB_PATH) as conn:
        created, expires = conn.execute("SELECT created_at, expires_at FROM videos").fetchone()
    gap = datetime.fromisoformat(expires) - datetime.fromisoformat(created)
    assert gap == timedelta(days=7)


def test_secrets_never_appear_in_responses(server, frame, fake_claude, tmp_path):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    raw = server.raw_client()
    login = raw.post("/api/login", json={"code": TEST_TOKEN}, headers=HEADERS)
    assert TEST_TOKEN not in login.get_data(as_text=True)
    cookie_session = raw.get_cookie("fr_session").value
    client = server.app.test_client()
    bodies = [
        login.get_data(as_text=True),
        client.get("/api/config").get_data(as_text=True),
        client.get("/api/reviews").get_data(as_text=True),
        client.get("/api/videos").get_data(as_text=True),
        client.get("/api/session").get_data(as_text=True),
    ]
    for body in bodies:
        assert TEST_TOKEN not in body
        assert "sk-test-SENTINEL" not in body
        assert cookie_session not in body


def test_session_token_is_never_stored_in_plain_text(server):
    raw = server.raw_client()
    raw.post("/api/login", json={"code": TEST_TOKEN}, headers=HEADERS)
    token = raw.get_cookie("fr_session").value
    with sqlite3.connect(server.DB_PATH) as conn:
        values = [v for row in conn.execute("SELECT session_hash, owner FROM auth_sessions") for v in row]
    assert token not in values
    assert TEST_TOKEN not in values


def test_responses_are_marked_not_to_be_cached(server):
    response = server.app.test_client().get("/api/config")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.fixture(autouse=True)
def synthetic_codecs_allowed(server, monkeypatch):
    monkeypatch.setattr(server.storage, "UNPLAYABLE_CODECS", set())


def test_playback_requests_are_not_throttled_by_the_general_api_limit(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT_PER_MIN", 3)
    client = server.app.test_client()
    video_id = upload_clip(client, tmp_path).get_json()["id"]
    statuses = [client.get(f"/api/videos/{video_id}/media", headers={"Range": "bytes=0-9"}).status_code
                for _ in range(12)]
    assert statuses == [206] * 12


def test_playback_requests_still_have_their_own_limit(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MEDIA_RATE_LIMIT_PER_MIN", 4)
    client = server.app.test_client()
    video_id = upload_clip(client, tmp_path).get_json()["id"]
    statuses = [client.get(f"/api/videos/{video_id}/media").status_code for _ in range(6)]
    assert statuses[:4] == [200] * 4
    assert statuses[4:] == [429, 429]

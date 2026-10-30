import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from conftest import FakeResponse, TEST_TOKEN, valid_report_text

ROOT = Path(__file__).resolve().parent.parent


def login(server, client, code=TEST_TOKEN):
    return client.post("/api/login", json={"code": code}, headers={"X-Film-Room": "1"})


def test_server_refuses_to_start_without_an_access_code(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "APP_TOKEN"}
    env.update({"FILM_ROOM_DB": str(tmp_path / "x.db"), "FILM_ROOM_STORAGE": str(tmp_path / "s")})
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'app'); import server"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "APP_TOKEN" in result.stderr


def test_server_refuses_a_short_access_code(tmp_path):
    env = dict(os.environ, APP_TOKEN="short", FILM_ROOM_DB=str(tmp_path / "x.db"),
               FILM_ROOM_STORAGE=str(tmp_path / "s"))
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'app'); import server"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_api_requires_login_before_anything_else(server):
    client = server.raw_client()
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/reviews").status_code == 401
    assert client.get("/api/videos").status_code == 401


def test_wrong_code_is_rejected(server):
    client = server.raw_client()
    response = login(server, client, code="not-the-code-at-all")
    assert response.status_code == 401


def test_correct_code_sets_an_httponly_strict_session_cookie(server):
    client = server.raw_client()
    response = login(server, client)
    assert response.status_code == 200
    cookie = response.headers.get("Set-Cookie", "")
    assert "fr_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert TEST_TOKEN not in response.get_data(as_text=True)


def test_session_token_is_stored_only_as_a_hash(server):
    client = server.raw_client()
    login(server, client)
    token = client.get_cookie("fr_session").value
    with sqlite3.connect(server.DB_PATH) as conn:
        stored = [row[0] for row in conn.execute("SELECT session_hash FROM auth_sessions")]
    assert stored and token not in stored
    assert all(len(h) == 64 for h in stored)


def test_logged_in_cookie_grants_access(server):
    client = server.raw_client()
    login(server, client)
    assert client.get("/api/session").status_code == 200
    assert client.get("/api/config").status_code == 200


def test_logout_ends_the_session(server):
    client = server.raw_client()
    login(server, client)
    token = client.get_cookie("fr_session").value
    client.post("/api/logout", headers={"X-Film-Room": "1"})
    client.set_cookie("fr_session", token)
    assert client.get("/api/session").status_code == 401


def test_expired_session_is_rejected(server):
    client = server.raw_client()
    login(server, client)
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE auth_sessions SET expires_at = '2020-01-01T00:00:00+00:00'")
    assert client.get("/api/session").status_code == 401


def test_state_changing_requests_need_the_page_header(server):
    client = server.raw_client()
    login(server, client)
    assert client.delete("/api/videos/1").status_code == 403
    assert client.post("/api/review", json={}).status_code == 403


def test_repeated_wrong_codes_are_blocked_even_for_correct_guesses_afterwards(server):
    client = server.raw_client()
    statuses = [login(server, client, code=f"wrong-{i:02d}-guess").status_code for i in range(6)]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    assert login(server, client).status_code == 429


def test_header_access_code_still_works_for_scripts(server):
    client = server.raw_client()
    response = client.get("/api/session", headers={"X-App-Token": TEST_TOKEN})
    assert response.status_code == 200


def test_video_media_is_not_reachable_without_login(server, fake_claude):
    client = server.raw_client()
    assert client.get("/api/videos/1/media").status_code == 401


def test_error_responses_do_not_contain_the_access_code(server):
    response = server.raw_client().post("/api/login", json={"code": "nope"}, headers={"X-Film-Room": "1"})
    assert TEST_TOKEN not in response.get_data(as_text=True)


def test_authenticated_review_response_never_contains_the_api_key(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    body = client.post("/api/review", json={
        "mode": "upload", "discipline": "bjj", "frames": [frame], "frameTimes": [0.0], "context": "",
    }).get_data(as_text=True)
    assert "sk-test-SENTINEL" not in body

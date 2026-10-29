import json
import sqlite3

import anthropic
import httpx2
import pytest

from conftest import FakeResponse, valid_report_text

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, status, message="error"):
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


def review_body(frame, count=4, context="closed guard", times=None):
    return {
        "mode": "upload",
        "frames": [frame] * count,
        "frameTimes": times if times is not None else [i * 5.0 for i in range(count)],
        "context": context,
    }


def rows(server):
    with sqlite3.connect(server.DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM film_reviews").fetchone()[0]


def test_config_reports_the_sampling_settings(server):
    body = server.app.test_client().get("/api/config").get_json()
    assert body["maxFrames"] == 16
    assert body["minFrames"] == 4
    assert body["frameIntervalSeconds"] == 8


def test_bad_mode_is_rejected(server, frame):
    body = dict(review_body(frame), mode="drill")
    response = server.app.test_client().post("/api/review", json=body)
    assert response.status_code == 400


def test_frame_count_above_the_cap_is_rejected(server, frame, monkeypatch):
    monkeypatch.setattr(server, "MAX_FRAMES", 4)
    response = server.app.test_client().post("/api/review", json=review_body(frame, count=5))
    assert response.status_code == 400
    assert "at most 4" in response.get_json()["error"]


def test_frame_times_must_match_the_frame_count(server, frame):
    response = server.app.test_client().post("/api/review", json=review_body(frame, count=4, times=[0, 5]))
    assert response.status_code == 400
    assert "one time for each frame" in response.get_json()["error"]


def test_non_jpeg_frame_is_rejected(server):
    body = {"mode": "upload", "frames": ["data:image/png;base64,AAAA"], "frameTimes": [0.0], "context": ""}
    response = server.app.test_client().post("/api/review", json=body)
    assert response.status_code == 400


def test_successful_review_stores_a_validated_report(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text())])
    response = server.app.test_client().post("/api/review", json=review_body(frame))

    assert response.status_code == 200
    body = response.get_json()
    assert body["cached"] is False
    assert body["report"]["top_priorities"][0]["title"] == "Elbow discipline"
    assert rows(server) == 1

    call = client.messages.calls[0]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["max_tokens"] == server.REVIEW_MAX_TOKENS
    assert "temperature" not in call

    with sqlite3.connect(server.DB_PATH) as conn:
        stored = conn.execute("SELECT report_json, input_tokens, output_tokens, request_hash FROM film_reviews").fetchone()
    assert json.loads(stored[0])["summary"].startswith("Closed guard")
    assert (stored[1], stored[2]) == (1200, 450)
    assert len(stored[3]) == 64


def test_identical_request_is_served_from_cache_without_an_api_call(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text())])
    app = server.app.test_client()
    first = app.post("/api/review", json=review_body(frame)).get_json()
    second = app.post("/api/review", json=review_body(frame)).get_json()

    assert len(client.messages.calls) == 1
    assert second["cached"] is True
    assert second["id"] == first["id"]
    assert rows(server) == 1


def test_changed_context_is_a_new_request(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text())])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame, context="closed guard"))
    app.post("/api/review", json=review_body(frame, context="half guard"))
    assert len(client.messages.calls) == 2


def test_malformed_output_is_retried_once_then_succeeds(server, frame, fake_claude):
    client = fake_claude([FakeResponse("not json at all"), FakeResponse(valid_report_text())])
    response = server.app.test_client().post("/api/review", json=review_body(frame))
    assert response.status_code == 200
    assert len(client.messages.calls) == 2


def test_malformed_output_twice_fails_cleanly_and_stores_nothing(server, frame, fake_claude):
    fake_claude([FakeResponse("nope"), FakeResponse("still nope")])
    response = server.app.test_client().post("/api/review", json=review_body(frame))
    assert response.status_code == 502
    assert rows(server) == 0


def test_cut_off_output_returns_a_clear_error(server, frame, fake_claude):
    fake_claude([FakeResponse("{", stop_reason="max_tokens")])
    response = server.app.test_client().post("/api/review", json=review_body(frame))
    assert response.status_code == 502
    assert "cut off" in response.get_json()["error"]


def test_refusal_returns_422(server, frame, fake_claude):
    fake_claude([FakeResponse("", stop_reason="refusal")])
    response = server.app.test_client().post("/api/review", json=review_body(frame))
    assert response.status_code == 422


@pytest.mark.parametrize("error, status", [
    (status_error(anthropic.RateLimitError, 429), 429),
    (status_error(anthropic.BadRequestError, 400), 422),
    (status_error(anthropic.InternalServerError, 500), 502),
    (anthropic.APIConnectionError(message="down", request=REQUEST), 503),
    (status_error(anthropic.AuthenticationError, 401), 500),
    (status_error(anthropic.PermissionDeniedError, 403), 500),
])
def test_api_errors_map_to_clear_statuses(server, frame, fake_claude, error, status):
    fake_claude([error])
    response = server.app.test_client().post("/api/review", json=review_body(frame))
    assert response.status_code == status
    assert response.get_json()["error"]


def test_api_key_never_appears_in_an_error_response(server, frame, fake_claude):
    fake_claude([status_error(anthropic.AuthenticationError, 401, "invalid x-api-key")])
    response = server.app.test_client().post("/api/review", json=review_body(frame))
    assert "sk-test-SENTINEL" not in response.get_data(as_text=True)


def test_access_code_is_required_when_configured(server, frame, monkeypatch):
    monkeypatch.setattr(server, "APP_TOKEN", "letmein")
    app = server.app.test_client()
    assert app.get("/api/reviews").status_code == 401
    assert app.get("/api/reviews", headers={"X-App-Token": "wrong"}).status_code == 401
    assert app.get("/api/reviews", headers={"X-App-Token": "letmein"}).status_code == 200


def test_rate_limit_returns_429_after_the_allowance(server, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT_PER_MIN", 3)
    server._hits.clear()
    app = server.app.test_client()
    statuses = [app.get("/api/reviews").status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]


def test_live_cue_returns_text_and_stores_nothing(server, frame, fake_claude):
    fake_claude([FakeResponse("Hips are square. Break posture now.")])
    response = server.app.test_client().post("/api/live-cue", json={"frames": [frame]})
    assert response.get_json()["cue"].startswith("Hips are square")
    assert rows(server) == 0


def test_history_returns_structured_reports_and_legacy_text(server, frame, fake_claude):
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback) "
            "VALUES ('2026-09-01T10:00:00', 'upload', NULL, 8, 'Observations: old text report')"
        )
    fake_claude([FakeResponse(valid_report_text())])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame))
    reviews = app.get("/api/reviews").get_json()["reviews"]
    assert reviews[0]["report"]["summary"].startswith("Closed guard")
    assert reviews[1]["report"] is None
    assert reviews[1]["feedback"] == "Observations: old text report"

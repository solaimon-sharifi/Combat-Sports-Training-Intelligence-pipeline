import json
import sqlite3

import anthropic
import httpx2
import pytest

from conftest import FakeResponse, valid_report_text
from disciplines import DISCIPLINES

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
ALL = list(DISCIPLINES)


def status_error(cls, status, message="error"):
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


def review_body(frame, discipline="bjj", count=4, context="closed guard", times=None):
    return {
        "mode": "upload",
        "discipline": discipline,
        "frames": [frame] * count,
        "frameTimes": times if times is not None else [i * 5.0 for i in range(count)],
        "context": context,
    }


def rows(server):
    with sqlite3.connect(server.DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM film_reviews").fetchone()[0]


def test_config_lists_all_five_disciplines(server):
    body = server.app.test_client().get("/api/config").get_json()
    assert set(body["disciplines"]) == set(ALL)
    assert body["disciplines"]["boxing"]["label"] == "Boxing"
    assert body["maxFrames"] == 16


def test_review_without_a_discipline_is_rejected(server, frame):
    body = review_body(frame)
    del body["discipline"]
    response = server.app.test_client().post("/api/review", json=body)
    assert response.status_code == 400
    assert "Choose one of" in response.get_json()["error"]


def test_review_with_an_unknown_discipline_is_rejected(server, frame):
    response = server.app.test_client().post("/api/review", json=review_body(frame, discipline="wrestling"))
    assert response.status_code == 400


def test_bad_mode_is_rejected(server, frame):
    body = dict(review_body(frame), mode="drill")
    assert server.app.test_client().post("/api/review", json=body).status_code == 400


def test_frame_count_above_the_cap_is_rejected(server, frame, monkeypatch):
    monkeypatch.setattr(server, "MAX_FRAMES", 4)
    response = server.app.test_client().post("/api/review", json=review_body(frame, count=5))
    assert response.status_code == 400
    assert "at most 4" in response.get_json()["error"]


def test_frame_times_must_match_the_frame_count(server, frame):
    response = server.app.test_client().post("/api/review", json=review_body(frame, count=4, times=[0, 5]))
    assert response.status_code == 400


def test_non_jpeg_frame_is_rejected(server):
    body = {"mode": "upload", "discipline": "bjj", "frames": ["data:image/png;base64,AAAA"],
            "frameTimes": [0.0], "context": ""}
    assert server.app.test_client().post("/api/review", json=body).status_code == 400


@pytest.mark.parametrize("discipline", ALL)
def test_each_discipline_returns_a_report_for_that_sport(server, frame, fake_claude, discipline):
    client = fake_claude([FakeResponse(valid_report_text(discipline))])
    response = server.app.test_client().post("/api/review", json=review_body(frame, discipline=discipline))
    assert response.status_code == 200
    body = response.get_json()
    assert body["report"]["discipline"] == discipline
    assert body["cached"] is False

    call = client.messages.calls[0]
    schema = call["output_config"]["format"]["schema"]
    areas = schema["properties"]["technical_findings"]["items"]["properties"]["area"]["enum"]
    assert set(areas) == set(DISCIPLINES[discipline]["areas"])
    text_blocks = [b["text"] for b in call["messages"][0]["content"] if b["type"] == "text"]
    assert any(DISCIPLINES[discipline]["label"] in t for t in text_blocks)


def test_model_output_for_another_sport_fails_validation(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("boxing")), FakeResponse(valid_report_text("boxing"))])
    response = server.app.test_client().post("/api/review", json=review_body(frame, discipline="bjj"))
    assert response.status_code == 502
    assert rows(server) == 0


def test_successful_review_stores_the_discipline_and_tokens(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("mma"))])
    server.app.test_client().post("/api/review", json=review_body(frame, discipline="mma"))
    with sqlite3.connect(server.DB_PATH) as conn:
        stored = conn.execute(
            "SELECT discipline, prompt_version, model, input_tokens, output_tokens, request_hash FROM film_reviews"
        ).fetchone()
    assert stored[0] == "mma"
    assert stored[1] == server.prompt_version("mma")
    assert stored[2] == server.MODEL
    assert (stored[3], stored[4]) == (1200, 450)
    assert len(stored[5]) == 64


def test_identical_request_is_served_from_cache(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    first = app.post("/api/review", json=review_body(frame)).get_json()
    second = app.post("/api/review", json=review_body(frame)).get_json()
    assert len(client.messages.calls) == 1
    assert second["cached"] is True
    assert second["id"] == first["id"]
    assert rows(server) == 1


def test_same_frames_for_a_different_discipline_are_not_shared(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("boxing")), FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    boxing = app.post("/api/review", json=review_body(frame, discipline="boxing")).get_json()
    bjj = app.post("/api/review", json=review_body(frame, discipline="bjj")).get_json()
    assert len(client.messages.calls) == 2
    assert boxing["cached"] is False and bjj["cached"] is False
    assert bjj["report"]["discipline"] == "bjj"


def test_changed_context_is_a_new_request(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame, context="closed guard"))
    app.post("/api/review", json=review_body(frame, context="half guard"))
    assert len(client.messages.calls) == 2


def test_changed_frame_times_are_a_new_request(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame, times=[0.0, 5.0, 10.0, 15.0]))
    app.post("/api/review", json=review_body(frame, times=[0.0, 6.0, 12.0, 18.0]))
    assert len(client.messages.calls) == 2


def test_changed_frame_content_is_a_new_request(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    other = frame[:-4] + "QUFB"
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame))
    body = review_body(frame)
    body["frames"] = [other] * 4
    app.post("/api/review", json=body)
    assert len(client.messages.calls) == 2


def test_changed_model_is_a_new_request(server, frame, fake_claude, monkeypatch):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame))
    monkeypatch.setattr(server, "MODEL", "claude-other-model")
    app.post("/api/review", json=review_body(frame))
    assert len(client.messages.calls) == 2


def test_changed_prompt_version_is_a_new_request(server, frame, fake_claude, monkeypatch):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame))
    monkeypatch.setattr(server, "prompt_version", lambda discipline: "edited-instructions")
    app.post("/api/review", json=review_body(frame))
    assert len(client.messages.calls) == 2


def test_cache_ignores_a_row_whose_discipline_differs(server, frame, fake_claude):
    client = fake_claude([FakeResponse(valid_report_text("bjj"))])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame))
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE film_reviews SET discipline = 'boxing'")
    app.post("/api/review", json=review_body(frame))
    assert len(client.messages.calls) == 2


def test_malformed_output_is_retried_once_then_succeeds(server, frame, fake_claude):
    client = fake_claude([FakeResponse("not json"), FakeResponse(valid_report_text("kickboxing"))])
    response = server.app.test_client().post("/api/review", json=review_body(frame, discipline="kickboxing"))
    assert response.status_code == 200
    assert len(client.messages.calls) == 2


def test_insufficient_evidence_is_a_valid_answer(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("muay_thai", evidence_status="insufficient"))])
    response = server.app.test_client().post("/api/review", json=review_body(frame, discipline="muay_thai"))
    body = response.get_json()
    assert response.status_code == 200
    assert body["report"]["evidence_status"] == "insufficient"
    assert body["report"]["technical_findings"] == []


def test_cut_off_output_returns_a_clear_error(server, frame, fake_claude):
    fake_claude([FakeResponse("{", stop_reason="max_tokens")])
    response = server.app.test_client().post("/api/review", json=review_body(frame))
    assert response.status_code == 502
    assert "cut off" in response.get_json()["error"]


def test_refusal_returns_422(server, frame, fake_claude):
    fake_claude([FakeResponse("", stop_reason="refusal")])
    assert server.app.test_client().post("/api/review", json=review_body(frame)).status_code == 422


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


def test_access_code_is_required_when_configured(server, monkeypatch):
    monkeypatch.setattr(server, "APP_TOKEN", "letmein")
    app = server.app.test_client()
    assert app.get("/api/reviews").status_code == 401
    assert app.get("/api/reviews", headers={"X-App-Token": "wrong"}).status_code == 401
    assert app.get("/api/reviews", headers={"X-App-Token": "letmein"}).status_code == 200


def test_rate_limit_returns_429_after_the_allowance(server, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT_PER_MIN", 3)
    server._hits.clear()
    app = server.app.test_client()
    assert [app.get("/api/reviews").status_code for _ in range(4)] == [200, 200, 200, 429]


def test_live_cue_requires_a_discipline_and_names_it_in_the_prompt(server, frame, fake_claude):
    client = fake_claude([FakeResponse("Hands up. Chin tucked.")])
    app = server.app.test_client()
    assert app.post("/api/live-cue", json={"frames": [frame]}).status_code == 400
    response = app.post("/api/live-cue", json={"frames": [frame], "discipline": "boxing"})
    assert response.get_json()["cue"] == "Hands up. Chin tucked."
    text = " ".join(b.get("text", "") for b in client.messages.calls[0]["messages"][0]["content"])
    assert "Boxing coach" in text
    assert rows(server) == 0


def test_history_shows_each_reviews_discipline_and_legacy_rows(server, frame, fake_claude):
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO film_reviews (created_at, mode, context, frame_count, feedback) "
            "VALUES ('2026-09-01T10:00:00', 'upload', NULL, 8, 'Observations: old text report')"
        )
    fake_claude([FakeResponse(valid_report_text("mma"))])
    app = server.app.test_client()
    app.post("/api/review", json=review_body(frame, discipline="mma"))
    reviews = app.get("/api/reviews").get_json()["reviews"]
    assert reviews[0]["discipline_label"] == "MMA"
    assert reviews[0]["report"]["discipline"] == "mma"
    assert reviews[1]["report"] is None
    assert reviews[1]["discipline_label"] == "Unspecified"
    assert reviews[1]["feedback"] == "Observations: old text report"

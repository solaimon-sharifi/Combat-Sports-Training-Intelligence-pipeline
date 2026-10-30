import io
import sqlite3

from conftest import FakeResponse, valid_report_text
import pytest
from test_videos import make_clip

HEADERS = {"X-Film-Room": "1"}


def make_review(client, frame, context="closed guard", video_id=None, times=(0.0,)):
    body = {"mode": "upload", "discipline": "bjj", "frames": [frame] * len(times),
            "frameTimes": list(times), "context": context}
    if video_id is not None:
        body["videoId"] = video_id
    return client.post("/api/review", json=body).get_json()


def store_video(client, tmp_path):
    clip = make_clip(tmp_path / "keep.mp4", "mp4v", seconds=4).read_bytes()
    response = client.post("/api/videos", data={"file": (io.BytesIO(clip), "keep.mp4")},
                           content_type="multipart/form-data")
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def count(server, table):
    with sqlite3.connect(server.DB_PATH) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_deleting_one_report_leaves_every_other_report_in_place(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 3)
    client = server.app.test_client()
    first = make_review(client, frame, context="one")
    second = make_review(client, frame, context="two")
    assert client.delete(f"/api/reviews/{first['id']}", headers=HEADERS).status_code == 200
    remaining = [r["id"] for r in client.get("/api/reviews").get_json()["reviews"]]
    assert remaining == [second["id"]]


def test_deleting_a_report_removes_its_cache_entry(server, frame, fake_claude):
    stub = fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    first = make_review(client, frame)
    client.delete(f"/api/reviews/{first['id']}", headers=HEADERS)
    again = make_review(client, frame)
    assert again["cached"] is False
    assert len(stub.messages.calls) == 2


def test_deleting_a_report_never_deletes_its_video(server, frame, fake_claude, tmp_path):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    video_id = store_video(client, tmp_path)
    review = make_review(client, frame, video_id=video_id)
    client.delete(f"/api/reviews/{review['id']}", headers=HEADERS)
    assert count(server, "videos") == 1
    assert client.get(f"/api/videos/{video_id}/media").status_code == 200


def test_a_shared_video_survives_deleting_one_of_two_reports(server, frame, fake_claude, tmp_path):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    video_id = store_video(client, tmp_path)
    one = make_review(client, frame, context="first", video_id=video_id)
    two = make_review(client, frame, context="second", video_id=video_id)
    client.delete(f"/api/reviews/{one['id']}", headers=HEADERS)
    rows = {r["id"]: r for r in client.get("/api/reviews").get_json()["reviews"]}
    assert rows[two["id"]]["video_available"] is True
    assert client.get(f"/api/videos/{video_id}/media").status_code == 200


def test_deleting_a_video_keeps_its_reports_without_playback(server, frame, fake_claude, tmp_path):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    video_id = store_video(client, tmp_path)
    review = make_review(client, frame, video_id=video_id)
    client.delete(f"/api/videos/{video_id}", headers=HEADERS)
    row = client.get("/api/reviews").get_json()["reviews"][0]
    assert row["id"] == review["id"]
    assert row["report"] is not None
    assert row["video_available"] is False


def test_deleting_a_missing_report_returns_404(server):
    assert server.app.test_client().delete("/api/reviews/999", headers=HEADERS).status_code == 404


def test_delete_all_requires_the_exact_confirmation_phrase(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client = server.app.test_client()
    make_review(client, frame)
    assert client.delete("/api/reviews", headers=HEADERS).status_code == 400
    assert client.delete("/api/reviews", json={"confirm": "yes"}, headers=HEADERS).status_code == 400
    assert count(server, "film_reviews") == 1


def test_delete_all_with_confirmation_removes_reports_but_keeps_videos(server, frame, fake_claude, tmp_path):
    fake_claude([FakeResponse(valid_report_text("bjj"))] * 2)
    client = server.app.test_client()
    video_id = store_video(client, tmp_path)
    make_review(client, frame, context="a", video_id=video_id)
    make_review(client, frame, context="b")
    response = client.delete("/api/reviews", json={"confirm": "DELETE ALL REPORTS"}, headers=HEADERS)
    assert response.get_json()["deleted"] == 2
    assert count(server, "film_reviews") == 0
    assert count(server, "videos") == 1


def test_report_deletion_requires_login(server, frame, fake_claude):
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    review = make_review(server.app.test_client(), frame)
    anonymous = server.raw_client()
    assert anonymous.delete(f"/api/reviews/{review['id']}", headers=HEADERS).status_code == 401
    assert anonymous.delete("/api/reviews", json={"confirm": "DELETE ALL REPORTS"},
                            headers=HEADERS).status_code == 401
    assert count(server, "film_reviews") == 1


@pytest.fixture(autouse=True)
def synthetic_codecs_allowed(server, monkeypatch):
    monkeypatch.setattr(server.storage, "UNPLAYABLE_CODECS", set())

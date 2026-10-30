import io
import sqlite3
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest

from conftest import FakeResponse, valid_report_text


def make_clip(path, fourcc, seconds=3, fps=10):
    """Write a small synthetic clip (labeled test footage, not training video)."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, (160, 120))
    assert writer.isOpened(), f"could not write {fourcc} test clip"
    for i in range(int(seconds * fps)):
        frame = np.full((120, 160, 3), 40, np.uint8)
        cv2.rectangle(frame, (i % 140, 40), (i % 140 + 20, 80), (200, 120, 60), -1)
        writer.write(frame)
    writer.release()
    return path


@pytest.fixture
def mp4_clip(tmp_path):
    return make_clip(tmp_path / "synthetic.mp4", "mp4v").read_bytes()


@pytest.fixture
def mov_clip(tmp_path):
    return make_clip(tmp_path / "synthetic.mov", "jpeg").read_bytes()


def logged_in(server):
    client = server.app.test_client()
    return client


@pytest.fixture(autouse=True)
def synthetic_codecs_allowed(server, monkeypatch):
    """The test clips use OpenCV's MPEG-4 Part 2 and Motion JPEG writers, which browsers
    cannot play. Rejecting them is tested separately; everything else needs them accepted."""
    monkeypatch.setattr(server.storage, "UNPLAYABLE_CODECS", set())


def test_mp4v_upload_is_rejected_because_browsers_cannot_play_it(server, mp4_clip, monkeypatch):
    monkeypatch.setattr(server.storage, "UNPLAYABLE_CODECS", {"mp4v", "mjpg", "jpeg"})
    response = upload(logged_in(server), mp4_clip, "old-codec.mp4")
    assert response.status_code == 422
    assert "H.264" in response.get_json()["error"]
    assert stored_rows(server) == []


def upload(client, data, name):
    return client.post(
        "/api/videos",
        data={"file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def stored_rows(server):
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM videos")]


def test_mp4_upload_is_stored_privately_with_seven_day_expiry(server, mp4_clip):
    response = upload(logged_in(server), mp4_clip, "practice.mp4")
    assert response.status_code == 201
    body = response.get_json()
    assert 2.0 <= body["durationSeconds"] <= 4.0
    row = stored_rows(server)[0]
    assert row["mime"] == "video/mp4"
    stored = server.storage.video_path(row["storage_name"])
    assert stored.exists()
    assert "static" not in str(stored)
    created = datetime.fromisoformat(row["created_at"])
    expires = datetime.fromisoformat(row["expires_at"])
    assert expires - created == timedelta(days=7)


def test_mov_upload_is_accepted_and_records_its_codec(server, mov_clip):
    response = upload(logged_in(server), mov_clip, "IMG_0001.MOV")
    assert response.status_code == 201
    row = stored_rows(server)[0]
    assert row["mime"] == "video/quicktime"
    assert row["codec"] in ("mjpa", "mjpg", "jpeg")


def test_unsupported_extension_is_rejected(server, mp4_clip):
    response = upload(logged_in(server), mp4_clip, "clip.avi")
    assert response.status_code == 422
    assert "MP4 or MOV" in response.get_json()["error"]
    assert stored_rows(server) == []


def test_file_without_a_video_signature_is_rejected(server):
    response = upload(logged_in(server), b"this is plain text, not a video" * 10, "fake.mp4")
    assert response.status_code == 422
    assert "not an MP4 or MOV" in response.get_json()["error"]


def test_corrupt_video_with_a_valid_header_is_rejected(server):
    data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
    response = upload(logged_in(server), data, "broken.mp4")
    assert response.status_code == 422
    assert stored_rows(server) == []


def test_empty_upload_is_rejected(server):
    response = upload(logged_in(server), b"", "empty.mp4")
    assert response.status_code == 422


def test_oversized_upload_is_rejected(server, mp4_clip, monkeypatch):
    monkeypatch.setattr(server.storage, "MAX_BYTES", 1024)
    response = upload(logged_in(server), mp4_clip, "big.mp4")
    assert response.status_code == 413
    assert stored_rows(server) == []


def test_clip_longer_than_the_limit_is_rejected(server, mp4_clip, monkeypatch):
    monkeypatch.setattr(server.storage, "MAX_SECONDS", 1.0)
    response = upload(logged_in(server), mp4_clip, "long.mp4")
    assert response.status_code == 422
    assert "longer than" in response.get_json()["error"]
    assert stored_rows(server) == []


def test_owner_can_stream_the_video_with_seek_support(server, mp4_clip):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    response = client.get(f"/api/videos/{video_id}/media")
    assert response.status_code == 200
    assert response.mimetype == "video/mp4"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    ranged = client.get(f"/api/videos/{video_id}/media", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206
    assert len(ranged.get_data()) == 100


def test_video_media_is_forbidden_to_anyone_else(server, mp4_clip):
    video_id = upload(logged_in(server), mp4_clip, "practice.mp4").get_json()["id"]
    anonymous = server.raw_client()
    assert anonymous.get(f"/api/videos/{video_id}/media").status_code == 401


def test_videos_are_not_served_from_the_static_folder(server, mp4_clip):
    video_id = upload(logged_in(server), mp4_clip, "practice.mp4").get_json()["id"]
    name = stored_rows(server)[0]["storage_name"]
    assert server.app.test_client().get(f"/{name}").status_code == 404
    assert server.app.test_client().get(f"/videos/{name}").status_code == 404
    assert video_id


def test_another_owner_cannot_read_or_delete_a_video(server, mp4_clip):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE videos SET owner = 'someone-else'")
    assert client.get(f"/api/videos/{video_id}/media").status_code == 404
    assert client.delete(f"/api/videos/{video_id}", headers={"X-Film-Room": "1"}).status_code == 404


def test_deleting_a_video_removes_the_file_and_the_link_from_reviews(server, mp4_clip, frame, fake_claude):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    fake_claude([FakeResponse(valid_report_text("bjj"))])
    client.post("/api/review", json={
        "mode": "upload", "discipline": "bjj", "frames": [frame], "frameTimes": [1.0],
        "context": "", "videoId": video_id,
    })
    path = server.storage.video_path(stored_rows(server)[0]["storage_name"])
    assert path.exists()

    response = client.delete(f"/api/videos/{video_id}", headers={"X-Film-Room": "1"})
    assert response.status_code == 200
    assert not path.exists()
    assert stored_rows(server) == []
    review = client.get("/api/reviews").get_json()["reviews"][0]
    assert review["video_id"] is None
    assert review["video_available"] is False
    assert review["report"] is not None


def test_expired_videos_are_deleted_and_no_longer_served(server, mp4_clip):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    row = stored_rows(server)[0]
    path = server.storage.video_path(row["storage_name"])
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE videos SET expires_at = '2020-01-01T00:00:00+00:00'")
    assert client.get(f"/api/videos/{video_id}/media").status_code == 404
    assert not path.exists()
    assert stored_rows(server) == []


def test_retention_sweep_keeps_unexpired_videos(server, mp4_clip):
    client = logged_in(server)
    upload(client, mp4_clip, "keep.mp4")
    upload(client, mp4_clip, "expire.mp4")
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE videos SET expires_at = '2020-01-01T00:00:00+00:00' WHERE display_name = 'expire.mp4'")
        conn.commit()
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        removed = server.storage.purge_expired(conn)
    assert removed == 1
    names = [r["display_name"] for r in stored_rows(server)]
    assert names == ["keep.mp4"]


def test_listing_shows_only_the_owners_videos(server, mp4_clip):
    client = logged_in(server)
    upload(client, mp4_clip, "one.mp4")
    listing = client.get("/api/videos").get_json()["videos"]
    assert [v["displayName"] if "displayName" in v else v["display_name"] for v in listing] == ["one.mp4"]


def test_review_with_a_stored_video_keeps_its_timestamps(server, mp4_clip, frame, fake_claude):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    fake_claude([FakeResponse(valid_report_text("boxing"))])
    body = client.post("/api/review", json={
        "mode": "upload", "discipline": "boxing", "frames": [frame, frame],
        "frameTimes": [0.5, 2.5], "context": "", "videoId": video_id,
    }).get_json()
    times = [f["time_seconds"] for f in body["report"]["frames"]]
    assert times == [0.5, 2.5]
    with sqlite3.connect(server.DB_PATH) as conn:
        linked = conn.execute("SELECT video_id FROM film_reviews ORDER BY review_id DESC LIMIT 1").fetchone()[0]
    assert linked == video_id


def test_timestamps_beyond_the_stored_clip_are_rejected(server, mp4_clip, frame, fake_claude):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    fake_claude([FakeResponse(valid_report_text("boxing"))])
    response = client.post("/api/review", json={
        "mode": "upload", "discipline": "boxing", "frames": [frame], "frameTimes": [60.0],
        "context": "", "videoId": video_id,
    })
    assert response.status_code == 400
    assert "beyond the stored video" in response.get_json()["error"]


def test_another_owners_video_cannot_be_attached_to_a_review(server, mp4_clip, frame, fake_claude):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    with sqlite3.connect(server.DB_PATH) as conn:
        conn.execute("UPDATE videos SET owner = 'someone-else'")
    response = client.post("/api/review", json={
        "mode": "upload", "discipline": "boxing", "frames": [frame], "frameTimes": [1.0],
        "context": "", "videoId": video_id,
    })
    assert response.status_code == 400


def test_live_sessions_cannot_reference_a_stored_video(server, mp4_clip, frame):
    client = logged_in(server)
    video_id = upload(client, mp4_clip, "practice.mp4").get_json()["id"]
    response = client.post("/api/review", json={
        "mode": "live", "discipline": "boxing", "frames": [frame], "frameTimes": [1.0],
        "context": "", "videoId": video_id,
    })
    assert response.status_code == 400


def test_unsupported_upload_does_not_leave_temporary_files(server, mp4_clip):
    upload(logged_in(server), b"not a video at all" * 20, "bad.mp4")
    leftovers = list((server.storage.STORAGE_DIR / "tmp").glob("*"))
    assert leftovers == []


def test_codec_is_read_from_the_sample_description(server, mp4_clip, tmp_path):
    path = tmp_path / "codec.mp4"
    path.write_bytes(mp4_clip)
    assert server.storage.video_codec(path) == "mp4v"

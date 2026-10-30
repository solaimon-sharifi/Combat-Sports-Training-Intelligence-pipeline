"""Checks against real video files on this machine. Skipped unless paths are provided.

    FILM_ROOM_TEST_H264_MP4=path\\to\\h264.mp4  FILM_ROOM_TEST_HEVC_MOV=path\\to\\clip.MOV pytest tests/test_real_media.py

Nothing here uploads or sends footage anywhere. The files are only read for their
container headers, so private footage never enters the repository.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))


def path_from_env(name):
    value = os.environ.get(name)
    if not value or not Path(value).exists():
        pytest.skip(f"set {name} to a real file to run this check")
    return Path(value)


def test_real_h264_mp4_is_recognised_as_playable():
    import storage

    codec = storage.video_codec(path_from_env("FILM_ROOM_TEST_H264_MP4"))
    assert codec == "avc1"
    assert codec not in storage.UNPLAYABLE_CODECS


def test_real_hevc_mov_is_recognised_and_not_refused_by_the_server():
    import storage

    codec = storage.video_codec(path_from_env("FILM_ROOM_TEST_HEVC_MOV"))
    assert codec in ("hvc1", "hev1")
    assert codec not in storage.UNPLAYABLE_CODECS

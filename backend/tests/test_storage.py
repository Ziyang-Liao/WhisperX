"""Tests for the S3 StorageService, using moto to mock S3 locally (no real AWS)."""

import pytest

moto = pytest.importorskip("moto", reason="moto not installed; S3 unit tests skipped")

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

from app.services.storage import (  # noqa: E402
    StorageService,
    media_prefixes,
    subtitle_key,
    video_key,
)

BUCKET = "test-subtitle-bucket"
REGION = "us-east-1"


@pytest.fixture
def storage():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield StorageService(bucket=BUCKET, region=REGION)


def test_key_helpers():
    assert video_key(7, "mp4") == "videos/7/source.mp4"
    assert video_key(7, ".MP4") == "videos/7/source.mp4"
    assert subtitle_key(7, "ja", "srt") == "subtitles/7/ja.srt"
    assert subtitle_key(7, "en", ".vtt") == "subtitles/7/en.vtt"
    assert media_prefixes(7) == ["videos/7/", "subtitles/7/", "thumbnails/7"]


def test_put_and_get_bytes(storage):
    key = subtitle_key(1, "en", "srt")
    storage.put_bytes(key, b"1\n00:00:00,000 --> 00:00:01,000\nhi\n", "text/plain")
    assert storage.get_bytes(key).startswith(b"1\n")
    assert storage.exists(key)


def test_exists_false_for_missing(storage):
    assert storage.exists("videos/999/source.mp4") is False
    assert storage.head("videos/999/source.mp4") is None


def test_presigned_urls_contain_signature(storage):
    key = video_key(1, "mp4")
    put_url = storage.presigned_put(key, content_type="video/mp4")
    get_url = storage.presigned_get(key)
    assert BUCKET in put_url and "Signature" in put_url or "X-Amz-Signature" in put_url
    assert key in get_url


def test_delete_prefixes_cascades(storage):
    storage.put_bytes(video_key(5, "mp4"), b"video")
    storage.put_bytes(subtitle_key(5, "en", "srt"), b"en")
    storage.put_bytes(subtitle_key(5, "ja", "vtt"), b"ja")
    # An unrelated media's files must survive.
    storage.put_bytes(video_key(6, "mp4"), b"other")

    deleted = storage.delete_prefixes(media_prefixes(5))
    assert deleted == 3
    assert not storage.exists(video_key(5, "mp4"))
    assert not storage.exists(subtitle_key(5, "en", "srt"))
    assert storage.exists(video_key(6, "mp4"))

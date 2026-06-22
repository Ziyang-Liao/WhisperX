from __future__ import annotations
"""S3-backed storage for source videos and generated subtitle files.

All object access goes through this service so the rest of the app never talks
to boto3 directly. Large media is moved via presigned URLs (client uploads /
downloads directly to S3) to keep big files out of the API process's memory.
"""

import os
from dataclasses import dataclass

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# S3 key layout (see docs/subtitle-platform-design.md §9)
VIDEO_PREFIX = "videos"
SUBTITLE_PREFIX = "subtitles"
THUMBNAIL_PREFIX = "thumbnails"

DEFAULT_PRESIGN_TTL = 900  # seconds (15 min)


def video_key(media_id: int, ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return f"{VIDEO_PREFIX}/{media_id}/source.{ext}"


def subtitle_key(media_id: int, language: str, fmt: str) -> str:
    fmt = fmt.lstrip(".").lower()
    return f"{SUBTITLE_PREFIX}/{media_id}/{language}.{fmt}"


def media_prefixes(media_id: int) -> list[str]:
    """All key prefixes owned by a media record (for cascade delete)."""
    return [
        f"{VIDEO_PREFIX}/{media_id}/",
        f"{SUBTITLE_PREFIX}/{media_id}/",
        f"{THUMBNAIL_PREFIX}/{media_id}",
    ]


@dataclass
class StorageService:
    """Thin wrapper over an S3 bucket.

    bucket: target bucket name (from S3_BUCKET env in production).
    region: AWS region; the client also honors the ambient AWS credential chain
            (instance role on EC2), so no keys are passed explicitly.
    """

    bucket: str
    region: str = "us-east-1"
    _client: object = None

    def __post_init__(self) -> None:
        if self._client is None:
            # SigV4 + virtual-host addressing so presigned URLs work in all regions.
            self._client = boto3.client(
                "s3",
                region_name=self.region,
                config=Config(signature_version="s3v4"),
            )

    @classmethod
    def from_env(cls) -> "StorageService":
        bucket = os.environ["S3_BUCKET"]
        region = os.environ.get("AWS_REGION", "us-east-1")
        return cls(bucket=bucket, region=region)

    # --- uploads / downloads -------------------------------------------------

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """Upload in-memory bytes (used for generated SRT/VTT, which are small)."""
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        return key

    def upload_file(self, local_path: str, key: str, content_type: str | None = None) -> str:
        """Upload a local file (used by the worker after generating artifacts)."""
        extra = {"ContentType": content_type} if content_type else {}
        self._client.upload_file(local_path, self.bucket, key, ExtraArgs=extra or None)
        return key

    def download_file(self, key: str, local_path: str) -> str:
        """Download an object to a local path (worker pulls the source video)."""
        self._client.download_file(self.bucket, key, local_path)
        return local_path

    def get_bytes(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    # --- presigned URLs ------------------------------------------------------

    def presigned_get(self, key: str, ttl: int = DEFAULT_PRESIGN_TTL) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl,
        )

    def presigned_put(
        self, key: str, content_type: str | None = None, ttl: int = DEFAULT_PRESIGN_TTL
    ) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return self._client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=ttl
        )

    # --- existence / delete --------------------------------------------------

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def head(self, key: str) -> dict | None:
        """Return object metadata (ContentLength etc.) or None if absent."""
        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def delete_prefixes(self, prefixes: list[str]) -> int:
        """Delete every object under the given prefixes. Returns count deleted."""
        deleted = 0
        for prefix in prefixes:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objects:
                    self._client.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": objects}
                    )
                    deleted += len(objects)
        return deleted

import os
from pathlib import Path

from config_loader import get_config_section


def _bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes"}


_MINIO_CONFIG = get_config_section("minio")
MINIO_BUCKET = os.getenv("MINIO_BUCKET") or _MINIO_CONFIG.get("bucket", "img")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT") or _MINIO_CONFIG.get("endpoint", "127.0.0.1:9000")


def _get_minio_client():
    from minio import Minio

    secure = _bool_value(os.getenv("MINIO_SECURE"), _bool_value(_MINIO_CONFIG.get("secure"), False))
    return Minio(
        MINIO_ENDPOINT,
        access_key=os.getenv("MINIO_ACCESS_KEY") or _MINIO_CONFIG.get("access_key", "minioadmin"),
        secret_key=os.getenv("MINIO_SECRET_KEY") or _MINIO_CONFIG.get("secret_key", "minioadmin"),
        secure=secure,
    )


def _content_type(path):
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


def _ensure_public_bucket(client, bucket, verbose=False):
    if not client.bucket_exists(bucket):
        if verbose:
            print(f"[MINIO] Bucket does not exist, creating: {bucket}")
        client.make_bucket(bucket)
    elif verbose:
        print(f"[MINIO] Bucket exists: {bucket}")

    policy = f"""{{
      "Version": "2012-10-17",
      "Statement": [
        {{
          "Effect": "Allow",
          "Principal": {{"AWS": ["*"]}},
          "Action": ["s3:GetObject"],
          "Resource": ["arn:aws:s3:::{bucket}/*"]
        }}
      ]
    }}"""
    client.set_bucket_policy(bucket, policy)
    if verbose:
        print(f"[MINIO] Public read policy applied: {bucket}")


def upload_image(path, object_name=None, bucket=MINIO_BUCKET, verbose=False):
    object_name = object_name or Path(path).name
    client = _get_minio_client()
    if verbose:
        print(f"[MINIO] Uploading object: bucket={bucket}, object={object_name}, file={path}")
    _ensure_public_bucket(client, bucket, verbose=verbose)
    client.fput_object(
        bucket,
        object_name,
        str(path),
        content_type=_content_type(path),
    )
    if verbose:
        print(f"[MINIO] Uploaded object: bucket={bucket}, object={object_name}")
    return object_name


def clear_bucket(bucket=MINIO_BUCKET, verbose=False):
    client = _get_minio_client()
    if verbose:
        print(f"[MINIO] Clearing bucket: endpoint={MINIO_ENDPOINT}, bucket={bucket}")
    _ensure_public_bucket(client, bucket, verbose=verbose)

    deleted = 0
    for item in client.list_objects(bucket, recursive=True):
        if verbose:
            print(f"[MINIO] Removing object: bucket={bucket}, object={item.object_name}")
        client.remove_object(bucket, item.object_name)
        deleted += 1

    if verbose:
        print(f"[MINIO] Cleared bucket: bucket={bucket}, deleted={deleted}")
    return deleted


def list_object_names(bucket=MINIO_BUCKET):
    client = _get_minio_client()
    if not client.bucket_exists(bucket):
        return []

    return sorted(item.object_name for item in client.list_objects(bucket, recursive=True))

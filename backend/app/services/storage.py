"""文件上传存储服务（MinIO / S3 兼容）"""

import os
import uuid
from pathlib import PurePosixPath

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "pdf", "png", "webp"}
ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}

_storage_client = None


def get_storage_client():
    global _storage_client
    if _storage_client is None:
        endpoint = os.environ.get("minio_endpoint", "http://localhost:9000")
        _storage_client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.environ.get("minio_access_key", "minioadmin"),
            aws_secret_access_key=os.environ.get("minio_secret_key", "minioadmin"),
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )
    return _storage_client


def validate_file(filename: str, content_type: str, size: int) -> str | None:
    ext = PurePosixPath(filename).suffix.lstrip(".").lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"File extension '.{ext}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    if content_type not in ALLOWED_MIME_TYPES:
        return f"Content type '{content_type}' is not allowed"
    if size > MAX_FILE_SIZE:
        return f"File size ({size} bytes) exceeds maximum allowed ({MAX_FILE_SIZE} bytes)"
    return None


def build_file_key(tenant_id: str, module: str, filename: str) -> str:
    ext = PurePosixPath(filename).suffix.lstrip(".").lower()
    file_uuid = uuid.uuid4().hex
    return f"{tenant_id}/{module}/{file_uuid}.{ext}"


def validate_file_key(file_key: str) -> str | None:
    path = PurePosixPath(file_key)
    if file_key.startswith("/") or ".." in path.parts:
        return "Invalid file path"
    if not path.suffix:
        return "Invalid file path"
    return None


async def upload_file(
    tenant_id: str,
    module: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict:
    error = validate_file(filename, content_type, len(content))
    if error:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=error)

    key = build_file_key(tenant_id, module, filename)
    bucket = os.environ.get("minio_bucket", "yimatong")

    client = get_storage_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType=content_type,
    )

    file_id = str(uuid.uuid4())
    return {
        "file_id": file_id,
        "file_url": key,
        "filename": filename,
        "content_type": content_type,
        "size": len(content),
        "module": module,
    }


def get_file_record(file_id: str) -> dict | None:
    # In production this would query the database
    # For now return None to indicate record not found
    return None


async def get_file_info(tenant_id: str, file_id: str) -> dict:
    record = get_file_record(file_id)
    if not record:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="File not found")

    if record["tenant_id"] != tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="File not found")

    bucket = os.environ.get("minio_bucket", "yimatong")
    client = get_storage_client()
    download_url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": record["file_url"]},
        ExpiresIn=3600,
    )

    return {
        **record,
        "download_url": download_url,
    }


async def get_public_file(file_key: str) -> dict:
    error = validate_file_key(file_key)
    if error:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=error)

    bucket = os.environ.get("minio_bucket", "yimatong")
    client = get_storage_client()
    try:
        obj = client.get_object(Bucket=bucket, Key=file_key)
    except ClientError as exc:
        from fastapi import HTTPException

        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 404)
        raise HTTPException(status_code=404 if status == 404 else 502, detail="File not found") from exc

    return {
        "body": obj["Body"],
        "content_type": obj.get("ContentType") or "application/octet-stream",
    }

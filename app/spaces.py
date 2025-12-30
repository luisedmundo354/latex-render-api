import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.client import Config

def get_s3_client():
    # Example endpoint: https://nyc3.digitaloceanspaces.com
    endpoint = os.environ["SPACES_ENDPOINT"]
    region = os.environ["SPACES_REGION"]
    key = os.environ["SPACES_KEY"]
    secret = os.environ["SPACES_SECRET"]

    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4"),
    )


def make_zip_object_key(prefix: str = "uploads") -> str:
    # uploads/2025-12-30/<uuid>.zip
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{prefix}/{day}/{uuid.uuid4().hex}.zip"


def presign_put_zip(s3, bucket: str, key: str, expires_seconds: int = 300) -> str:
    # If you include ContentType here, the client should send the same Content-Type header on PUT
    return s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentType": "application/zip",
        },
        ExpiresIn=expires_seconds,
    )


def fetch_object_bytes(s3, bucket: str, key: str) -> bytes:
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def delete_object(s3, bucket: str, key: str) -> None:
    s3.delete_object(Bucket=bucket, Key=key)

"""File storage. Local disk is the development backend. S3 is optional."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decrypt_secret
from app.models import Credential, Organization


class Storage:
    def __init__(self, organization: Organization | None = None, credential: Credential | None = None) -> None:
        settings = get_settings()
        self.organization = organization
        backend = settings.storage_backend
        self.bucket = settings.s3_bucket
        self.endpoint = settings.s3_endpoint
        self.region = settings.s3_region or "us-east-1"
        self.access_key = settings.s3_access_key
        self.secret_key = settings.s3_secret_key
        if organization and organization.storage_backend in {"local", "s3"}:
            backend = organization.storage_backend
            if organization.s3_bucket:
                self.bucket = organization.s3_bucket
            if organization.s3_endpoint:
                self.endpoint = organization.s3_endpoint
            if organization.s3_region:
                self.region = organization.s3_region
        if credential is not None:
            raw = decrypt_secret(credential.ciphertext)
            access, _, secret = raw.partition("\n")
            self.access_key = access
            self.secret_key = secret
        self.backend = backend
        self.root = Path(settings.storage_dir).resolve()

    def _local_path(self, key: str) -> Path:
        relative = Path(key)
        if relative.is_absolute() or ".." in relative.parts:
            raise AppError(400, "file_processing_error", "Storage key is not valid.")
        path = (self.root / relative).resolve()
        if not str(path).startswith(str(self.root)):
            raise AppError(400, "file_processing_error", "Storage key escapes the storage root.")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        digest = hashlib.sha256(data).hexdigest()
        if self.backend == "s3":
            self._s3().put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
            return digest
        path = self._local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return digest

    def get_bytes(self, key: str) -> bytes:
        if self.backend == "s3":
            response = self._s3().get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        path = self._local_path(key)
        if not path.is_file():
            raise AppError(404, "file_processing_error", "Stored file was not found.")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        if not key:
            return False
        if self.backend == "s3":
            try:
                self._s3().head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:
                return False
        return self._local_path(key).is_file()

    def delete(self, key: str) -> None:
        if not key:
            return
        if self.backend == "s3":
            self._s3().delete_object(Bucket=self.bucket, Key=key)
            return
        path = self._local_path(key)
        if path.is_file():
            path.unlink()

    def probe(self) -> dict:
        key = "_health/probe.txt"
        payload = b"ok"
        digest = self.put_bytes(key, payload, "text/plain")
        read_back = self.get_bytes(key)
        if read_back != payload:
            raise AppError(500, "configuration_error", "Storage probe wrote a file that could not be read back.")
        return {"backend": self.backend, "writable": True, "sha256": digest, "bucket": self.bucket if self.backend == "s3" else ""}

    def _s3(self):
        if not self.bucket or not self.access_key or not self.secret_key:
            raise AppError(
                400,
                "configuration_error",
                "S3 storage is selected but the bucket or credentials are missing.",
            )
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint or None,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )

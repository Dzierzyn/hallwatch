"""Backup zdarzen do storage S3-compatible (Cloudflare R2 / Backblaze B2 / MinIO / AWS).

Sens backupu poza mieszkaniem jest oczywisty: jesli ktos zabierze komputer albo
kamere, nagranie zdarzenia i tak jest bezpieczne. Upload leci w tle, zeby nigdy
nie zablokowac petli przetwarzania obrazu.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .config import CloudCfg

log = logging.getLogger(__name__)

CONTENT_TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class CloudUploader:
    """Kolejka uploadow obslugiwana przez watek w tle."""

    def __init__(self, cfg: CloudCfg, on_uploaded: Callable[[int, str], None] | None = None) -> None:
        self.cfg = cfg
        self.on_uploaded = on_uploaded
        self._queue: queue.Queue[tuple[int, Path] | None] = queue.Queue(maxsize=256)
        self._thread: threading.Thread | None = None
        self._client = None
        self.enabled = False
        self.error: str | None = None
        self.uploaded = 0
        self.failed = 0

        if not cfg.enabled:
            return
        key = os.environ.get("HALLWATCH_S3_KEY")
        secret = os.environ.get("HALLWATCH_S3_SECRET")
        if not (cfg.bucket and key and secret):
            self.error = "brak cloud.bucket lub HALLWATCH_S3_KEY / HALLWATCH_S3_SECRET"
            log.warning("Chmura wylaczona: %s", self.error)
            return
        try:
            import boto3
            from botocore.config import Config as BotoConfig

            self._client = boto3.client(
                "s3",
                endpoint_url=cfg.endpoint_url or None,
                aws_access_key_id=key,
                aws_secret_access_key=secret,
                region_name=cfg.region or None,
                config=BotoConfig(
                    signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}
                ),
            )
            self.enabled = True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.error("Nie udalo sie zainicjowac klienta S3: %s", exc)

    def start(self) -> "CloudUploader":
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="cloud-upload", daemon=True)
            self._thread.start()
        return self

    def key_for(self, path: Path, ts: float | None = None) -> str:
        day = time.strftime("%Y/%m/%d", time.localtime(ts or path.stat().st_mtime))
        prefix = self.cfg.prefix.strip("/")
        return f"{prefix}/{day}/{path.name}" if prefix else f"{day}/{path.name}"

    def enqueue(self, event_id: int, path: Path) -> None:
        if not self.enabled or not path.exists():
            return
        try:
            self._queue.put_nowait((event_id, path))
        except queue.Full:
            log.warning("Kolejka uploadu pelna - pomijam %s", path.name)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            event_id, path = item
            try:
                key = self.key_for(path)
                extra = {"ContentType": CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")}
                self._client.upload_file(str(path), self.cfg.bucket, key, ExtraArgs=extra)  # type: ignore[union-attr]
                self.uploaded += 1
                log.info("Wyslano do chmury: %s", key)
                if self.on_uploaded is not None:
                    self.on_uploaded(event_id, key)
                if self.cfg.delete_local_after_upload:
                    path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                self.failed += 1
                log.error("Upload %s nieudany: %s", path.name, exc)
            finally:
                self._queue.task_done()

    def presigned_url(self, key: str, expires_s: int = 3600) -> str | None:
        if not self.enabled:
            return None
        try:
            return self._client.generate_presigned_url(  # type: ignore[union-attr]
                "get_object",
                Params={"Bucket": self.cfg.bucket, "Key": key},
                ExpiresIn=expires_s,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Nie udalo sie podpisac URL: %s", exc)
            return None

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=30.0)

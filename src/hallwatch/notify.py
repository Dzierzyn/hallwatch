"""Push notifications through ntfy.sh (no accounts, no tokens, works on a phone).

You install the ntfy app, subscribe to your own random topic, and receive a push
with a thumbnail of the event. Throttling makes sure that a single walk down the
corridor does not send 40 notifications.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import requests

from .config import NotifyCfg

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: NotifyCfg) -> None:
        self.cfg = cfg
        self._last_sent = 0.0
        self._lock = threading.Lock()
        self.enabled = cfg.enabled and bool(cfg.ntfy_topic)
        if cfg.enabled and not self.enabled:
            log.warning("Notifications enabled but notify.ntfy_topic is missing from the config")

    @property
    def url(self) -> str:
        return f"{self.cfg.ntfy_server.rstrip('/')}/{self.cfg.ntfy_topic}"

    def send(
        self,
        title: str,
        message: str,
        priority: str = "default",
        tags: str = "eyes",
        snapshot: Path | None = None,
        force: bool = False,
    ) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            now = time.time()
            if not force and now - self._last_sent < self.cfg.min_interval_s:
                return False
            self._last_sent = now

        headers = {
            "Title": title.encode("utf-8"),
            "Priority": priority,
            "Tags": tags,
        }
        try:
            if snapshot and self.cfg.attach_snapshot and snapshot.exists():
                headers["Filename"] = snapshot.name
                headers["Message"] = message.encode("utf-8")
                with snapshot.open("rb") as fh:
                    resp = requests.put(self.url, data=fh, headers=headers, timeout=10)
            else:
                resp = requests.post(
                    self.url, data=message.encode("utf-8"), headers=headers, timeout=10
                )
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 - a notification must not take down the pipeline
            log.warning("Failed to send notification: %s", exc)
            return False

    def send_async(self, *args: object, **kwargs: object) -> None:
        threading.Thread(target=self.send, args=args, kwargs=kwargs, daemon=True).start()  # type: ignore[arg-type]

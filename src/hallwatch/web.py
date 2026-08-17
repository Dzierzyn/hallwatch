"""Dashboard: podglad live (MJPEG) + API zdarzen i statystyk."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from .config import Config
from .pipeline import Pipeline

log = logging.getLogger(__name__)

BOUNDARY = "hallwatchframe"
STATIC = Path(__file__).parent / "static"


def create_app(cfg: Config, pipeline: Pipeline) -> FastAPI:
    app = FastAPI(title="HallWatch", docs_url="/api/docs", redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/stream.mjpg")
    def stream() -> StreamingResponse:
        """MJPEG: kolejne klatki JPEG w jednej odpowiedzi multipart.

        Zaden JS nie jest potrzebny - <img src="/stream.mjpg"> po prostu dziala.
        """
        interval = 1.0 / max(1.0, cfg.web.stream_fps)

        def frames():
            last_seq = -1
            while True:
                jpeg, seq = pipeline.latest_jpeg()
                if jpeg is not None and seq != last_seq:
                    last_seq = seq
                    yield (
                        b"--" + BOUNDARY.encode() + b"\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
                    )
                time.sleep(interval)

        return StreamingResponse(
            frames(),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store, no-cache", "Pragma": "no-cache"},
        )

    @app.get("/api/state")
    def api_state() -> JSONResponse:
        s = pipeline.snapshot_state()
        data = {k: v for k, v in s.__dict__.items() if k != "jpeg"}
        data["uptime_s"] = time.time() - s.started_at
        data["audio_enabled"] = cfg.audio.enabled
        data["camera_name"] = cfg.camera.name
        data["retention_days"] = cfg.recording.retention_days
        data["privacy_masks"] = len(cfg.privacy.masks)
        return JSONResponse(data)

    @app.get("/api/events")
    def api_events(limit: int = 50, kind: str | None = None) -> JSONResponse:
        events = pipeline.store.recent_events(limit=min(limit, 500), kind=kind)
        return JSONResponse(
            [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "started_at": e.started_at,
                    "ended_at": e.ended_at,
                    "duration_s": (e.ended_at - e.started_at) if e.ended_at else None,
                    "max_persons": e.max_persons,
                    "count_in": e.count_in,
                    "count_out": e.count_out,
                    "peak_dbfs": e.peak_dbfs,
                    "clip": f"/media/{e.clip_path}" if e.clip_path else None,
                    "snapshot": f"/media/{e.snapshot_path}" if e.snapshot_path else None,
                    "cloud": bool(e.cloud_key),
                }
                for e in events
            ]
        )

    @app.post("/api/wake")
    def wake(source: str = "api") -> JSONResponse:
        """Sygnal wybudzenia dla trybu on_demand (kamera na baterii).

        Uniwersalny punkt wejscia: curl, automatyzacja z Home Assistanta,
        webhook z huba kamery, IFTTT - cokolwiek potrafi zrobic POST.
        """
        accepted = pipeline.wake(source)
        return JSONResponse(
            {
                "accepted": accepted,
                "mode": cfg.camera.mode,
                "detail": (
                    "sesja zostanie otwarta"
                    if accepted
                    else "tryb continuous - strumien jest juz otwarty, wybudzanie zbedne"
                ),
            },
            status_code=202 if accepted else 200,
        )

    @app.post("/api/watch")
    def watch() -> JSONResponse:
        """Puls 'patrze' z dashboardu - trzyma sesje otwarta na czas podgladu."""
        return JSONResponse(pipeline.hold_session())

    @app.get("/api/stats")
    def api_stats(minutes: int = 180) -> JSONResponse:
        since = time.time() - minutes * 60
        return JSONResponse(
            {"minutes": pipeline.store.stats_since(since), "totals": pipeline.store.totals(since)}
        )

    @app.get("/media/{path:path}")
    def media(path: str) -> FileResponse:
        """Serwuje klipy i miniatury, blokujac wyjscie poza katalog nagran."""
        base = cfg.path(cfg.recording.dir).resolve()
        target = (cfg.root / path).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            raise HTTPException(status_code=404, detail="nie znaleziono")
        return FileResponse(target)

    @app.get("/api/events/{event_id}/cloud")
    def cloud_link(event_id: int) -> RedirectResponse:
        event = pipeline.store.event(event_id)
        if event is None or not event.cloud_key:
            raise HTTPException(status_code=404, detail="brak kopii w chmurze")
        url = pipeline.uploader.presigned_url(event.cloud_key)
        if not url:
            raise HTTPException(status_code=503, detail="nie udalo sie podpisac URL")
        return RedirectResponse(url)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        s = pipeline.snapshot_state()
        healthy = s.frames_total > 0 and s.last_error is None
        return JSONResponse(
            {"ok": healthy, "frames": s.frames_total, "fps": round(s.fps, 2), "error": s.last_error},
            status_code=200 if healthy else 503,
        )

    return app

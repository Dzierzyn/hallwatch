"""Dashboard: live preview (MJPEG) plus an events and statistics API, for many cameras.

Every endpoint accepts a ?camera=<slug> parameter. Omitting it means the first camera
in the configuration, so that older links keep working.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from .config import Config
from .pipeline import PipelineManager

log = logging.getLogger(__name__)

BOUNDARY = "hallwatchframe"
STATIC = Path(__file__).parent / "static"


def create_app(cfg: Config, manager: PipelineManager) -> FastAPI:
    app = FastAPI(title="HallWatch", docs_url="/api/docs", redoc_url=None)

    def pick(camera: str | None):
        try:
            return manager.get(camera or None)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no such camera '{camera}'") from None

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/cameras")
    def api_cameras() -> JSONResponse:
        """The camera list, from which the dashboard builds its switch."""
        return JSONResponse([
            {
                "slug": p.prof.slug,
                "name": p.prof.name,
                "mode": p.prof.mode,
                "classes": p.prof.detection.classes,
                "recording": p.prof.recording.enabled,
                "sampling": (
                    {
                        "every_minutes": p.prof.sampling.every_minutes,
                        "seconds": p.prof.sampling.seconds,
                        "duty_cycle": round(p.prof.sampling.duty_cycle, 4),
                    }
                    if p.prof.mode == "sampling"
                    else None
                ),
            }
            for p in manager.pipelines.values()
        ])

    @app.get("/stream.mjpg")
    def stream(camera: str | None = Query(default=None)) -> StreamingResponse:
        """MJPEG: successive JPEG frames in a single multipart response."""
        pipeline = pick(camera)
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
    def api_state(camera: str | None = Query(default=None)) -> JSONResponse:
        pipeline = pick(camera)
        prof = pipeline.prof
        s = pipeline.snapshot_state()
        data = {k: v for k, v in s.__dict__.items() if k != "jpeg"}
        data["uptime_s"] = time.time() - s.started_at
        data["audio_enabled"] = prof.audio.enabled
        data["camera_name"] = prof.name
        data["camera_slug"] = prof.slug
        data["retention_days"] = prof.recording.retention_days
        data["privacy_masks"] = len(prof.privacy.masks)
        data["classes"] = prof.detection.classes
        data["recording_enabled"] = prof.recording.enabled
        if prof.mode == "sampling":
            data["duty_cycle"] = round(prof.sampling.duty_cycle, 4)
        return JSONResponse(data)

    @app.get("/api/events")
    def api_events(
        limit: int = 50, kind: str | None = None, camera: str | None = Query(default=None)
    ) -> JSONResponse:
        name = None
        if camera:
            name = pick(camera).prof.name
        events = manager.store.recent_events(limit=min(limit, 500), kind=kind, camera=name)
        return JSONResponse([
            {
                "id": e.id,
                "camera": e.camera,
                "kind": e.kind,
                "started_at": e.started_at,
                "ended_at": e.ended_at,
                "duration_s": (e.ended_at - e.started_at) if e.ended_at else None,
                "max_persons": e.max_persons,
                "count_in": e.count_in,
                "count_out": e.count_out,
                "peak_dbfs": e.peak_dbfs,
                "sampled": bool(e.sampled),
                "sample_weight": e.sample_weight,
                "clip": f"/media/{e.clip_path}" if e.clip_path else None,
                "snapshot": f"/media/{e.snapshot_path}" if e.snapshot_path else None,
                "cloud": bool(e.cloud_key),
            }
            for e in events
        ])

    @app.post("/api/wake")
    def wake(source: str = "api", camera: str | None = Query(default=None)) -> JSONResponse:
        """Wake signal for on_demand mode (battery camera)."""
        pipeline = pick(camera)
        accepted = pipeline.wake(source)
        return JSONResponse(
            {
                "accepted": accepted,
                "camera": pipeline.prof.name,
                "mode": pipeline.prof.mode,
                "detail": (
                    "a session will be opened"
                    if accepted
                    else f"tryb {pipeline.prof.mode} - wybudzanie zbedne"
                ),
            },
            status_code=202 if accepted else 200,
        )

    @app.post("/api/watch")
    def watch(camera: str | None = Query(default=None)) -> JSONResponse:
        """The 'I am watching' heartbeat from the dashboard, holding the session open."""
        return JSONResponse(pick(camera).hold_session())

    @app.get("/api/stats")
    def api_stats(minutes: int = 180) -> JSONResponse:
        since = time.time() - minutes * 60
        return JSONResponse(
            {"minutes": manager.store.stats_since(since), "totals": manager.store.totals(since)}
        )

    @app.get("/media/{path:path}")
    def media(path: str) -> FileResponse:
        """Serwuje klipy i miniatury, blokujac wyjscie poza katalogi nagran."""
        allowed = [cfg.path(cam.recording.dir).resolve() for cam in cfg.cameras]
        target = (cfg.root / path).resolve()
        if not any(str(target).startswith(str(base)) for base in allowed) or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    @app.get("/api/events/{event_id}/cloud")
    def cloud_link(event_id: int) -> RedirectResponse:
        event = manager.store.event(event_id)
        if event is None or not event.cloud_key:
            raise HTTPException(status_code=404, detail="no cloud copy")
        url = manager.uploader.presigned_url(event.cloud_key)
        if not url:
            raise HTTPException(status_code=503, detail="nie udalo sie podpisac URL")
        return RedirectResponse(url)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Healthy = every camera processed frames and none reported an error."""
        cams = {}
        healthy = True
        for slug, pipe in manager.pipelines.items():
            s = pipe.snapshot_state()
            # a camera in sampling/on_demand mode may be asleep - that is not an error
            live_ok = s.frames_total > 0 or pipe.prof.mode != "continuous"
            ok = live_ok and s.last_error is None
            healthy &= ok
            cams[slug] = {
                "ok": ok, "frames": s.frames_total, "fps": round(s.fps, 2),
                "mode": pipe.prof.mode, "session_active": s.session_active,
                "error": s.last_error,
            }
        return JSONResponse({"ok": healthy, "cameras": cams}, status_code=200 if healthy else 503)

    return app

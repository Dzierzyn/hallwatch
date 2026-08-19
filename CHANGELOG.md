# Changelog

All notable changes to HallWatch are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- MIT license, contribution guide, security policy, issue/PR templates, CI.
- Out-of-the-box single-webcam default config; real-world setups moved to `examples/`.
- Docker image + compose file for RTSP deployments.
- Optional dashboard auth (`web.auth_token`) with a startup warning when
  binding beyond localhost without it.
- Configurable video codec (`recording.codec`) for hardware encoders
  (e.g. `h264_v4l2m2m` on Raspberry Pi).
- pytest suite (unit + integration markers) replacing ad-hoc checks;
  `hallwatch selftest` remains as the user-facing smoke test.

### Fixed
- Odd frame heights (portrait phone cameras) crashed the H.264 encoder and
  silently deleted every clip; dimensions are now rounded to even values.
- Databases created by pre-multi-camera versions failed to open (index created
  before migration added its column).

### Changed
- Whole project - code, config, dashboard, docs - is now English-first
  (Polish README kept as a translation).

## [0.1.0] - 2026-08-18

Initial public version: motion-gated YOLO11 person/vehicle detection with
ByteTrack, line-crossing counts, zones, privacy masks, pre-roll event
recording, SQLite event store, MJPEG dashboard, ntfy notifications,
S3-compatible backup, three camera modes (continuous / on_demand / sampling)
and an optional analytics stack (dbt + ML forecasting/anomalies).

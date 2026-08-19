# Security Policy

HallWatch is a self-hosted camera tool. It processes video of real people and
homes, so we take reports seriously.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | yes       |

## Reporting a vulnerability

Please report vulnerabilities **privately** via
[GitHub Security Advisories](https://github.com/Dzierzyn/hallwatch/security/advisories/new).
If that does not work for you, email `dzierzeckidaniel@gmail.com` with
`[hallwatch security]` in the subject.

You can expect an acknowledgement within **7 days**. Please give us a
reasonable window to ship a fix before public disclosure.

## What counts as a vulnerability here

- Privacy-mask bypass: any way masked pixels can reach detection, disk,
  the dashboard or the cloud.
- Path traversal in `/media/` or anywhere else that serves files.
- Authentication bypass when `web.auth_token` is set.
- Credential leakage (RTSP passwords, S3 keys) into logs, clips metadata or
  API responses.

## Deployment model (what is *not* a vulnerability)

- The dashboard binds to `127.0.0.1` by default and **has no authentication
  unless you set `web.auth_token`**. Exposing it to a network without a token
  is a deployment error, not a vulnerability - but we document it loudly and
  warn at startup.
- Camera credentials live in your `config.yaml` / environment; protecting that
  file is up to you (`config.yaml` with real credentials should never be
  committed - see `.gitignore`).

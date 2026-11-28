# Security policy

## Supported versions

| Version | Security fixes |
| --- | --- |
| 0.7.x | Yes |
| Older than 0.7 | No |

## Reporting a vulnerability

On the repository's [Security tab](https://github.com/rohitdev14/Logentra-OS/security), select **Report a vulnerability** to submit a private report. Please do not open a public issue for a vulnerability. Include the affected version and deployment profile, and redact credentials and private deployment data.

Security reports are handled on a best-effort basis by a single maintainer.

## Self-hosted deployments

Operators are responsible for securing their self-hosted deployments, including network access, credentials, and updates.

- The app defaults to TCP port `8787`. The default Compose profile publishes it on `127.0.0.1`.
- The production HTTPS overlay defaults to `127.0.0.1:8443` on the host and forwards to TCP port `443` in the proxy container.
- The optional PostgreSQL service uses TCP port `5432` within Compose. The optional MQTT integration defaults to broker port `1883`.
- App settings live in `config.yaml` (see `config.example.yaml`) or in `LOGENTRA_*` environment variables. Compose settings live in `compose.yaml`, `compose.production.yaml`, and the local `.env` file (see `.env.example`). The HTTPS proxy configuration lives in `deployment/nginx.conf`.

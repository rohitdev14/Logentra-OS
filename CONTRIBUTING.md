# Contributing to Logbook

Thanks for considering a contribution. This is an early-release project; small, focused changes are easier to review than large rewrites.

## Development setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
python run.py --no-browser
```

The API is at `http://127.0.0.1:8787/`, Swagger at `/docs`. After editing UI sources under `ui/src/`, rebuild the bundled page:

```bash
python ui/build.py
```

Optional starter packs are stored in `ui/src/starter_packs.json` (not hardcoded into the server).

## Tests

Please run the API suite before opening a pull request:

```bash
python -m pytest tests/test_api.py tests/test_ingest.py tests/test_oor.py -q
```

The Playwright smoke test (`tests/test_ui.py`) is optional and needs Chrome or Chromium on the machine. GitHub Actions runs only the API/ingest/event tests.

## Pull requests

- Use a feature branch and a short, descriptive commit message.
- Keep changes scoped. If you add an API route, add a test and mention it in `CHANGELOG.md` under an Unreleased heading.
- Do not commit `config.yaml`, database files, virtualenvs, or secrets.
- Do not include confidential site data, real plant setpoints, or employer-internal procedures in examples, fixtures, or screenshots. Demo data should stay fictional and generic.
- The MIT license applies to contributions unless you say otherwise in the PR.

## Reporting issues

Include the Logbook version (`GET /api/health` or the footer), the database backend (SQLite or PostgreSQL), and steps to reproduce. Feature ideas are welcome; please check existing issues first.

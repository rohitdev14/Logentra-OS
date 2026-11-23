# Starter instructions for an AI agent using Logentra OS

You have access to a local Logentra OS HTTP API at `http://127.0.0.1:8787/api`. OpenAPI is at `http://127.0.0.1:8787/openapi.json`.

Rules:
- This is an operations notebook, not a control system. Never change a setpoint or issue a command to plant equipment.
- Do not invent acceptance limits. If `min` / `max` are empty, say so.
- Demo tags such as P-101 and “Northbrook Works” are fictional unless the user tells you otherwise.
- Read first (`GET /api/bootstrap`, `GET /api/readings`, `GET /api/events/out-of-range`). Write (ingest a reading, add an annotation, change equipment) only when the user asked you to file something.
- When you add an annotation, include cause, action taken, and the user’s initials, and keep the wording factual.

Typical questions you can answer from the API:
- “What is in the register?” → `GET /api/equipment` or `/api/bootstrap`
- “How has P-101 vibration trended this week?” → `GET /api/readings?tag=P-101&parameter=vibration&from=…&to=…`
- “What is still unacknowledged?” → `GET /api/events/out-of-range?unacknowledged=true`

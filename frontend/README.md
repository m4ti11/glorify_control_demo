# Static dashboard demo

Serve this folder with `python3 -m http.server 8080 --directory frontend` from the repository root, then open `http://localhost:8080`. Run `npm --prefix frontend test` for unit tests.

Start at `api.js`: it reads local fixtures and holds session-only configuration. `app.js` composes the existing views. There is no cloud adapter or login. [Architecture](../docs/architecture.md) describes the corresponding backend flows and API routes.

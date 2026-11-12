"""Backend entry point (JSON API; also serves ../frontend/dist when it exists).

Development::

    python run.py            # API on http://127.0.0.1:5000
    (in ../frontend)  npm run dev   # site on http://localhost:5173, proxies /api here

Production (any WSGI server), for example::

    waitress-serve --listen=0.0.0.0:8000 run:app
"""

import os

from app import create_app
from app.config import ConfigError

try:
    app = create_app()
except ConfigError as exc:
    # Fail with a readable message instead of a stack trace.
    raise SystemExit(f"Configuration error: {exc}") from None


if __name__ == "__main__":
    if app.config["ENV_NAME"] == "production":
        raise SystemExit(
            "Refusing to start the Flask development server with FLASK_ENV=production. "
            "Use a WSGI server instead, for example: waitress-serve --listen=0.0.0.0:8000 run:app"
        )
    host = os.environ.get("HOST", "127.0.0.1")
    # The Werkzeug debugger allows code execution, so it is only ever enabled
    # when the server is reachable from this machine alone.
    debug = app.config["DEBUG"] and host in ("127.0.0.1", "localhost", "::1")
    app.run(
        host=host,
        port=int(os.environ.get("PORT", "5000")),
        debug=debug,
        # The reloader starts a second process, which would load the model twice.
        use_reloader=False,
    )

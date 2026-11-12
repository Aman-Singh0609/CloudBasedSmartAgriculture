"""Local stand-in for the OpenWeatherMap API, for development without an API key.

Usage (from the backend folder)::

    python scripts/dev_weather_stub.py            # listens on 127.0.0.1:5055

then start the backend with::

    WEATHER_API_KEY=local-dev-key WEATHER_API_BASE_URL=http://127.0.0.1:5055/weather python run.py

It answers in the provider's response format with made-up numbers, so the
whole weather flow can be exercised end to end. Special city names trigger
failure cases: "Atlantis" (not found), "Slowtown" (times out), "Brokenville"
(provider error), "Garbage" (malformed body), "Drytown" (no rain block).
It only ever binds to the loopback address and is never used in production.
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HOST, PORT = "127.0.0.1", 5055


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console quiet
        return

    def _send(self, status: int, body: str, content_type: str = "application/json") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - name required by http.server
        query = parse_qs(urlsplit(self.path).query)
        city = (query.get("q") or [""])[0]
        key = city.strip().lower()

        if not (query.get("appid") or [""])[0]:
            return self._send(401, json.dumps({"cod": 401, "message": "Invalid API key"}))
        if key == "atlantis":
            return self._send(404, json.dumps({"cod": "404", "message": "city not found"}))
        if key == "slowtown":
            time.sleep(12)
            return self._send(200, "{}")
        if key == "brokenville":
            return self._send(500, json.dumps({"cod": 500, "message": "internal error"}))
        if key == "garbage":
            return self._send(200, "<html>gateway error</html>", "text/html")

        payload = {
            "name": city.title(),
            "sys": {"country": "IN"},
            "main": {"temp": 27.3, "humidity": 64},
            "weather": [{"description": "light rain"}],
            "dt": int(time.time()),
        }
        if key != "drytown":
            payload["rain"] = {"1h": 0.4}
        return self._send(200, json.dumps(payload))


def serve(port: int = PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), Handler)


if __name__ == "__main__":
    server = serve(int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
    print(f"Weather stub listening on http://{HOST}:{server.server_port}/weather (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

"""End-to-end smoke test against a RUNNING server, over real HTTP.

Unlike the pytest suite (which calls the application in-process), this talks to
a live server exactly as a browser would: session cookie, CSRF token, real
database, security headers. It creates the demo account from
``tests/fixtures/demo_user.json`` and a handful of analyses, so it doubles as a
way to get sample data into a development database.

Usage (from the backend folder, with the server running)::

    python scripts/live_smoke_test.py                       # http://127.0.0.1:5000
    python scripts/live_smoke_test.py http://127.0.0.1:8000

Never point this at production: it creates an account and saves analyses.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "demo_user.json"

SAMPLES = [
    {"nitrogen": 90, "phosphorus": 42, "potassium": 43, "temperature": 20.8, "humidity": 82, "ph": 6.5, "rainfall": 202.9},
    {"nitrogen": 85, "phosphorus": 58, "potassium": 41, "temperature": 21.7, "humidity": 80, "ph": 7.0, "rainfall": 226.6},
    {"nitrogen": 60, "phosphorus": 55, "potassium": 44, "temperature": 23.0, "humidity": 82, "ph": 7.8, "rainfall": 263.9},
    {"nitrogen": 71, "phosphorus": 54, "potassium": 16, "temperature": 22.6, "humidity": 63, "ph": 5.7, "rainfall": 87.7},
    {"nitrogen": 40, "phosphorus": 72, "potassium": 77, "temperature": 17.0, "humidity": 16.9, "ph": 7.4, "rainfall": 88.5},
    {"nitrogen": 20, "phosphorus": 67, "potassium": 20, "temperature": 20.0, "humidity": 21.6, "ph": 5.7, "rainfall": 105.9},
    {"nitrogen": 117, "phosphorus": 46, "potassium": 19, "temperature": 24.0, "humidity": 80, "ph": 6.9, "rainfall": 80.0},
    {"nitrogen": 101, "phosphorus": 28, "potassium": 30, "temperature": 25.5, "humidity": 58, "ph": 6.8, "rainfall": 158.0},
    {"nitrogen": 100, "phosphorus": 82, "potassium": 50, "temperature": 27.4, "humidity": 80, "ph": 6.0, "rainfall": 104.9},
    {"nitrogen": 20, "phosphorus": 27, "potassium": 30, "temperature": 31.2, "humidity": 50, "ph": 5.8, "rainfall": 94.7},
    {"nitrogen": 99, "phosphorus": 17, "potassium": 50, "temperature": 25.6, "humidity": 85, "ph": 6.5, "rainfall": 50.8},
    {"nitrogen": 90, "phosphorus": 20, "potassium": 43, "temperature": 27.3, "humidity": 64, "ph": 6.5, "rainfall": 900},
]


class Check:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def __call__(self, condition: bool, label: str, detail: object = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed.append(label)
            print(f"  FAIL  {label}  {detail}")


def main(base: str) -> int:
    check = Check()
    user = json.loads(FIXTURE.read_text(encoding="utf-8"))
    http = requests.Session()
    timings: dict[str, list[float]] = {}

    def call(method: str, path: str, *, csrf: bool = True, **kwargs):
        headers = kwargs.pop("headers", {})
        if csrf and method != "GET":
            headers["X-CSRFToken"] = http.get(f"{base}/api/csrf-token", timeout=10).json()["data"]["csrf_token"]
        started = time.perf_counter()
        response = http.request(method, f"{base}{path}", headers=headers, timeout=20, **kwargs)
        route = re.sub(r"/[0-9a-f]{24}$", "/<id>", path.split("?")[0])
        timings.setdefault(f"{method} {route}", []).append((time.perf_counter() - started) * 1000)
        return response

    print(f"Smoke test against {base}\n\n[health and headers]")
    health = call("GET", "/health")
    check(health.status_code == 200 and health.json()["data"]["status"] == "ok", "health reports ok", health.text[:200])
    check(health.json()["data"]["checks"]["database"] == "ok", "database check ok")
    check(health.json()["data"]["checks"]["model"] == "ok", "model check ok")
    for name in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "X-Request-ID"):
        check(name in health.headers, f"header {name} present")
    check("unsafe-inline" not in health.headers.get("Content-Security-Policy", ""), "CSP forbids inline code")

    print("\n[anonymous access]")
    check(call("GET", "/api/predictions").status_code == 401, "history requires login")
    check(call("GET", "/api/auth/me").json()["data"]["authenticated"] is False, "not signed in initially")

    print("\n[CSRF is enforced on the live server]")
    forged = call("POST", "/api/auth/login", csrf=False, json={"email": user["email"], "password": user["password"]})
    check(forged.status_code == 400 and forged.json()["error"]["code"] == "CSRF_VALIDATION_FAILED", "login without CSRF token rejected", forged.text[:200])

    print("\n[signup and login]")
    signup = call("POST", "/api/auth/signup", json={**{k: user[k] for k in ("name", "email", "password")}, "confirm_password": user["password"]})
    check(signup.status_code in (201, 409), "signup succeeds (or account already exists)", signup.text[:200])
    wrong = call("POST", "/api/auth/login", json={"email": user["email"], "password": "Definitely-Wrong-1"})
    check(wrong.status_code == 401 and wrong.json()["error"]["message"] == "Invalid email or password.", "wrong password gives generic 401")
    login = call("POST", "/api/auth/login", json={"email": user["email"], "password": user["password"]})
    check(login.status_code == 200, "login succeeds", login.text[:200])
    cookie = login.headers.get("Set-Cookie", "")
    check("HttpOnly" in cookie and "SameSite=Lax" in cookie, "session cookie is HttpOnly and SameSite=Lax")
    check(call("GET", "/api/auth/me").json()["data"]["user"]["email"] == user["email"], "session identifies the user")

    print("\n[validation is enforced by the backend]")
    bad = call("POST", "/api/predictions", json={**SAMPLES[0], "ph": 15, "nitrogen": "abc"})
    check(bad.status_code == 422 and set(bad.json()["error"]["details"]) == {"ph", "nitrogen"}, "invalid readings rejected with field messages", bad.text[:200])
    check(call("POST", "/api/predictions", data="{broken", headers={"Content-Type": "application/json"}).status_code == 400, "malformed JSON rejected")

    print("\n[weather through the backend]")
    weather = call("GET", "/api/weather?city=Pune")
    weather_ok = weather.status_code == 200
    if weather_ok:
        data = weather.json()["data"]
        check(data["rainfall"] is None and isinstance(data["temperature"], (int, float)), "weather returns temperature/humidity, never model rainfall")
    else:
        check(weather.status_code in (502, 503, 504), "weather unavailable is reported cleanly (BLOCKED: no provider configured)", weather.text[:200])

    print("\n[predictions]")
    created_ids = []
    for index, sample in enumerate(SAMPLES):
        payload = dict(sample)
        if index == 0 and weather_ok:
            payload["temperature"], payload["humidity"] = data["temperature"], data["humidity"]
        response = call("POST", "/api/predictions", json=payload)
        if response.status_code != 201:
            check(False, f"prediction {index + 1} created", response.text[:200])
            continue
        created_ids.append(response.json()["data"]["id"])
    check(len(created_ids) == len(SAMPLES), f"{len(SAMPLES)} predictions created")
    first = call("GET", f"/api/predictions/{created_ids[0]}").json()["data"]
    check({"recommended_crop", "confidence", "alternatives", "soil_analysis", "warnings", "limitations", "crop"} <= set(first), "result has the full structure")
    check(first["soil_analysis"]["method"] == "rule_based", "soil analysis labelled rule-based")
    if weather_ok:
        check(first["input_sources"]["temperature"] == "weather_api" and first["input_sources"]["rainfall"] == "manual", "server attributes weather values correctly")
    last = call("GET", f"/api/predictions/{created_ids[-1]}").json()["data"]
    check(last["reliability"] == "reduced" and "rainfall" in last["out_of_range_fields"], "out-of-range rainfall flagged as reduced reliability")

    print("\n[history]")
    page1 = call("GET", "/api/predictions").json()["data"]
    check(len(page1["items"]) == 10 and page1["pagination"]["total"] >= len(SAMPLES), "first page has 10 items")
    check(page1["items"][0]["id"] == created_ids[-1], "newest analysis is listed first")
    page2 = call("GET", "/api/predictions?page=2").json()["data"]
    check(len(page2["items"]) >= 2, "second page has the remainder")
    trends = call("GET", "/api/predictions/trends").json()["data"]["trends"]
    check({card["key"] for card in trends} == {"nitrogen", "phosphorus", "potassium", "ph"}, "trend data for N, P, K and pH")

    print("\n[isolation between users]")
    other = requests.Session()
    token = other.get(f"{base}/api/csrf-token", timeout=10).json()["data"]["csrf_token"]
    other_user = {"name": "Second Tester", "email": "second.tester@example.test", "password": "Orch4rd-Gate-Pasture", "confirm_password": "Orch4rd-Gate-Pasture"}
    other.post(f"{base}/api/auth/signup", json=other_user, headers={"X-CSRFToken": token}, timeout=20)
    other.post(f"{base}/api/auth/login", json=other_user, headers={"X-CSRFToken": token}, timeout=20)
    token = other.get(f"{base}/api/csrf-token", timeout=10).json()["data"]["csrf_token"]
    check(other.get(f"{base}/api/predictions/{created_ids[0]}", timeout=20).status_code == 404, "another user cannot read the analysis")
    check(other.delete(f"{base}/api/predictions/{created_ids[0]}", headers={"X-CSRFToken": token}, timeout=20).status_code == 404, "another user cannot delete the analysis")
    check(other.get(f"{base}/api/predictions", timeout=20).json()["data"]["items"] == [], "another user's history is empty")
    check(call("GET", f"/api/predictions/{created_ids[0]}").status_code == 200, "owner still has the analysis")

    print("\n[delete and logout]")
    check(call("DELETE", f"/api/predictions/{created_ids[1]}").status_code == 200, "owner can delete an analysis")
    check(call("GET", f"/api/predictions/{created_ids[1]}").status_code == 404, "deleted analysis is gone")
    check(call("POST", "/api/auth/logout").status_code == 200, "logout succeeds")
    check(call("GET", "/api/predictions").status_code == 401, "history requires login again after logout")

    print("\n[approximate latency over real HTTP, this machine]")
    for name, values in sorted(timings.items()):
        values.sort()
        print(f"  {name:38s} n={len(values):3d}  median {values[len(values) // 2]:7.1f} ms  max {values[-1]:7.1f} ms")

    print(f"\n{check.passed} passed, {len(check.failed)} failed")
    for label in check.failed:
        print(f"  FAILED: {label}")
    return 1 if check.failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:5000"))

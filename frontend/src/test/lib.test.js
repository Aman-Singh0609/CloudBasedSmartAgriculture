import { describe, expect, it } from "vitest";
import { ApiError, api, request, resetCsrfToken, setUnauthorizedHandler } from "../api/client.js";
import { formatDateTime, percent, safeNextPath } from "../lib/format.js";
import { checkReading, emailProblem, formatNumber, nameProblem, passwordProblem } from "../lib/validation.js";
import { FEATURES, fail, mockApi, ok } from "./helpers.jsx";

const byKey = Object.fromEntries(FEATURES.map((spec) => [spec.key, spec]));

describe("checkReading (browser-side validation)", () => {
  it("accepts ordinary values", () => {
    expect(checkReading(byKey.ph, "6.5")).toEqual({ level: "ok", message: "" });
    expect(checkReading(byKey.nitrogen, " 90 ").level).toBe("ok");
    expect(checkReading(byKey.temperature, "20.8").level).toBe("ok");
  });

  it("reports empty values as required", () => {
    expect(checkReading(byKey.ph, "")).toEqual({ level: "empty", message: "Soil pH is required." });
    expect(checkReading(byKey.ph, "   ").level).toBe("empty");
    expect(checkReading(byKey.ph, undefined).level).toBe("empty");
  });

  it.each(["abc", "12abc", "nan", "NaN", "inf", "Infinity", "1e3", "1e309", "1_000", "0x10", "١٢", "1,5", "1.2.3", "--5", "9999999999999"])(
    "rejects %s as not a number",
    (raw) => {
      const result = checkReading(byKey.nitrogen, raw);
      expect(result.level).toBe("error");
      expect(result.message).toBe("Nitrogen (N) must be a number, for example 90.");
    },
  );

  it("rejects values outside the allowed range with a readable message", () => {
    expect(checkReading(byKey.ph, "15")).toEqual({ level: "error", message: "Soil pH must be between 0 and 14." });
    expect(checkReading(byKey.ph, "-1").level).toBe("error");
    expect(checkReading(byKey.humidity, "101").message).toBe("Humidity must be between 0 and 100 %.");
    expect(checkReading(byKey.temperature, "-11").message).toBe("Temperature must be between -10 and 60 °C.");
  });

  it("accepts the range boundaries", () => {
    for (const spec of FEATURES) {
      expect(checkReading(spec, String(spec.hard_min)).level).not.toBe("error");
      expect(checkReading(spec, String(spec.hard_max)).level).not.toBe("error");
    }
  });

  it("warns, but does not block, outside the model's reference range", () => {
    const result = checkReading(byKey.rainfall, "900");
    expect(result.level).toBe("warning");
    expect(result.message).toContain("Outside the model's reference range (20.2–298.6 mm)");
    expect(checkReading(byKey.nitrogen, "250").level).toBe("warning");
    expect(checkReading(byKey.temperature, "-5").level).toBe("warning");
  });
});

describe("account field checks", () => {
  it("validates email format", () => {
    expect(emailProblem("asha@example.test")).toBeNull();
    expect(emailProblem("")).toMatch(/required/);
    expect(emailProblem("not-an-email")).toMatch(/valid email/);
    expect(emailProblem("a b@example.test")).toMatch(/valid email/);
  });

  it("validates passwords", () => {
    expect(passwordProblem("Tr4ctor-Lane-Meadow")).toBeNull();
    expect(passwordProblem("")).toMatch(/required/);
    expect(passwordProblem("short1")).toMatch(/at least 8/);
    expect(passwordProblem("onlyletters")).toMatch(/one letter and one number/);
    expect(passwordProblem("1234567890")).toMatch(/one letter and one number/);
    expect(passwordProblem("a1".repeat(70))).toMatch(/at most 128/);
  });

  it("validates names", () => {
    expect(nameProblem("Asha Patel")).toBeNull();
    expect(nameProblem("रमेश कुमार")).toBeNull();
    expect(nameProblem("")).toMatch(/required/);
    expect(nameProblem("A")).toMatch(/between 2 and 80/);
  });
});

describe("formatting", () => {
  it("formats numbers without trailing zeros", () => {
    expect(formatNumber(90)).toBe("90");
    expect(formatNumber(6.5)).toBe("6.5");
    expect(formatNumber(27.345)).toBe("27.35");
    expect(formatNumber(Number.NaN)).toBe("");
    expect(formatNumber("7")).toBe("");
  });

  it("formats confidence as a percentage and handles missing values", () => {
    expect(percent(0.82)).toBe("82%");
    expect(percent(1)).toBe("100%");
    expect(percent(null)).toBe("—");
    expect(percent(undefined)).toBe("—");
  });

  it("formats timestamps and tolerates bad input", () => {
    expect(formatDateTime("2026-11-10T12:00:00Z")).toMatch(/2026/);
    expect(formatDateTime(null)).toBe("Unknown");
    expect(formatDateTime("not a date")).toBe("Unknown");
  });
});

describe("safeNextPath (open-redirect protection)", () => {
  it.each(["/advisor", "/history", "/history?page=2", "/history/abc123"])("allows the internal path %s", (path) => {
    expect(safeNextPath(path)).toBe(path);
  });

  it.each([
    "https://evil.example",
    "http://evil.example/advisor",
    "//evil.example",
    "//evil.example/advisor",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "advisor",
    "",
    null,
    undefined,
    42,
    "/advisor\nSet-Cookie: x=1",
    "/\tevil",
    "/" + "a".repeat(600),
  ])("rejects %s and falls back", (target) => {
    expect(safeNextPath(target)).toBe("/advisor");
    expect(safeNextPath(target, "/history")).toBe("/history");
  });
});

describe("API client", () => {
  it("returns the data of a successful response", async () => {
    mockApi({ "GET /api/auth/me": ok({ authenticated: false, user: null }) });
    await expect(api.me()).resolves.toEqual({ authenticated: false, user: null });
  });

  it("sends cookies only to the same origin and never sends a CSRF token on reads", async () => {
    const calls = mockApi();
    await api.config();
    expect(calls).toHaveLength(1);
    expect(calls[0].credentials).toBe("same-origin");
    expect(calls[0].headers["X-CSRFToken"]).toBeUndefined();
  });

  it("fetches a CSRF token once and attaches it to every write", async () => {
    const calls = mockApi({
      "POST /api/predictions": ok({ id: "p1" }, 201),
      "DELETE /api/predictions/p1": ok({ id: "p1" }),
    });
    await api.createPrediction({ ph: "6.5" });
    await api.deletePrediction("p1");
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /api/csrf-token",
      "POST /api/predictions",
      "DELETE /api/predictions/p1",
    ]);
    expect(calls[1].headers["X-CSRFToken"]).toBe("csrf-1");
    expect(calls[1].headers["Content-Type"]).toBe("application/json");
    expect(calls[1].body).toEqual({ ph: "6.5" });
    expect(calls[2].headers["X-CSRFToken"]).toBe("csrf-1");
  });

  it("refreshes a stale CSRF token and retries the request once", async () => {
    let tokens = 0;
    let posts = 0;
    const calls = mockApi({
      "GET /api/csrf-token": () => ok({ csrf_token: `csrf-${(tokens += 1)}` }),
      "POST /api/auth/logout": () =>
        (posts += 1) === 1 ? fail(400, "CSRF_VALIDATION_FAILED", "Token expired") : ok({}),
    });
    await expect(api.logout()).resolves.toEqual({});
    const posted = calls.filter((call) => call.method === "POST");
    expect(posted.map((call) => call.headers["X-CSRFToken"])).toEqual(["csrf-1", "csrf-2"]);
  });

  it("does not retry forever when CSRF keeps failing", async () => {
    const calls = mockApi({ "POST /api/auth/logout": fail(400, "CSRF_VALIDATION_FAILED", "Token expired") });
    await expect(api.logout()).rejects.toMatchObject({ status: 400, code: "CSRF_VALIDATION_FAILED" });
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(2);
  });

  it("turns error envelopes into ApiError with code, details and request id", async () => {
    mockApi({ "POST /api/predictions": fail(422, "INVALID_INPUT", "Please correct the readings.", { ph: "Soil pH must be between 0 and 14." }) });
    const error = await api.createPrediction({}).catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      code: "INVALID_INPUT",
      message: "Please correct the readings.",
      details: { ph: "Soil pH must be between 0 and 14." },
      requestId: "req-test",
    });
  });

  it("reports network failures with a friendly message", async () => {
    mockApi({ "GET /api/config": new TypeError("Failed to fetch") });
    await expect(api.config()).rejects.toMatchObject({ status: 0, code: "NETWORK_ERROR" });
    await expect(api.config()).rejects.toThrow(/could not reach the server/i);
  });

  it("reports timeouts separately", async () => {
    const abort = new Error("aborted");
    abort.name = "AbortError";
    mockApi({ "GET /api/config": abort });
    await expect(api.config()).rejects.toMatchObject({ code: "TIMEOUT" });
  });

  it("copes with a response that is not JSON", async () => {
    mockApi({ "GET /api/config": { status: 502, body: "__invalid__" } });
    await expect(api.config()).rejects.toMatchObject({ status: 502, code: "UNEXPECTED_RESPONSE" });
  });

  it("rejects a 200 response that is not a success envelope", async () => {
    mockApi({ "GET /api/config": { status: 200, body: { hello: "world" } } });
    await expect(api.config()).rejects.toMatchObject({ code: "UNEXPECTED_RESPONSE" });
  });

  it("notifies the app when the session has expired", async () => {
    mockApi({ "GET /api/predictions": fail(401, "AUTHENTICATION_REQUIRED", "Please log in to continue.") });
    let notified = 0;
    setUnauthorizedHandler(() => (notified += 1));
    await expect(api.listPredictions()).rejects.toMatchObject({ status: 401 });
    expect(notified).toBe(1);
    setUnauthorizedHandler(null);
  });

  it("does not treat a wrong password as an expired session", async () => {
    mockApi({ "POST /api/auth/login": fail(401, "INVALID_CREDENTIALS", "Invalid email or password.") });
    let notified = 0;
    setUnauthorizedHandler(() => (notified += 1));
    await expect(api.login({ email: "a@example.test", password: "x" })).rejects.toMatchObject({ code: "INVALID_CREDENTIALS" });
    expect(notified).toBe(0);
    setUnauthorizedHandler(null);
  });

  it("URL-encodes user-supplied path and query values", async () => {
    const calls = mockApi({
      "GET /api/weather": ok({}),
      "GET /api/predictions/..%2F..%2Fauth%2Fme": ok({}),
    });
    await api.weather("New Delhi & more=1");
    await api.getPrediction("../../auth/me");
    expect(calls[0].path).toBe("/api/weather?city=New%20Delhi%20%26%20more%3D1");
    expect(calls[1].path).toBe("/api/predictions/..%2F..%2Fauth%2Fme");
  });

  it("forgets the CSRF token when asked (after login or logout)", async () => {
    const calls = mockApi({ "POST /api/auth/logout": ok({}) });
    await request("POST", "/api/auth/logout");
    resetCsrfToken();
    await request("POST", "/api/auth/logout");
    expect(calls.filter((call) => call.path === "/api/csrf-token")).toHaveLength(2);
  });
});

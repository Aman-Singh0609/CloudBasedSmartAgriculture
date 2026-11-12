# AgricultureCloud API

Base URL: the same origin as the site (development: `http://localhost:5173`, proxied to the
backend on `http://127.0.0.1:5000`).

## Conventions

**Response envelope.** Every endpoint returns JSON in one of two shapes:

```json
{ "success": true, "data": { }, "message": "Human-readable summary" }
```

```json
{
  "success": false,
  "error": { "code": "INVALID_INPUT", "message": "Human-readable message", "details": { "ph": "Soil pH must be between 0 and 14." } },
  "request_id": "0c6d6f0d9a3e4c2f8a4a1b6f3d2e9c11"
}
```

`details` is present only for validation errors and maps field names to messages.
`request_id` matches the `X-Request-ID` response header and the server logs.

**Authentication.** A session cookie (`agricloud_session`) set by `POST /api/auth/login`.
Endpoints marked *Auth: required* return `401 AUTHENTICATION_REQUIRED` without it.

**CSRF.** Every `POST` and `DELETE` must send the header `X-CSRFToken` with a token from
`GET /api/csrf-token`. Logging in or out starts a new session, so fetch a new token afterwards.
A missing or stale token returns `400 CSRF_VALIDATION_FAILED`.

**Request bodies** must be JSON (`Content-Type: application/json`), at most 64 KB.

**Status codes used**

| Code | Meaning |
|---|---|
| 200 | OK |
| 201 | Created |
| 202 | Accepted (password-reset request) |
| 400 | Malformed JSON, or CSRF token missing/invalid |
| 401 | Not logged in, or wrong credentials |
| 404 | Not found (including another user's record) |
| 405 | Method not allowed |
| 409 | Conflict (email already registered) |
| 413 | Body too large |
| 415 | Body is not JSON |
| 422 | Validation failed |
| 429 | Rate limit exceeded (`Retry-After` header is set) |
| 500 | Unexpected server error |
| 502 / 504 | Weather provider error / timeout |
| 503 | Database, model or weather service unavailable |

**Error codes:** `BAD_REQUEST`, `CSRF_VALIDATION_FAILED`, `UNSUPPORTED_MEDIA_TYPE`,
`PAYLOAD_TOO_LARGE`, `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIALS`, `NOT_FOUND`,
`METHOD_NOT_ALLOWED`, `EMAIL_ALREADY_REGISTERED`, `INVALID_INPUT`, `INVALID_RESET_TOKEN`,
`RATE_LIMITED`, `INTERNAL_ERROR`, `DATABASE_UNAVAILABLE`, `MODEL_UNAVAILABLE`,
`WEATHER_NOT_CONFIGURED`, `WEATHER_UNAVAILABLE`, `WEATHER_CITY_NOT_FOUND`,
`WEATHER_PROVIDER_ERROR`, `WEATHER_TIMEOUT`.

A default limit of 300 requests per minute per IP applies to every endpoint except the health
checks; tighter limits are listed per endpoint.

---

## Health

### GET /health

Readiness check. **Auth:** none. **Rate limit:** exempt. **Depends on:** MongoDB, ML model.

`200` when the database and model are both usable, otherwise `503` with the same body shape.

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "checks": {
      "application": "ok",
      "database": "ok",
      "model": "ok",
      "migrations": "up_to_date",
      "weather": "configured"
    },
    "version": "2.0.0",
    "timestamp": "2026-11-11T14:24:18Z"
  },
  "message": "All required services are available"
}
```

- `status`: `ok`, `degraded` (migrations pending) or `unhealthy`.
- `database`, `model`: `ok` or `unavailable`.
- `migrations`: `up_to_date`, `pending` or `unknown`.
- `weather`: `configured` or `not_configured`. Informational only; it never makes the app unhealthy.

No host names, connection strings or error text are returned.

### GET /health/live

Liveness check; never touches dependencies. **Auth:** none. Always `200` with `{"status": "ok"}`.

---

## Bootstrap

### GET /api/config

What the front end needs to render its forms. **Auth:** none.

```json
{
  "success": true,
  "data": {
    "version": "2.0.0",
    "features": [
      {
        "key": "nitrogen", "model_name": "N", "label": "Nitrogen (N)", "short_label": "N",
        "unit": "", "group": "nutrients",
        "hard_min": 0, "hard_max": 300, "model_min": 0, "model_max": 140,
        "step": 1, "example": 90, "help": "Nitrogen content of your soil, from a soil test report. Drives leafy growth."
      }
    ],
    "field_groups": [ { "key": "nutrients", "title": "Soil nutrients", "hint": "…" } ],
    "weather_enabled": true,
    "model_available": true,
    "password_reset_enabled": false,
    "supported_crops": [ { "label": "apple", "name": "Apple" } ]
  },
  "message": "Configuration retrieved"
}
```

`features` lists all seven inputs. `hard_min`/`hard_max` are the limits the backend enforces;
`model_min`/`model_max` are the range the model was trained on.

### GET /api/csrf-token

**Auth:** none. Returns `{"csrf_token": "…"}` bound to the caller's session cookie.

---

## Accounts

### GET /api/auth/me

Current session. **Auth:** none (never returns 401).

```json
{ "success": true, "data": { "authenticated": true, "user": { "id": "…", "name": "Asha Patel", "email": "asha@example.com" } }, "message": "Session retrieved" }
```

`503 DATABASE_UNAVAILABLE` if the session cannot be checked.

### POST /api/auth/signup

**Auth:** none. **Rate limit:** 20 per hour per IP. **Depends on:** MongoDB.

```json
{ "name": "Asha Patel", "email": "asha@example.com", "password": "…", "confirm_password": "…" }
```

Validation:

- `name` – 2 to 80 characters; letters (any script), spaces, hyphens, apostrophes, full stops.
- `email` – valid address, at most 254 characters; trimmed and lower-cased before storage.
- `password` – 8 to 128 characters, at least one letter and one number, not a common password, must not contain the email name or the user's name.
- `confirm_password` – must equal `password`.
- Any other fields are ignored.

`201`:

```json
{ "success": true, "data": { "user": { "id": "…", "name": "Asha Patel", "email": "asha@example.com" } }, "message": "Account created. You can now log in." }
```

Errors: `422 INVALID_INPUT` (with `details`), `409 EMAIL_ALREADY_REGISTERED`, `415`, `400`, `429`.
Signing up does not log the user in.

### POST /api/auth/login

**Auth:** none. **Rate limit:** 20 per minute and 100 per hour per IP; 8 failed attempts per 15 minutes per email address.

```json
{ "email": "asha@example.com", "password": "…" }
```

`200` with `{"user": {…}}` and a new session cookie.

Errors: `401 INVALID_CREDENTIALS` with the fixed message "Invalid email or password." for every
cause (unknown email, wrong password, malformed input); `429 RATE_LIMITED`.

### POST /api/auth/logout

**Auth:** none required. Clears the session. `200`. Works even if the database is unreachable.

### POST /api/auth/forgot-password

**Auth:** none. **Rate limit:** 5 per hour per IP, 3 per hour per email. **Depends on:** mail server.

```json
{ "email": "asha@example.com" }
```

`202` with the same message whether or not the account exists. If it does, an email with a
single-use link (`<APP_BASE_URL>/reset-password/<token>`, valid 30 minutes) is sent.

Errors: `422 INVALID_INPUT` (bad email format), `404 NOT_FOUND` when no mail backend is configured.

### GET /api/auth/reset-password/&lt;token&gt;

Checks a reset link without using it. **Rate limit:** 30 per hour per IP.
`200 {"valid": true}`, `422 INVALID_RESET_TOKEN`, or `404` when reset is not configured.

### POST /api/auth/reset-password/&lt;token&gt;

**Rate limit:** 10 per hour per IP.

```json
{ "password": "…", "confirm_password": "…" }
```

`200` – password changed, the token is consumed, and every existing session for the account
(including the caller's) is ended. Errors: `422 INVALID_INPUT` (weak password; the token stays
usable), `422 INVALID_RESET_TOKEN`, `404`.

---

## Weather

### GET /api/weather?city=&lt;name&gt;

Current temperature and humidity for a place. **Auth:** required. **Rate limit:** 20 per minute
per user. **Depends on:** OpenWeatherMap (called server-side; the API key is never returned).

`city`: 2 to 80 characters; letters, spaces, commas, hyphens, apostrophes, full stops.

`200`:

```json
{
  "success": true,
  "data": {
    "location": "Pune, IN",
    "temperature": 27.3,
    "humidity": 64.0,
    "conditions": "light rain",
    "observed_at": "2026-11-11T08:09:00Z",
    "provider": "openweathermap",
    "source": "weather_api",
    "rainfall": null,
    "observed_rain_last_hour_mm": 0.4,
    "rainfall_note": "Rainfall is not filled in automatically. …"
  },
  "message": "Weather fetched successfully"
}
```

`rainfall` is always `null`: the provider's last-hour reading is not the growing-period
rainfall the model expects. `observed_rain_last_hour_mm` is informational and may be `null`.

The server remembers the fetched values in the session. If a later prediction is submitted with
the same temperature or humidity, the server marks that reading as coming from the weather
service (see `input_sources` below).

Errors: `422 INVALID_INPUT`, `404 WEATHER_CITY_NOT_FOUND`, `503 WEATHER_NOT_CONFIGURED`,
`503 WEATHER_UNAVAILABLE` (network failure, provider rejected the key, provider busy),
`502 WEATHER_PROVIDER_ERROR` (unexpected or malformed response), `504 WEATHER_TIMEOUT`, `429`.

---

## Predictions

Every prediction endpoint requires login and only ever sees the caller's own records.

### POST /api/predictions

Run the model and save the analysis. **Rate limit:** 30 per minute per user.
**Depends on:** ML model, MongoDB.

```json
{
  "nitrogen": 90,
  "phosphorus": 42,
  "potassium": 43,
  "temperature": 20.8,
  "humidity": 82,
  "ph": 6.5,
  "rainfall": 202.9
}
```

Validation (all seven are required; numbers or plain decimal strings):

| Field | Allowed | Model reference range |
|---|---|---|
| `nitrogen` | 0 – 300 | 0 – 140 |
| `phosphorus` | 0 – 300 | 5 – 145 |
| `potassium` | 0 – 400 | 5 – 205 |
| `ph` | 0 – 14 | 3.5 – 9.9 |
| `temperature` (°C) | −10 – 60 | 8.8 – 43.7 |
| `humidity` (%) | 0 – 100 | 14.3 – 100 |
| `rainfall` (mm) | 0 – 3000 | 20.2 – 298.6 |

Rejected: missing values, text, booleans, objects, arrays, `NaN`, infinity, exponent notation,
digit separators, and anything outside the allowed range. Values inside the allowed range but
outside the model reference range are accepted and flagged. Extra fields are ignored; the owner
and the result cannot be supplied by the client.

`201` with a `Location` header (`/api/predictions/<id>`):

```json
{
  "success": true,
  "message": "Prediction generated successfully",
  "data": {
    "id": "6af478af6456779a0dad2668",
    "recommended_crop": "rice",
    "crop_name": "Rice",
    "confidence": 0.99,
    "confidence_band": "high",
    "confidence_label": "Strong model agreement",
    "alternatives": [
      { "crop": "jute", "crop_name": "Jute", "probability": 0.01, "summary": "A fibre crop of hot, humid, high-rainfall regions." }
    ],
    "inputs": { "nitrogen": 90.0, "phosphorus": 42.0, "potassium": 43.0, "ph": 6.5, "temperature": 20.8, "humidity": 82.0, "rainfall": 202.9 },
    "input_sources": { "nitrogen": "manual", "phosphorus": "manual", "potassium": "manual", "ph": "manual", "temperature": "manual", "humidity": "manual", "rainfall": "manual" },
    "weather": null,
    "reliability": "normal",
    "out_of_range_fields": [],
    "created_at": "2026-11-11T14:16:15Z",
    "model": { "model_type": "VotingClassifier", "model_version": "2.0", "n_classes": 22, "sha256_prefix": "f7928fa23ca9", "sklearn_version": "1.6.1" },
    "crop": {
      "label": "rice", "name": "Rice", "also_known_as": "Paddy, Dhan", "scientific_name": "Oryza sativa",
      "category": "Cereal", "perennial": false, "summary": "…", "description": "…",
      "growing_conditions": { "climate": "…", "soil": "…", "water": "…" },
      "season": "…", "cautions": ["…"],
      "typical_ranges": { "nitrogen": [60, 99], "ph": [5.0, 7.9] }
    },
    "soil_analysis": {
      "method": "rule_based",
      "compared_with": "rice",
      "within_typical_range": 7,
      "total_readings": 7,
      "ph_class": "Neutral",
      "ph_description": "Suitable for most crops.",
      "readings": [
        {
          "key": "nitrogen", "label": "Nitrogen (N)", "unit": "", "group": "nutrients",
          "value": 90.0, "value_text": "90",
          "typical_range": [60.0, 99.0], "typical_text": "60–99",
          "status": "within", "status_label": "Within typical range",
          "chart": { "band_start": 42.86, "band_width": 27.86, "marker": 64.29, "scale_min": "0", "scale_max": "140" }
        }
      ],
      "suggestions": [],
      "amount_advice": "Amounts depend on your field. …"
    },
    "input_check": { "unusual_combination": false },
    "agronomic_check": { "source": "FAO EcoCrop", "violations": [] },
    "warnings": [],
    "limitations": ["The recommendation uses only the seven readings you entered and patterns in the model's training data.", "…"]
  }
}
```

Field notes:

- **Machine-learning output:** `recommended_crop`, `confidence`, `alternatives`. `confidence` is the model's class probability (0–1); `alternatives` holds up to three other crops with a non-zero probability, highest first.
- `confidence_band`: `high` (≥ 0.80), `moderate` (≥ 0.50), `low`, or `unknown` (older records).
- **Rule-based output:** everything under `soil_analysis` (`method` is always `rule_based`). `status` is `below`, `within` or `above` the crop's typical range.
- `input_sources`: `manual` or `weather_api` per reading, decided by the server.
- `weather`: `{location, provider, observed_at}` when at least one reading came from the lookup, else `null`.
- `reliability`: `reduced` when `out_of_range_fields` is not empty.
- `input_check.unusual_combination`: `true` when the readings together resemble nothing in the training data; a matching warning is added.
- `agronomic_check.violations`: readings (temperature, pH) outside the recommended crop's FAO EcoCrop absolute limits, each with `field`, `value`, `limit_low`, `limit_high`, `direction`; the band is then shown as low.
- `warnings`: plain-language cautions (unusual combination, outside crop limits, out-of-range inputs, low confidence, multi-year crops, few readings in range).

Errors: `422 INVALID_INPUT` (with `details` per field), `400`, `415`, `413`, `401`, `429`,
`503 MODEL_UNAVAILABLE`, `503 DATABASE_UNAVAILABLE`. Nothing is saved when an error is returned.

### GET /api/predictions?page=&per_page=

History, newest first. `page` ≥ 1 (default 1); `per_page` 1–50 (default 10).

```json
{
  "success": true,
  "message": "Predictions retrieved successfully",
  "data": {
    "items": [
      {
        "id": "6af478af6456779a0dad2668",
        "recommended_crop": "rice",
        "crop_name": "Rice",
        "confidence": 0.99,
        "inputs": { "nitrogen": 90.0, "phosphorus": 42.0, "potassium": 43.0, "ph": 6.5, "temperature": 20.8, "humidity": 82.0, "rainfall": 202.9 },
        "reliability": "normal",
        "created_at": "2026-11-11T14:16:15Z"
      }
    ],
    "pagination": { "page": 1, "per_page": 10, "total": 11, "total_pages": 2, "has_previous": false, "has_next": true }
  }
}
```

A page past the end returns an empty `items` list. Errors: `422 INVALID_INPUT` (bad `page` or
`per_page`), `401`.

### GET /api/predictions/trends

Soil readings across the caller's 30 most recent analyses, for charts.

```json
{
  "success": true,
  "message": "Trends retrieved successfully",
  "data": {
    "trends": [
      {
        "key": "nitrogen", "label": "Nitrogen (N)", "count": 3,
        "latest_text": "90", "first_text": "60", "min_text": "60", "max_text": "90",
        "change": "up",
        "points": "0.00,28.00 50.00,15.00 100.00,2.00",
        "values_text": "60, 75, 90"
      }
    ]
  }
}
```

One entry each for `nitrogen`, `phosphorus`, `potassium` and `ph`. `points` is an SVG polyline
in a 100 × 30 box, oldest on the left. `change` is `up`, `down` or `same`. The list is empty
until there are two analyses.

### GET /api/predictions/&lt;id&gt;

One saved analysis, in the same shape as the `POST` response. The machine-learning output is
returned exactly as stored; the rule-based analysis is recomputed from the stored inputs.

Errors: `404 NOT_FOUND` for an unknown ID, a malformed ID, or a record owned by another user; `401`.

### DELETE /api/predictions/&lt;id&gt;

**Rate limit:** 60 per minute per user. `200` with `{"id": "…"}`.
Errors: `404 NOT_FOUND` (unknown, malformed, or someone else's), `401`, `400 CSRF_VALIDATION_FAILED`.

---

## Example session (curl)

```bash
curl -c jar -s http://127.0.0.1:5000/api/csrf-token
```

```bash
curl -b jar -c jar -s -X POST http://127.0.0.1:5000/api/auth/login -H "Content-Type: application/json" -H "X-CSRFToken: <token>" -d "{\"email\":\"asha@example.com\",\"password\":\"<password>\"}"
```

```bash
curl -b jar -c jar -s http://127.0.0.1:5000/api/csrf-token
```

```bash
curl -b jar -s -X POST http://127.0.0.1:5000/api/predictions -H "Content-Type: application/json" -H "X-CSRFToken: <new token>" -d "{\"nitrogen\":90,\"phosphorus\":42,\"potassium\":43,\"temperature\":20.8,\"humidity\":82,\"ph\":6.5,\"rainfall\":202.9}"
```

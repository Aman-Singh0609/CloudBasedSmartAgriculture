// Crop Advisor: the seven-reading form, weather lookup and submission.

import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../api/client.js";
import { Alert, ErrorSummary, Icon, PageError, PageLoading, Spinner, useDocumentTitle } from "../components/ui.jsx";
import { useConfig } from "../context/ConfigContext.jsx";
import { checkReading, formatNumber } from "../lib/validation.js";

const GROUP_ICONS = { nutrients: "flask", condition: "layers", climate: "sun" };
const WEATHER_FIELDS = ["temperature", "humidity"];

function ReadingField({ spec, value, result, showEmpty, touched, forced, source, onChange, onBlur }) {
  let level = result.level;
  let message = result.message;
  if (level === "empty") {
    level = showEmpty ? "error" : "ok";
    message = showEmpty ? message : "";
  } else if (!touched && !showEmpty && !forced) {
    // Do not judge a value while it is still being typed for the first time.
    level = "ok";
    message = "";
  }
  const messageId = `${spec.key}-message`;

  return (
    <div className={`field field--reading${level === "error" ? " is-invalid" : ""}${level === "warning" ? " is-warning" : ""}`}>
      <div className="field__head">
        <label className="label" htmlFor={spec.key}>
          {spec.label}
          {spec.unit ? <span className="label__unit"> ({spec.unit})</span> : null}
        </label>
        <span className="field__hint" id={`${spec.key}-hint`}>
          {spec.help}
        </span>
      </div>
      <div className={`input-wrap${spec.unit ? " input-wrap--unit" : ""}`}>
        <input
          className="input"
          id={spec.key}
          name={spec.key}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          maxLength={16}
          required
          value={value}
          placeholder={`e.g. ${formatNumber(spec.example)}`}
          aria-invalid={level === "error" ? "true" : undefined}
          aria-describedby={`${spec.key}-hint ${spec.key}-range ${messageId}`}
          onChange={(event) => onChange(spec.key, event.target.value)}
          onBlur={() => onBlur(spec.key)}
        />
        {spec.unit ? (
          <span className="input-unit" aria-hidden="true">
            {spec.unit}
          </span>
        ) : null}
      </div>
      <div className="field__foot">
      <span className="field__range" id={`${spec.key}-range`}>
        Allowed {formatNumber(spec.hard_min)}–{formatNumber(spec.hard_max)}
        {spec.unit ? ` ${spec.unit}` : ""}. Model reference range {formatNumber(spec.model_min)}–
        {formatNumber(spec.model_max)}.
      </span>
      {source ? (
        <span className={`source-tag${source.fromWeather ? "" : " source-tag--manual"}`}>
          <Icon name={source.fromWeather ? "cloud" : "user"} />
          {source.fromWeather ? `From weather service: ${source.location}` : "Edited by you"}
        </span>
      ) : null}
      <span
        className={`field__message${level === "error" || level === "warning" ? ` field__message--${level}` : ""}`}
        id={messageId}
      >
        {message && (level === "error" || level === "warning") ? (
          <>
            <Icon name={level === "error" ? "error" : "alert"} />
            <span>{message}</span>
          </>
        ) : null}
      </span>
      </div>
    </div>
  );
}

function WeatherLookup({ enabled, onFetched }) {
  const [city, setCity] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState({ kind: "", text: "" });
  const inputRef = useRef(null);

  if (!enabled) {
    return (
      <div className="weather-box">
        <p className="small">
          <Icon name="info" /> Weather lookup is not set up on this server. Please enter temperature and humidity
          yourself.
        </p>
      </div>
    );
  }

  const lookUp = async () => {
    if (busy) return;
    const name = city.trim();
    if (name.length < 2) {
      setStatus({ kind: "error", text: "Enter a town or city name first." });
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setStatus({ kind: "", text: `Looking up current weather for ${name}…` });
    try {
      const data = await api.weather(name);
      onFetched(data);
      const rain =
        typeof data.observed_rain_last_hour_mm === "number"
          ? ` Rain in the last hour there: ${formatNumber(data.observed_rain_last_hour_mm)} mm (for information only).`
          : "";
      setStatus({
        kind: "ok",
        text: `Filled in temperature and humidity for ${data.location}. Please enter rainfall yourself.${rain}`,
      });
    } catch (error) {
      setStatus({
        kind: "error",
        text:
          error instanceof ApiError
            ? error.status === 0
              ? `${error.message} You can still enter temperature and humidity yourself.`
              : error.message
            : "The weather lookup failed. Please enter temperature and humidity yourself.",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="weather-box">
      <div className="weather-box__row">
        <div className="field">
          <label className="label" htmlFor="weather-city">
            Look up current weather <span className="label__unit">(optional)</span>
          </label>
          <span className="field__hint" id="weather-city-hint">
            Enter your town or city to fill in temperature and humidity.
          </span>
          <input
            ref={inputRef}
            className="input"
            id="weather-city"
            type="text"
            maxLength={80}
            autoComplete="address-level2"
            placeholder="e.g. Pune"
            value={city}
            aria-describedby="weather-city-hint weather-status"
            onChange={(event) => setCity(event.target.value)}
            onKeyDown={(event) => {
              // Enter here looks up the weather instead of submitting the whole form.
              if (event.key === "Enter") {
                event.preventDefault();
                lookUp();
              }
            }}
          />
        </div>
        <button className="btn btn--secondary" type="button" onClick={lookUp} disabled={busy}>
          {busy ? <Spinner /> : <Icon name="cloud" />} {busy ? "Fetching…" : "Fetch weather"}
        </button>
      </div>
      <p
        className={`weather-box__status${status.kind ? ` is-${status.kind}` : ""}`}
        id="weather-status"
        role="status"
        aria-live="polite"
      >
        {status.text}
      </p>
    </div>
  );
}

function AdvisorForm({ config }) {
  const navigate = useNavigate();
  const specs = config.features;
  const [values, setValues] = useState(() => Object.fromEntries(specs.map((spec) => [spec.key, ""])));
  const [touched, setTouched] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [serverErrors, setServerErrors] = useState({});
  const [general, setGeneral] = useState(null);
  const [busy, setBusy] = useState(false);
  const [weather, setWeather] = useState(null);
  const summaryRef = useRef(null);

  const results = useMemo(
    () => Object.fromEntries(specs.map((spec) => [spec.key, checkReading(spec, values[spec.key])])),
    [specs, values],
  );

  const focusSummary = () => window.setTimeout(() => summaryRef.current?.focus(), 0);

  const onChange = (key, value) => {
    setValues((current) => ({ ...current, [key]: value }));
    setServerErrors((current) => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  };

  const onBlur = (key) => setTouched((current) => ({ ...current, [key]: true }));

  const resultFor = (spec) =>
    serverErrors[spec.key] ? { level: "error", message: serverErrors[spec.key] } : results[spec.key];

  const sourceFor = (key) => {
    if (!weather || !WEATHER_FIELDS.includes(key) || typeof weather[key] !== "number") return null;
    const current = Number.parseFloat(values[key]);
    const fromWeather = Number.isFinite(current) && Math.abs(current - weather[key]) < 0.05;
    return { fromWeather, location: weather.location };
  };

  const onWeather = (data) => {
    setWeather(data);
    setValues((current) => {
      const next = { ...current };
      WEATHER_FIELDS.forEach((key) => {
        if (typeof data[key] === "number") next[key] = formatNumber(data[key]);
      });
      return next;
    });
  };

  const fillExample = () => {
    setValues(Object.fromEntries(specs.map((spec) => [spec.key, formatNumber(spec.example)])));
    setServerErrors({});
    setGeneral(null);
  };

  const errorList = submitted
    ? specs
        .map((spec) => ({ spec, result: resultFor(spec) }))
        .filter(({ result }) => result.level === "empty" || result.level === "error")
        .map(({ spec, result }) => ({ field: spec.key, message: result.message }))
    : [];

  const onSubmit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setSubmitted(true);
    setGeneral(null);
    setServerErrors({});

    const invalid = specs.some((spec) => ["empty", "error"].includes(results[spec.key].level));
    if (invalid) {
      focusSummary();
      return;
    }

    setBusy(true);
    try {
      const payload = Object.fromEntries(specs.map((spec) => [spec.key, values[spec.key].trim()]));
      const created = await api.createPrediction(payload);
      navigate(`/history/${created.id}`);
    } catch (error) {
      setBusy(false);
      if (error instanceof ApiError && error.status === 422 && error.details) {
        setServerErrors(error.details);
        focusSummary();
        return;
      }
      if (error instanceof ApiError && error.status === 401) {
        return; // the auth context is already redirecting to the login page
      }
      const text = error instanceof ApiError ? error.message : "Something went wrong while analyzing your readings.";
      setGeneral(error instanceof ApiError && error.status === 0 ? `${text} Your values are still here.` : text);
    }
  };

  return (
    <form className="stack stack--lg" onSubmit={onSubmit} noValidate aria-busy={busy}>
      {general ? (
        <Alert kind="error" focusOnMount>
          {general}
        </Alert>
      ) : null}
      <ErrorSummary errors={errorList} summaryRef={summaryRef} />

      {config.field_groups.map((group) => {
        const fields = specs.filter((spec) => spec.group === group.key);
        return (
          <fieldset className="card field-group" key={group.key}>
            <legend className="field-group__legend">
              <Icon name={GROUP_ICONS[group.key] || "info"} /> {group.title}
            </legend>
            <p className="card__hint field-group__hint">{group.hint}</p>

            {group.key === "climate" ? <WeatherLookup enabled={config.weather_enabled} onFetched={onWeather} /> : null}

            <div className={`field-grid ${fields.length >= 3 ? "field-grid--3" : "field-grid--2"}`}>
              {fields.map((spec) => (
                <ReadingField
                  key={spec.key}
                  spec={spec}
                  value={values[spec.key]}
                  result={resultFor(spec)}
                  showEmpty={submitted}
                  source={sourceFor(spec.key)}
                  onChange={onChange}
                  onBlur={onBlur}
                  touched={Boolean(touched[spec.key])}
                  forced={Boolean(serverErrors[spec.key])}
                />
              ))}
            </div>

            {group.key === "climate" ? (
              <p className="small muted field-group__note">
                <Icon name="info" /> Rainfall is never filled in from the weather lookup: the weather service reports
                only the last hour, while the model expects typical rainfall for the growing period.
              </p>
            ) : null}
          </fieldset>
        );
      })}

      {busy ? (
        <div className="loading-panel" role="status" aria-live="polite">
          <Spinner />
          <span>Analyzing soil and climate data…</span>
        </div>
      ) : null}

      <div className="form-actions">
        <button className="btn btn--lg" type="submit" disabled={busy || !config.model_available}>
          Get recommendation <Icon name="arrow-right" />
        </button>
        <button className="btn btn--ghost" type="button" onClick={fillExample} disabled={busy}>
          Fill with example values
        </button>
      </div>
    </form>
  );
}

export default function Advisor() {
  useDocumentTitle("Crop Advisor");
  const { status, config, error, reload } = useConfig();

  if (status === "loading") return <PageLoading label="Loading the advisor…" />;
  if (status === "error") {
    return (
      <PageError
        title="The advisor could not be loaded"
        message={error?.message || "We could not reach the server. Please try again."}
        onRetry={reload}
      />
    );
  }

  return (
    <div className="container page">
      <header className="page__header">
        <h1>Crop Advisor</h1>
        <p>
          Enter seven readings about your soil and climate. You will get a recommended crop, alternatives, and a
          comparison of each reading with what that crop typically needs.
        </p>
      </header>

      {!config.model_available ? (
        <Alert kind="error" title="Recommendations are temporarily unavailable">
          <p>The recommendation model could not be loaded on this server. Please try again later.</p>
        </Alert>
      ) : null}

      <div className="advisor">
        <AdvisorForm config={config} />

        <aside className="advisor__aside" aria-label="Help">
          <div className="card card--tint">
            <h2 className="card__title">
              <Icon name="book" /> What you need
            </h2>
            <ul className="tip-list">
              <li>
                <strong>A soil test report</strong> for nitrogen, phosphorus, potassium and pH.
              </li>
              <li>
                <strong>Typical temperature, humidity and rainfall</strong> for the season you plan to grow in.
              </li>
              <li>
                Values outside the <em>model reference range</em> are accepted, but the result is marked as less
                reliable.
              </li>
            </ul>
          </div>
          <div className="card card--flat">
            <h2 className="card__title">
              <Icon name="info" /> About the nutrient scale
            </h2>
            <p className="small muted">
              The model's reference data records N, P and K as relative soil content without a documented unit. Enter
              the figures from your report as they are; if your report uses a very different scale, values will fall
              outside the reference range and the result will say so.
            </p>
          </div>
          <div className="card card--flat">
            <h2 className="card__title">
              <Icon name="shield" /> Advisory only
            </h2>
            <p className="small muted">
              The recommendation is based only on these seven readings. It cannot consider markets, pests,
              irrigation, soil texture or future weather. Your analysis is saved to your account so you can return to
              it.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}

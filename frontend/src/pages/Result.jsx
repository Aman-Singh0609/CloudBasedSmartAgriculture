// Result dashboard for one saved analysis.
//
// The page keeps two kinds of information visibly apart: what the
// machine-learning model said (crop, confidence, alternatives) and the
// rule-based comparison of each reading with the crop's typical range.

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client.js";
import {
  Alert,
  ConfirmDialog,
  Icon,
  PageError,
  PageLoading,
  ReadingRow,
  ShareBar,
  useDocumentTitle,
} from "../components/ui.jsx";
import { formatDateTime, percent } from "../lib/format.js";
import { formatNumber } from "../lib/validation.js";
import NotFound from "./NotFound.jsx";

const BAND_ICONS = { high: "check-circle", moderate: "info", low: "alert", unknown: "info" };
const STATUS_ICONS = { within: "check", below: "arrow-down", above: "arrow-up" };

function MethodTag({ kind, children }) {
  return <span className={`method-tag method-tag--${kind}`}>{children}</span>;
}

function LocalTime({ iso }) {
  return (
    <time dateTime={iso || undefined} title={iso || undefined}>
      {formatDateTime(iso)}
    </time>
  );
}

function CalloutList({ kind, icon, items }) {
  return (
    <ul className={`callout-list callout-list--${kind}`}>
      {items.map((item) => (
        <li key={item}>
          <Icon name={icon} />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function ResultView({ data, onDelete }) {
  const { crop, soil_analysis: analysis, alternatives, warnings, weather } = data;
  const cropLower = crop.name.toLowerCase();
  const readings = analysis ? analysis.readings : [];
  const soilReadings = readings.filter((reading) => reading.group !== "climate");
  const climateReadings = readings.filter((reading) => reading.group === "climate");

  return (
    <div className="container page">
      <p className="small no-print">
        <Link to="/history">
          <Icon name="arrow-left" /> All analyses
        </Link>
      </p>

      {data.reliability === "reduced" ? (
        <div className="stack">
          <Alert kind="warning" title="Reduced reliability">
            <p>
              One or more of your readings are outside the range the model was trained on. Read the cautions below
              before relying on this result.
            </p>
          </Alert>
        </div>
      ) : null}

      <section className="result-hero result-section" aria-labelledby="result-title">
        <div>
          <MethodTag kind="ml">
            <Icon name="chart" /> Machine-learning recommendation
          </MethodTag>
          <p className="result-hero__label">Recommended crop</p>
          <h1 className="result-hero__crop" id="result-title">
            {crop.name}
          </h1>
          <p className="result-hero__meta">
            {crop.category} &middot; <em>{crop.scientific_name}</em>
            {crop.also_known_as ? ` · also known as ${crop.also_known_as}` : ""}
          </p>
          <p className="result-hero__summary">{crop.summary}</p>
          <p className="result-hero__meta">
            <Icon name="clock" /> Analysed <LocalTime iso={data.created_at} />
          </p>
        </div>

        <div className="confidence">
          <div className="confidence__label">Model confidence</div>
          <div className="confidence__value">{percent(data.confidence)}</div>
          {typeof data.confidence === "number" ? <ShareBar value={data.confidence} /> : null}
          <div className="confidence__band">
            <Icon name={BAND_ICONS[data.confidence_band] || "info"} /> {data.confidence_label}
          </div>
          <p className="confidence__note">
            How strongly the model's decision trees agree on this crop. It is not the chance of a successful harvest.
          </p>
        </div>
      </section>

      {warnings.length > 0 ? (
        <section className="card result-section" aria-labelledby="cautions-title">
          <div className="card__head">
            <h2 id="cautions-title">
              <Icon name="alert" /> Cautions for this result
            </h2>
          </div>
          <CalloutList kind="warn" icon="alert" items={warnings} />
        </section>
      ) : null}

      <div className="result-grid result-grid--2">
        <section className="card" aria-labelledby="why-title">
          <div className="card__head">
            <h2 id="why-title">Why this recommendation</h2>
          </div>
          <p>
            The model compared your seven readings with the patterns it learned from reference crop data and found{" "}
            <strong>{cropLower}</strong> to be the closest match.
          </p>
          {analysis ? (
            <>
              <p>
                <strong>
                  {analysis.within_typical_range} of {analysis.total_readings}
                </strong>{" "}
                of your readings fall within the typical range for {cropLower}:
              </p>
              <ul className="checklist checklist--compact">
                {readings.map((reading) => (
                  <li key={reading.key}>
                    <Icon name={STATUS_ICONS[reading.status] || "info"} />
                    <span>
                      {reading.label} <strong>{reading.value_text}</strong> – {reading.status_label.toLowerCase()} (
                      {reading.typical_text})
                    </span>
                  </li>
                ))}
              </ul>
              <p className="small muted">
                The reading comparison is a simple rule applied after the model has run. It helps explain the result
                but is not part of the model.
              </p>
            </>
          ) : null}
        </section>

        <section className="card" aria-labelledby="alternatives-title">
          <div className="card__head">
            <h2 id="alternatives-title">Alternative crops</h2>
            <MethodTag kind="ml">Machine learning</MethodTag>
          </div>
          {alternatives.length > 0 ? (
            <>
              <p className="small muted">
                Other crops the model considered, with the share of model confidence each received.
              </p>
              <ol className="alt-list">
                {alternatives.map((alternative) => (
                  <li className="alt" key={alternative.crop}>
                    <span className="alt__name">{alternative.crop_name}</span>
                    <span className="alt__pct">{percent(alternative.probability)}</span>
                    <ShareBar value={alternative.probability} className="bar--alt" />
                    <span className="alt__note">{alternative.summary}</span>
                  </li>
                ))}
              </ol>
            </>
          ) : (
            <p className="muted">
              {data.confidence === null
                ? "Alternatives were not recorded for this analysis."
                : "The model did not give any other crop a score for these readings."}
            </p>
          )}
        </section>
      </div>

      {analysis ? (
        <>
          <div className="result-grid result-grid--2">
            <section className="card" aria-labelledby="soil-title">
              <div className="card__head">
                <h2 id="soil-title">Soil analysis</h2>
                <MethodTag kind="rule">Rule-based, not ML</MethodTag>
              </div>
              <p className="small muted">
                Each reading compared with the typical range for {cropLower}. All soil values were entered by you.
              </p>
              <ul className="reading-list">
                {soilReadings.map((reading) => (
                  <ReadingRow key={reading.key} reading={reading} />
                ))}
              </ul>
              <p className="small">
                <strong>
                  Soil pH {formatNumber(data.inputs.ph)} is classed as {analysis.ph_class.toLowerCase()}.
                </strong>{" "}
                {analysis.ph_description}
              </p>
              <ul className="legend" aria-hidden="true">
                <li>
                  <span className="legend__swatch" />
                  Typical range for this crop
                </li>
                <li>
                  <span className="legend__swatch legend__swatch--marker" />
                  Your reading
                </li>
              </ul>
            </section>

            <section className="card" aria-labelledby="climate-title">
              <div className="card__head">
                <h2 id="climate-title">Climate summary</h2>
                <MethodTag kind="rule">Rule-based, not ML</MethodTag>
              </div>
              <p className="small muted">
                Climate readings compared with the typical range for {cropLower}, with where each value came from.
              </p>
              <ul className="reading-list">
                {climateReadings.map((reading) => (
                  <ReadingRow
                    key={reading.key}
                    reading={reading}
                    sourceLabel={
                      data.input_sources[reading.key] === "weather_api" ? "From weather service" : "Entered by you"
                    }
                  />
                ))}
              </ul>
              {weather ? (
                <p className="small">
                  <Icon name="cloud" /> Weather values are current conditions for <strong>{weather.location}</strong>,
                  observed <LocalTime iso={weather.observed_at} /> ({weather.provider}). They are a snapshot, not a
                  seasonal average.
                </p>
              ) : (
                <p className="small muted">All climate values were entered by you.</p>
              )}
            </section>
          </div>

          {analysis.suggestions.length > 0 ? (
            <section className="card result-section" aria-labelledby="improve-title">
              <div className="card__head">
                <h2 id="improve-title">What you could improve</h2>
                <MethodTag kind="rule">Rule-based, not ML</MethodTag>
              </div>
              <CalloutList kind="tip" icon="sprout" items={analysis.suggestions} />
              <p className="small muted">{analysis.amount_advice}</p>
            </section>
          ) : null}
        </>
      ) : null}

      <section className="card result-section" aria-labelledby="about-title">
        <div className="card__head">
          <h2 id="about-title">About {cropLower}</h2>
        </div>
        <p>{crop.description}</p>
        <dl className="dl dl--grid">
          <div>
            <dt>
              <Icon name="sun" /> Climate
            </dt>
            <dd>{crop.growing_conditions.climate}</dd>
          </div>
          <div>
            <dt>
              <Icon name="layers" /> Soil
            </dt>
            <dd>{crop.growing_conditions.soil}</dd>
          </div>
          <div>
            <dt>
              <Icon name="droplet" /> Water
            </dt>
            <dd>{crop.growing_conditions.water}</dd>
          </div>
          <div>
            <dt>
              <Icon name="clock" /> Season
            </dt>
            <dd>{crop.season}</dd>
          </div>
        </dl>
        {crop.cautions.length > 0 ? (
          <>
            <h3 className="result-subhead">Things to watch</h3>
            <CalloutList kind="warn" icon="alert" items={crop.cautions} />
          </>
        ) : null}
        <p className="small muted">
          General reference information. Season notes reflect common practice in India; local conditions and varieties
          differ.
        </p>
      </section>

      <section className="card card--flat result-section" aria-labelledby="limits-title">
        <div className="card__head">
          <h2 id="limits-title">
            <Icon name="info" /> Limitations
          </h2>
        </div>
        <CalloutList kind="limit" icon="info" items={data.limitations} />
      </section>

      <div className="form-actions no-print">
        <Link className="btn" to="/advisor">
          <Icon name="refresh" /> Analyze again
        </Link>
        <Link className="btn btn--secondary" to="/history">
          <Icon name="clock" /> View history
        </Link>
        <button className="btn btn--ghost" type="button" onClick={() => window.print()}>
          <Icon name="printer" /> Print
        </button>
        <button className="btn btn--danger-ghost" type="button" onClick={onDelete}>
          <Icon name="trash" /> Delete
        </button>
      </div>
    </div>
  );
}

export default function Result() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  useDocumentTitle(state.data ? `${state.data.crop.name} recommendation` : "Analysis");

  const load = useCallback(async () => {
    setState({ status: "loading", data: null, error: null });
    try {
      setState({ status: "ready", data: await api.getPrediction(id), error: null });
    } catch (error) {
      setState({ status: error instanceof ApiError && error.status === 404 ? "missing" : "error", data: null, error });
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (state.status === "loading") return <PageLoading label="Loading your analysis…" />;
  if (state.status === "missing") {
    return <NotFound title="Analysis not found" message="This analysis does not exist or has been deleted." />;
  }
  if (state.status === "error") {
    return (
      <PageError
        title="This analysis could not be loaded"
        message={state.error?.message || "Please try again."}
        onRetry={load}
      >
        <Link className="btn btn--secondary" to="/history">
          Your analyses
        </Link>
      </PageError>
    );
  }

  const confirmDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deletePrediction(id);
      navigate("/history", { state: { notice: { kind: "success", text: "The analysis has been deleted." } } });
    } catch (error) {
      setDeleting(false);
      setConfirming(false);
      setDeleteError(error instanceof ApiError ? error.message : "The analysis could not be deleted.");
    }
  };

  return (
    <>
      {deleteError ? (
        <div className="container flash-region">
          <Alert kind="error" onDismiss={() => setDeleteError(null)}>
            {deleteError}
          </Alert>
        </div>
      ) : null}
      <ResultView data={state.data} onDelete={() => setConfirming(true)} />
      <ConfirmDialog
        open={confirming}
        title="Delete this analysis?"
        confirmLabel="Delete"
        busy={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setConfirming(false)}
      >
        <p>
          You are about to delete <strong>the {state.data.crop.name.toLowerCase()} analysis</strong>. This cannot be
          undone.
        </p>
      </ConfirmDialog>
    </>
  );
}

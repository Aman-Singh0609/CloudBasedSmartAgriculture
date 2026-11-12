// History: every saved analysis for the signed-in user, with trend charts.

import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api/client.js";
import {
  Alert,
  ConfirmDialog,
  Icon,
  PageError,
  PageLoading,
  StatusChip,
  useDocumentTitle,
} from "../components/ui.jsx";
import { useConfig } from "../context/ConfigContext.jsx";
import { formatDateTime, percent } from "../lib/format.js";
import { formatNumber } from "../lib/validation.js";

// Used when the configuration (and its column definitions) has not arrived yet.
const FALLBACK_COLUMNS = [
  { key: "nitrogen", label: "Nitrogen (N)", short_label: "N", unit: "" },
  { key: "phosphorus", label: "Phosphorus (P)", short_label: "P", unit: "" },
  { key: "potassium", label: "Potassium (K)", short_label: "K", unit: "" },
  { key: "ph", label: "Soil pH", short_label: "pH", unit: "" },
  { key: "temperature", label: "Temperature", short_label: "Temp", unit: "°C" },
  { key: "humidity", label: "Humidity", short_label: "Humidity", unit: "%" },
  { key: "rainfall", label: "Rainfall", short_label: "Rain", unit: "mm" },
];

function parsePage(raw) {
  return /^[1-9]\d{0,5}$/.test(raw || "") ? Number(raw) : 1;
}

function Trends({ trends }) {
  if (!trends || trends.length === 0) return null;
  return (
    <section className="stack" aria-labelledby="trend-title">
      <h2 id="trend-title">How your soil readings have changed</h2>
      <p className="muted small">Based on your {trends[0].count} most recent analyses, oldest on the left.</p>
      <div className="trend-grid">
        {trends.map((card) => (
          <div className="trend" key={card.key}>
            <div className="trend__name">{card.label}</div>
            <div className="trend__value">{card.latest_text}</div>
            <div className="trend__change">
              {card.change === "up" ? (
                <>
                  <Icon name="arrow-up" /> up from {card.first_text}
                </>
              ) : card.change === "down" ? (
                <>
                  <Icon name="arrow-down" /> down from {card.first_text}
                </>
              ) : (
                <>
                  <Icon name="equal" /> unchanged
                </>
              )}
            </div>
            <svg
              className="trend__chart"
              viewBox="0 0 100 30"
              preserveAspectRatio="none"
              role="img"
              aria-label={`${card.label} over your last ${card.count} analyses, oldest to newest: ${card.values_text}.`}
            >
              <polyline className="trend__line" points={card.points} />
            </svg>
            <div className="trend__range" aria-hidden="true">
              <span>low {card.min_text}</span>
              <span>high {card.max_text}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Pagination({ pagination }) {
  if (pagination.total_pages <= 1) return null;
  const link = (page) => (page === 1 ? "/history" : `/history?page=${page}`);
  return (
    <nav className="pagination" aria-label="History pages">
      {pagination.has_previous ? (
        <Link className="btn btn--secondary" to={link(pagination.page - 1)} rel="prev">
          <Icon name="arrow-left" /> Newer
        </Link>
      ) : (
        <span className="btn btn--secondary" aria-disabled="true">
          <Icon name="arrow-left" /> Newer
        </span>
      )}
      <span className="pagination__status">
        Page {pagination.page} of {pagination.total_pages}
      </span>
      {pagination.has_next ? (
        <Link className="btn btn--secondary" to={link(pagination.page + 1)} rel="next">
          Older <Icon name="arrow-right" />
        </Link>
      ) : (
        <span className="btn btn--secondary" aria-disabled="true">
          Older <Icon name="arrow-right" />
        </span>
      )}
    </nav>
  );
}

export default function History() {
  useDocumentTitle("History");
  const { config } = useConfig();
  const columns = config?.features || FALLBACK_COLUMNS;
  const [searchParams, setSearchParams] = useSearchParams();
  const page = parsePage(searchParams.get("page"));

  const [state, setState] = useState({ status: "loading", items: [], pagination: null, trends: [], error: null });
  const [target, setTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(
    async (showSpinner = true) => {
      if (showSpinner) setState((current) => ({ ...current, status: "loading", error: null }));
      try {
        const [list, trendData] = await Promise.all([api.listPredictions(page), api.trends()]);
        // Deleting the last row of the last page: step back to the new last page.
        if (list.items.length === 0 && list.pagination.total > 0 && page > list.pagination.total_pages) {
          setSearchParams(list.pagination.total_pages > 1 ? { page: String(list.pagination.total_pages) } : {}, {
            replace: true,
          });
          return;
        }
        setState({ status: "ready", items: list.items, pagination: list.pagination, trends: trendData.trends, error: null });
      } catch (error) {
        setState({ status: "error", items: [], pagination: null, trends: [], error });
      }
    },
    [page, setSearchParams],
  );

  useEffect(() => {
    load();
  }, [load]);

  const confirmDelete = async () => {
    if (!target) return;
    setDeleting(true);
    try {
      await api.deletePrediction(target.id);
      setNotice({ kind: "success", text: `The ${target.crop_name.toLowerCase()} analysis has been deleted.` });
      setTarget(null);
      await load(false);
    } catch (error) {
      setNotice({
        kind: "error",
        text: error instanceof ApiError ? error.message : "The analysis could not be deleted.",
      });
      setTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  if (state.status === "loading") return <PageLoading label="Loading your analyses…" />;
  if (state.status === "error") {
    return (
      <PageError
        title="Your analyses could not be loaded"
        message={state.error?.message || "Please try again."}
        onRetry={() => load()}
      />
    );
  }

  const { items, pagination, trends } = state;

  return (
    <div className="container page">
      <header className="page__header page__header--split">
        <div>
          <h1>Your analyses</h1>
          <p>Every recommendation you have generated, newest first.</p>
        </div>
        <Link className="btn" to="/advisor">
          <Icon name="plus" /> New analysis
        </Link>
      </header>

      {notice ? (
        <div className="stack">
          <Alert kind={notice.kind} onDismiss={() => setNotice(null)}>
            {notice.text}
          </Alert>
        </div>
      ) : null}

      {items.length === 0 ? (
        <div className="empty-state">
          <span className="empty-state__icon">
            <Icon name="sprout" className="icon--lg" />
          </span>
          <h2>No analyses yet</h2>
          <p>
            Run your first analysis to get a crop recommendation. It will be saved here so you can come back to it.
          </p>
          <Link className="btn" to="/advisor">
            Analyze my soil <Icon name="arrow-right" />
          </Link>
        </div>
      ) : (
        <>
          <Trends trends={trends} />

          <section className="stack history-list" aria-labelledby="list-title">
            <h2 id="list-title">
              All analyses <span className="muted small">({pagination.total})</span>
            </h2>
            <div className="table-wrap table-wrap--stack">
              <table className="table table--stack">
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Recommended crop</th>
                    <th scope="col" className="num">
                      Confidence
                    </th>
                    {columns.map((spec) => (
                      <th scope="col" className="num" key={spec.key}>
                        {spec.short_label}
                        {spec.unit ? <span className="label__unit"> ({spec.unit})</span> : null}
                      </th>
                    ))}
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td data-label="Date" className="nowrap">
                        <time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time>
                      </td>
                      <th scope="row" data-label="Recommended crop">
                        <span className="cluster">
                          <Link className="crop-cell" to={`/history/${item.id}`}>
                            {item.crop_name}
                          </Link>
                          {item.reliability === "reduced" ? (
                            <StatusChip status="caution" label="Reduced reliability" />
                          ) : null}
                        </span>
                      </th>
                      <td className="num" data-label="Confidence">
                        {percent(item.confidence)}
                      </td>
                      {columns.map((spec) => (
                        <td className="num" key={spec.key} data-label={spec.unit ? `${spec.label} (${spec.unit})` : spec.label}>
                          {typeof item.inputs[spec.key] === "number" ? formatNumber(item.inputs[spec.key]) : "—"}
                        </td>
                      ))}
                      <td className="cell-actions">
                        <span className="row-actions">
                          <Link className="btn btn--sm btn--secondary" to={`/history/${item.id}`}>
                            Open<span className="visually-hidden"> {item.crop_name} analysis</span>
                          </Link>
                          <button className="btn btn--sm btn--danger-ghost" type="button" onClick={() => setTarget(item)}>
                            <Icon name="trash" />
                            <span className="visually-hidden">Delete {item.crop_name} analysis</span>
                          </button>
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination pagination={pagination} />
          </section>
        </>
      )}

      <ConfirmDialog
        open={Boolean(target)}
        title="Delete this analysis?"
        confirmLabel="Delete"
        busy={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setTarget(null)}
      >
        <p>
          You are about to delete <strong>the {target ? target.crop_name.toLowerCase() : ""} analysis</strong>
          {target ? ` from ${formatDateTime(target.created_at)}` : ""}. This cannot be undone.
        </p>
      </ConfirmDialog>
    </div>
  );
}

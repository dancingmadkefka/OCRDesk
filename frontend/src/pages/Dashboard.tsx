import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Summary } from "../api";
import OcrPanel from "../components/OcrPanel";
import { displayStem } from "../stemUtils";

type StatusFilter = "all" | "todo" | "done";

export default function Dashboard() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  const load = useCallback(() => {
    api
      .getSummary()
      .then(setSummary)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!summary) return [];
    return summary.images.filter((img) => {
      if (status === "todo" && img.has_gt) return false;
      if (status === "done" && !img.has_gt) return false;
      const q = filter.toLowerCase();
      return img.stem.toLowerCase().includes(q) || displayStem(img.stem).title.toLowerCase().includes(q);
    });
  }, [summary, filter, status]);

  function toggleOne(stem: string, e: React.MouseEvent) {
    e.stopPropagation();
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(stem)) next.delete(stem);
      else next.add(stem);
      return next;
    });
  }

  if (error) {
    return (
      <div className="page">
        <div className="error-banner">
          <strong>Couldn't load documents</strong>
          <span>{error}</span>
          <p className="hint">
            Check the image folder path in <Link to="/settings">Settings</Link>.
          </p>
        </div>
      </div>
    );
  }

  if (!summary) return <div className="loading">Loading corpus…</div>;

  const models = Object.keys(summary.model_coverage).sort();
  const pct = summary.total ? Math.round((summary.gt_count / summary.total) * 100) : 0;
  const ringStyle = { "--ring-pct": `${pct * 3.6}deg` } as React.CSSProperties;

  return (
    <div className="page dashboard-v2">
      <header className="dash-hero">
        <div className="dash-hero-copy">
          <p className="dash-eyebrow">Document corpus</p>
          <h1>Audit OCR against real documents</h1>
          <p className="dash-lead">
            {summary.total} scanned documents in your corpus. Open any card to compare model output, ground truth, and
            the original image side by side.
          </p>
        </div>

        <div className="dash-hero-stats">
          <div className="progress-ring" style={ringStyle} aria-label={`${pct}% verified`}>
            <div className="progress-ring-inner">
              <span className="progress-ring-value">{pct}%</span>
              <span className="progress-ring-label">verified</span>
            </div>
          </div>
          <div className="dash-stat-pills">
            <div className="dash-stat-pill ok">
              <span className="dash-stat-num">{summary.gt_count}</span>
              <span className="dash-stat-cap">ground truths</span>
            </div>
            <div className="dash-stat-pill warn">
              <span className="dash-stat-num">{summary.remaining_gt}</span>
              <span className="dash-stat-cap">to verify</span>
            </div>
          </div>
        </div>
      </header>

      {models.length > 0 && (
        <section className="dash-coverage">
          <h2 className="dash-section-title">Model coverage</h2>
          <div className="coverage-bars">
            {models.map((m) => (
              <div key={m} className="coverage-row">
                <span className="mono coverage-name" title={m}>
                  {m}
                </span>
                <div className="coverage-track">
                  <div
                    className="coverage-fill"
                    style={{ width: `${(summary.model_coverage[m] / summary.total) * 100}%` }}
                  />
                </div>
                <span className="coverage-count">
                  {summary.model_coverage[m]}/{summary.total}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="dash-catalog">
        <div className="dash-toolbar">
          <input
            type="search"
            placeholder="Search documents…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="search-input"
          />
          <div className="filter-chips" role="tablist" aria-label="Filter by status">
            <button
              type="button"
              className={`filter-chip${status === "all" ? " active" : ""}`}
              onClick={() => setStatus("all")}
            >
              All <span className="chip-count">{summary.total}</span>
            </button>
            <button
              type="button"
              className={`filter-chip${status === "todo" ? " active" : ""}`}
              onClick={() => setStatus("todo")}
            >
              Needs review <span className="chip-count">{summary.remaining_gt}</span>
            </button>
            <button
              type="button"
              className={`filter-chip${status === "done" ? " active" : ""}`}
              onClick={() => setStatus("done")}
            >
              Verified <span className="chip-count">{summary.gt_count}</span>
            </button>
          </div>
          <span className="result-count">{filtered.length} documents</span>
        </div>

        {filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">{summary.total === 0 ? "📁" : "🔍"}</div>
            <h3>{summary.total === 0 ? "No documents found" : "No matches"}</h3>
            <p>
              {summary.total === 0 ? (
                <>
                  Check the image folder in <Link to="/settings">Settings</Link>.
                </>
              ) : (
                "Try a different search or filter."
              )}
            </p>
          </div>
        ) : (
          <div className="doc-grid">
            {filtered.map((img) => {
              const { title, hash } = displayStem(img.stem);
              const isSelected = selected.has(img.stem);
              return (
                <article
                  key={img.stem}
                  className={`doc-card${isSelected ? " selected" : ""}${img.has_gt ? " verified" : " pending"}`}
                  onClick={() => navigate(`/workspace/${encodeURIComponent(img.stem)}`)}
                >
                  <div className="doc-card-media">
                    <img src={api.imageUrl(img.stem)} alt="" loading="lazy" />
                    <label className="doc-card-check" onClick={(e) => toggleOne(img.stem, e)}>
                      <input type="checkbox" checked={isSelected} readOnly tabIndex={-1} />
                    </label>
                    <span className={`doc-card-badge${img.has_gt ? " ok" : " pending"}`}>
                      {img.has_gt ? "Verified" : "Needs review"}
                    </span>
                  </div>
                  <div className="doc-card-body">
                    <h3 className="doc-card-title">{title}</h3>
                    {hash && <span className="doc-card-id mono">{hash}</span>}
                    <div className="doc-card-tags">
                      {img.models.length ? (
                        img.models.map((m) => (
                          <span key={m} className="tag">
                            {m}
                          </span>
                        ))
                      ) : (
                        <span className="dim">No OCR runs yet</span>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {selected.size > 0 && (
        <div className="batch-dock" role="region" aria-label="Batch actions">
          <div className="batch-dock-inner">
            <span className="batch-dock-count">{selected.size} selected</span>
            <OcrPanel
              stems={[...selected]}
              existingModelsByStem={Object.fromEntries(
                summary.images.filter((img) => selected.has(img.stem)).map((img) => [img.stem, img.models])
              )}
              onComplete={load}
              compact
            />
            <button type="button" className="btn ghost" onClick={() => setSelected(new Set())}>
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

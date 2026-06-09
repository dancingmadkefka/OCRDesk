import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Summary } from "../api";
import OcrPanel from "../components/OcrPanel";

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");

  const load = useCallback(() => {
    api
      .getSummary()
      .then(setSummary)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = summary?.images.filter((img) =>
    img.stem.toLowerCase().includes(filter.toLowerCase())
  );

  function toggleAll(checked: boolean) {
    if (!filtered) return;
    if (checked) setSelected(new Set(filtered.map((i) => i.stem)));
    else setSelected(new Set());
  }

  function toggleOne(stem: string) {
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
          {error}
          <p className="hint">
            Check the image folder path in <Link to="/settings">Settings</Link>.
          </p>
        </div>
      </div>
    );
  }

  if (!summary) return <div className="loading">Loading…</div>;

  const models = Object.keys(summary.model_coverage).sort();

  return (
    <div className="page dashboard">
      <div className="page-header">
        <h1>Document Corpus</h1>
        <p className="subtitle">{summary.total} images · benchmark folder</p>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <span className="stat-value">{summary.gt_count}</span>
          <span className="stat-label">Ground truths</span>
        </div>
        <div className="stat-card accent">
          <span className="stat-value">{summary.remaining_gt}</span>
          <span className="stat-label">Remaining</span>
        </div>
        <div className="stat-card wide">
          <span className="stat-label">Model coverage</span>
          {models.length === 0 ? (
            <span className="dim">No results yet</span>
          ) : (
            <div className="coverage-bars">
              {models.map((m) => (
                <div key={m} className="coverage-row">
                  <span className="mono coverage-name">{m}</span>
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
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-toolbar">
          <input
            type="search"
            placeholder="Filter by stem…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="search-input"
          />
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={filtered?.length ? selected.size === filtered.length : false}
              onChange={(e) => toggleAll(e.target.checked)}
            />
            Select all visible
          </label>
        </div>

        <OcrPanel
          stems={[...selected]}
          existingModelsByStem={Object.fromEntries(
            (summary?.images ?? [])
              .filter((img) => selected.has(img.stem))
              .map((img) => [img.stem, img.models])
          )}
          onComplete={load}
        />

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th className="col-check" />
                <th>#</th>
                <th>Stem</th>
                <th>GT</th>
                <th>Model results</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered?.map((img, i) => (
                <tr key={img.stem} className={selected.has(img.stem) ? "selected" : ""}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(img.stem)}
                      onChange={() => toggleOne(img.stem)}
                    />
                  </td>
                  <td className="dim">{i + 1}</td>
                  <td className="mono stem-cell">{img.stem}</td>
                  <td>
                    {img.has_gt ? (
                      <span className="badge ok" title={img.gt_file || ""}>
                        ✓
                      </span>
                    ) : (
                      <span className="badge dim">—</span>
                    )}
                  </td>
                  <td className="models-cell">
                    {img.models.length ? (
                      img.models.map((m) => (
                        <span key={m} className="tag">
                          {m}
                        </span>
                      ))
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </td>
                  <td>
                    <Link to={`/workspace/${encodeURIComponent(img.stem)}`} className="btn small">
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

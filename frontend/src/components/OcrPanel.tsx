import { useEffect, useMemo, useState } from "react";
import { api, type OcrJob, type Settings } from "../api";

interface Props {
  stems: string[];
  /** Existing result model names per stem (from disk). */
  existingModelsByStem?: Record<string, string[]>;
  onComplete?: () => void;
  compact?: boolean;
}

const BACKENDS = [
  { id: "lmstudio", label: "LM Studio" },
  { id: "openai", label: "OpenAI" },
  { id: "anthropic", label: "Anthropic" },
] as const;

function safeName(s: string): string {
  return s.replace(/[^a-zA-Z0-9._-]/g, "_").replace(/^_|_$/g, "");
}

function resultExists(existing: string[], model: string): boolean {
  const target = safeName(model);
  return existing.some((m) => m === target || m === model);
}

export default function OcrPanel({ stems, existingModelsByStem, onComplete, compact }: Props) {
  const [backend, setBackend] = useState<string>("lmstudio");
  const [model, setModel] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [lmModels, setLmModels] = useState<string[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [job, setJob] = useState<OcrJob | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setSettings(null));
  }, []);

  useEffect(() => {
    setModel("");
  }, [backend]);

  useEffect(() => {
    if (backend === "lmstudio") {
      api.getLmModels().then((r) => setLmModels(r.models)).catch(() => setLmModels([]));
    }
  }, [backend]);

  useEffect(() => {
    if (!job || job.status === "completed") return;
    const timer = setInterval(async () => {
      const updated = await api.getOcrJob(job.id);
      setJob(updated);
      if (updated.status === "completed") {
        setRunning(false);
        onComplete?.();
      }
    }, 800);
    return () => clearInterval(timer);
  }, [job, onComplete]);

  const targetModel = useMemo(() => {
    if (model) return model;
    if (backend === "lmstudio" && lmModels.length > 0) return lmModels[0];
    if (backend === "openai") return settings?.default_openai_model ?? "gpt-4o";
    if (backend === "anthropic") return settings?.default_anthropic_model ?? "claude-sonnet-4-20250514";
    return null;
  }, [model, backend, lmModels, settings]);

  const conflictingStems = useMemo(() => {
    if (overwrite || !existingModelsByStem || !targetModel) return [];
    return stems.filter((stem) => resultExists(existingModelsByStem[stem] ?? [], targetModel));
  }, [overwrite, existingModelsByStem, targetModel, stems]);

  const blocked = conflictingStems.length > 0;

  async function handleRun() {
    if (!stems.length || blocked) return;
    setError(null);
    setRunning(true);
    try {
      const { job_id } = await api.createOcrJob({
        backend,
        model: model || undefined,
        stems,
        overwrite,
      });
      const j = await api.getOcrJob(job_id);
      setJob(j);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start job");
      setRunning(false);
    }
  }

  const progress = job ? Math.round((job.done / job.total) * 100) : 0;

  return (
    <div className={`ocr-panel ${compact ? "compact" : ""}`}>
      <div className="ocr-row">
        <label>
          Backend
          <select value={backend} onChange={(e) => setBackend(e.target.value)} disabled={running}>
            {BACKENDS.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Model
          {backend === "lmstudio" && lmModels.length > 0 ? (
            <select value={model} onChange={(e) => setModel(e.target.value)} disabled={running}>
              <option value="">Auto-detect</option>
              {lmModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              placeholder={
                backend === "openai"
                  ? (settings?.default_openai_model ?? "gpt-4o")
                  : backend === "anthropic"
                    ? (settings?.default_anthropic_model ?? "claude-sonnet-4-20250514")
                    : "optional"
              }
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={running}
            />
          )}
        </label>
        <label className={`checkbox-label${blocked ? " overwrite-required" : ""}`}>
          <input
            type="checkbox"
            checked={overwrite}
            onChange={(e) => setOverwrite(e.target.checked)}
            disabled={running}
          />
          Overwrite existing results
        </label>
        <button
          className="btn primary"
          onClick={handleRun}
          disabled={running || !stems.length || blocked}
          title={blocked ? "Enable Overwrite to replace existing results" : undefined}
        >
          {running ? "Running…" : `Run OCR (${stems.length})`}
        </button>
      </div>

      {blocked && (
        <div className="ocr-blocked-banner">
          {conflictingStems.length === 1 ? (
            <>
              Result already exists for <span className="mono">{targetModel}</span>. Check{" "}
              <strong>Overwrite existing results</strong> to replace it.
            </>
          ) : (
            <>
              {conflictingStems.length} selected images already have a result for{" "}
              <span className="mono">{targetModel}</span>. Check{" "}
              <strong>Overwrite existing results</strong> to replace them.
            </>
          )}
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {job && (
        <div className="job-progress">
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${progress}%` }} />
          </div>
          <div className="progress-meta">
            <span>
              {job.done}/{job.total}
              {job.current && ` — ${job.current}`}
            </span>
            <span className={job.status === "completed" ? "ok" : ""}>{job.status}</span>
          </div>
          {job.results.length > 0 && (
            <ul className="job-log">
              {job.results.map((r) => (
                <li key={r.stem} className={`log-${r.status}`}>
                  <span className="mono">{r.stem}</span>
                  {r.status === "ok" && <span> → {r.model}</span>}
                  {r.status === "error" && <span> — {r.error}</span>}
                  {r.status === "skipped" && <span> — skipped (already exists)</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

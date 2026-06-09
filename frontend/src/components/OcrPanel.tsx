import { useEffect, useMemo, useRef, useState } from "react";
import { api, type OcrJob, type Settings } from "../api";
import { getOcrJobSnapshot, startTrackedOcrJob, subscribeOcrJobs, type TrackedOcrJob } from "../ocrJobs";

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

let lmModelsCache: string[] | null = null;
let lmModelsPromise: Promise<string[]> | null = null;

async function getCachedLmModels(): Promise<string[]> {
  if (lmModelsCache) return lmModelsCache;
  if (!lmModelsPromise) {
    lmModelsPromise = api
      .getLmModels()
      .then((r) => {
        lmModelsCache = r.models;
        return r.models;
      })
      .catch((e) => {
        lmModelsPromise = null;
        throw e;
      });
  }
  return lmModelsPromise;
}

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
  const [jobs, setJobs] = useState<TrackedOcrJob[]>(() => getOcrJobSnapshot());
  const [startedJobId, setStartedJobId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const completedNotifiedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => setSettings(null));
  }, []);

  useEffect(() => {
    setModel("");
  }, [backend]);

  useEffect(() => {
    if (backend === "lmstudio") {
      getCachedLmModels().then(setLmModels).catch(() => setLmModels([]));
    }
  }, [backend]);

  useEffect(() => {
    return subscribeOcrJobs(() => setJobs(getOcrJobSnapshot()));
  }, []);

  const relevantJobs = useMemo(
    () =>
      jobs.filter((job) =>
        job.requestedStems.some((stem) => stems.includes(stem)) ||
        job.results.some((result) => stems.includes(result.stem))
      ),
    [jobs, stems]
  );

  const job = useMemo<OcrJob | null>(() => {
    if (startedJobId) return relevantJobs.find((j) => j.id === startedJobId) ?? relevantJobs[0] ?? null;
    return relevantJobs[0] ?? null;
  }, [relevantJobs, startedJobId]);

  useEffect(() => {
    const active = relevantJobs.some((j) => j.status !== "completed");
    setRunning(active);
    const newlyCompleted = relevantJobs.filter(
      (j) => j.status === "completed" && !completedNotifiedRef.current.has(j.id)
    );
    if (!newlyCompleted.length) return;
    newlyCompleted.forEach((j) => completedNotifiedRef.current.add(j.id));
    onComplete?.();
  }, [relevantJobs, onComplete]);

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
      const j = await startTrackedOcrJob({
        backend,
        model: model || undefined,
        stems,
        overwrite,
      });
      setStartedJobId(j.id);
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
          Overwrite existing
        </label>
        <div className="ocr-spacer" />
        <button
          className="btn primary"
          onClick={handleRun}
          disabled={running || !stems.length || blocked}
          title={blocked ? "Enable Overwrite to replace existing results" : !stems.length ? "Select documents first" : undefined}
        >
          {running ? "Running…" : stems.length ? `Run OCR · ${stems.length}` : "Run OCR"}
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

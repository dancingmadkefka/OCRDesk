import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ImageDetail } from "../api";
import ComparePane, { type ViewOption } from "../components/ComparePane";
import OcrPanel from "../components/OcrPanel";
import RefinementPanel from "../components/RefinementPanel";
import RestartServerButton from "../components/RestartServerButton";
import { displayStem } from "../stemUtils";

import { stripFences } from "../htmlUtils";

export default function Workspace() {
  const { stem: rawStem } = useParams();
  const stem = rawStem ? decodeURIComponent(rawStem) : "";
  const navigate = useNavigate();

  const [detail, setDetail] = useState<ImageDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [leftView, setLeftView] = useState("image");
  const [rightView, setRightView] = useState("image");
  const [zoom, setZoom] = useState(1);
  const [showManual, setShowManual] = useState(false);
  const [manualText, setManualText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [railOpen, setRailOpen] = useState(true);

  const [refineOpen, setRefineOpen] = useState(false);
  const [refineBaseModel, setRefineBaseModel] = useState<string | null>(null);
  const [refineInitialHtml, setRefineInitialHtml] = useState<string>("");

  const load = useCallback((preferredRight?: string) => {
    if (!stem) return Promise.resolve();
    return api
      .getImage(stem)
      .then((d) => {
        setDetail(d);
        const keys = ["image", ...(d.ground_truth ? ["ground-truth"] : []), ...d.models];
        const fallback = d.ground_truth ? "ground-truth" : d.models[0] || "image";
        setRightView((cur) => {
          if (preferredRight && keys.includes(preferredRight)) return preferredRight;
          if (cur === "image") return fallback;
          return keys.includes(cur) ? cur : fallback;
        });
      })
      .catch((e) => setError(e.message));
  }, [stem]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    setMessage(null);
    setZoom(1);
    setLeftView("image");
    setRightView("image");
    setShowManual(false);
    load();
  }, [stem]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), 5000);
    return () => window.clearTimeout(timer);
  }, [message]);

  const viewOptions: ViewOption[] = useMemo(() => {
    if (!detail) return [];
    const opts: ViewOption[] = [{ key: "image", label: "Original image (photo)", type: "image" }];
    if (detail.ground_truth) {
      opts.push({
        key: "ground-truth",
        label: `Ground truth: ${detail.ground_truth.file}`,
        type: "html",
        html: detail.ground_truth.html,
        raw: detail.ground_truth.raw,
        editable: true,
      });
    }
    for (const m of detail.models) {
      opts.push({
        key: m,
        label: `Candidate: ${m}`,
        type: "html",
        html: detail.model_contents[m],
        raw: detail.model_contents[m],
        editable: true,
      });
    }
    return opts;
  }, [detail]);

  const handleSaveHtml = useCallback(
    async (key: string, html: string) => {
      if (!stem) return;
      if (key === "ground-truth") {
        const res = await api.saveGroundTruth(stem, html);
        setMessage(`Saved ${res.ground_truth}`);
        await load("ground-truth");
      } else {
        await api.saveResult(stem, key, html);
        setMessage(`Saved candidate ${key}`);
        await load(key);
      }
    },
    [stem, load]
  );

  useEffect(() => {
    const keys = viewOptions.map((o) => o.key);
    if (!keys.includes(leftView)) setLeftView("image");
    if (!keys.includes(rightView)) {
      setRightView(keys.includes("ground-truth") ? "ground-truth" : keys[1] || "image");
    }
  }, [viewOptions, leftView, rightView]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowLeft" && detail?.prev_stem) navigate(`/workspace/${encodeURIComponent(detail.prev_stem)}`);
      if (e.key === "ArrowRight" && detail?.next_stem) navigate(`/workspace/${encodeURIComponent(detail.next_stem)}`);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detail, navigate]);

  async function handleManualSave() {
    if (!stem || !manualText.trim()) return;
    const html = stripFences(manualText);
    try {
      await api.saveManual(stem, html);
      if (!detail?.has_gt) {
        const res = await api.saveGroundTruth(stem, html);
        setMessage(`Saved and set as ground truth (${res.ground_truth})`);
      } else {
        setMessage("Manual result saved");
      }
      setShowManual(false);
      setManualText("");
      await load(!detail?.has_gt ? "ground-truth" : undefined);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Save failed");
    }
  }

  async function handleSetSelectedAsGroundTruth() {
    if (!stem || !detail) return;
    const selected = viewOptions.find((o) => o.key === rightView);
    if (!selected || selected.type !== "html" || selected.key === "ground-truth") return;
    try {
      const html = stripFences(selected.raw ?? selected.html ?? "");
      const res = await api.saveGroundTruth(stem, html);
      setMessage(`Set ${selected.label.replace(/^Candidate:\s*/, "")} as ground truth (${res.ground_truth})`);
      await load("ground-truth");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Failed to set ground truth");
    }
  }

  function startRefinementFrom(modelKey: string) {
    if (!detail) return;
    const content =
      detail.model_contents[modelKey] ||
      (modelKey === "ground-truth" && detail.ground_truth ? detail.ground_truth.raw : "");
    if (!content) return;
    setRefineBaseModel(modelKey);
    setRefineInitialHtml(content);
    setRefineOpen(true);
    setMessage(null);
  }

  async function handleRefineAccept(html: string, targetModelName?: string) {
    if (!stem) return;
    const name = targetModelName || refineBaseModel || "refined";
    await api.saveResult(stem, name, html);
    setMessage(`Saved refined output as ${name}`);
    setRefineOpen(false);
    setRefineBaseModel(null);
    setRefineInitialHtml("");
    await load(name);
  }

  if (error) return <div className="error-banner ws-error">{error}</div>;
  if (!detail || detail.stem !== stem) return <div className="loading">Loading document…</div>;

  const { title } = displayStem(detail.stem);
  const canRefine = detail.models.length > 0 || detail.ground_truth;
  const rightOption = viewOptions.find((o) => o.key === rightView);
  const canSetRightAsGt = Boolean(rightOption?.type === "html" && rightView !== "ground-truth");

  return (
    <div className={`workspace workspace-v2${refineOpen ? " refining" : ""}${railOpen ? " rail-open" : ""}`}>
      <header className="ws-header">
        <div className="ws-header-left">
          <Link to="/" className="ws-back" title="Back to corpus">
            ← Corpus
          </Link>
          <div className="ws-doc-meta">
            <h1 className="ws-doc-title">{title}</h1>
            <span className="ws-doc-progress">
              {detail.index} / {detail.total}
              {detail.has_gt && <span className="ws-verified-pill">Verified</span>}
            </span>
          </div>
        </div>

        <div className="ws-header-center">
          <button
            className="btn icon"
            disabled={!detail.prev_stem}
            onClick={() => detail.prev_stem && navigate(`/workspace/${encodeURIComponent(detail.prev_stem)}`)}
            title="Previous document (←)"
          >
            ‹
          </button>
          <button
            className="btn icon"
            disabled={!detail.next_stem}
            onClick={() => detail.next_stem && navigate(`/workspace/${encodeURIComponent(detail.next_stem)}`)}
            title="Next document (→)"
          >
            ›
          </button>
        </div>

        <div className="ws-header-right">
          <label className="zoom-control">
            <span className="zoom-label">Zoom</span>
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.05}
              value={zoom}
              onChange={(e) => setZoom(parseFloat(e.target.value))}
            />
            <span className="zoom-value">{Math.round(zoom * 100)}%</span>
          </label>
          <RestartServerButton />
        </div>
      </header>

      {message && (
        <div className="toast" role="status">
          <span>{message}</span>
          <button type="button" className="toast-close" onClick={() => setMessage(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <div className="ws-stage">
        {refineOpen && refineBaseModel ? (
          <RefinementPanel
            stem={stem}
            baseModel={refineBaseModel}
            initialHtml={refineInitialHtml}
            onClose={() => {
              setRefineOpen(false);
              setRefineBaseModel(null);
              setRefineInitialHtml("");
            }}
            onAccept={handleRefineAccept}
            onSaved={load}
          />
        ) : (
          <>
            <div className="compare-container ws-compare">
              <ComparePane
                key={`${stem}-L`}
                stem={stem}
                side="L"
                selected={leftView}
                options={viewOptions}
                onChange={setLeftView}
                zoom={zoom}
                onSave={handleSaveHtml}
              />
              <div className="pane-divider" />
              <ComparePane
                key={`${stem}-R`}
                stem={stem}
                side="R"
                selected={rightView}
                options={viewOptions}
                onChange={setRightView}
                zoom={zoom}
                onSave={handleSaveHtml}
              />
            </div>

            <aside className={`ws-rail${railOpen ? " open" : ""}`}>
              <button
                type="button"
                className="ws-rail-toggle"
                onClick={() => setRailOpen((v) => !v)}
                aria-expanded={railOpen}
                title={railOpen ? "Collapse actions" : "Expand actions"}
              >
                {railOpen ? "›" : "‹"}
                <span className="ws-rail-toggle-label">Actions</span>
              </button>

              {railOpen && (
                <div className="ws-rail-body">
                  <p className="ws-rail-heading">Run &amp; refine</p>
                  <OcrPanel
                    stems={[stem]}
                    existingModelsByStem={detail ? { [stem]: detail.models } : undefined}
                    onComplete={load}
                    compact
                  />

                  <div className="ws-gt-status">
                    <p className="ws-gt-title">{detail.has_gt ? "Ground truth set" : "No ground truth yet"}</p>
                    <p className="ws-gt-copy">
                      {detail.ground_truth
                        ? detail.ground_truth.file
                        : "Dropdown candidates are model outputs until one is explicitly set as GT."}
                    </p>
                  </div>

                  <button
                    className="btn primary ws-rail-btn"
                    onClick={handleSetSelectedAsGroundTruth}
                    disabled={!canSetRightAsGt}
                    title={
                      canSetRightAsGt
                        ? "Copy the selected right-pane candidate into the ground truth file"
                        : "Select a candidate in the right pane first"
                    }
                  >
                    Set selected as ground truth
                  </button>

                  {canRefine && (
                    <button
                      className="btn accent ws-rail-btn"
                      onClick={() => {
                        let candidate = rightView;
                        if (candidate !== "ground-truth" && !detail.models.includes(candidate)) {
                          candidate = detail.models[0] || "ground-truth";
                        }
                        startRefinementFrom(candidate);
                      }}
                    >
                      Refine with AI critique
                    </button>
                  )}

                  <button
                    className={`btn ws-rail-btn${showManual ? " accent" : ""}`}
                    onClick={() => setShowManual((v) => !v)}
                  >
                    {showManual ? "Hide paste panel" : "Paste HTML manually"}
                  </button>

                  {showManual && (
                    <div className="ws-rail-manual">
                      <textarea
                        value={manualText}
                        onChange={(e) => setManualText(e.target.value)}
                        placeholder="Paste OCR HTML…"
                        rows={6}
                      />
                      <div className="manual-actions">
                        <button className="btn primary" onClick={handleManualSave}>
                          {detail.has_gt ? "Save" : "Save as ground truth"}
                        </button>
                      </div>
                    </div>
                  )}

                  <p className="ws-rail-hint">
                    Edit HTML in the right pane and save. Use <kbd>←</kbd> <kbd>→</kbd> to switch documents.
                  </p>
                </div>
              )}
            </aside>
          </>
        )}
      </div>
    </div>
  );
}

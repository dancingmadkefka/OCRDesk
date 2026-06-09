import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ImageDetail } from "../api";
import ComparePane, { type ViewOption } from "../components/ComparePane";
import OcrPanel from "../components/OcrPanel";
import RefinementPanel from "../components/RefinementPanel";

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

  // Refinement session
  const [refineOpen, setRefineOpen] = useState(false);
  const [refineBaseModel, setRefineBaseModel] = useState<string | null>(null);
  const [refineInitialHtml, setRefineInitialHtml] = useState<string>("");

  const load = useCallback(() => {
    if (!stem) return;
    api
      .getImage(stem)
      .then((d) => {
        setDetail(d);
        if (d.ground_truth) {
          setRightView("ground-truth");
        } else if (d.models.length) {
          setRightView(d.models[0]);
        }
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
    load();
  }, [stem]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(null), 5000);
    return () => window.clearTimeout(timer);
  }, [message]);

  const viewOptions: ViewOption[] = useMemo(() => {
    if (!detail) return [];
    const opts: ViewOption[] = [{ key: "image", label: "Original image", type: "image" }];
    if (detail.ground_truth) {
      opts.push({
        key: "ground-truth",
        label: `Ground truth (${detail.ground_truth.file})`,
        type: "html",
        html: detail.ground_truth.html,
        raw: detail.ground_truth.raw,
        editable: true,
      });
    } else {
      for (const m of detail.models) {
        opts.push({
          key: m,
          label: `Model: ${m}`,
          type: "html",
          html: detail.model_contents[m],
          raw: detail.model_contents[m],
          editable: true,
        });
      }
    }
    return opts;
  }, [detail]);

  const handleSaveHtml = useCallback(
    async (key: string, html: string) => {
      if (!stem) return;
      const hasGt = detail?.has_gt ?? false;
      if (key === "ground-truth" || hasGt) {
        const res = await api.saveGroundTruth(stem, html);
        setMessage(`Saved ${res.ground_truth}`);
      } else {
        await api.saveResult(stem, key, html);
        const res = await api.saveGroundTruth(stem, html);
        setMessage(`Saved and set as ground truth (${res.ground_truth})`);
      }
      await load();
    },
    [stem, load, detail?.has_gt]
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
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Save failed");
    }
  }

  function startRefinementFrom(modelKey: string) {
    if (!detail) return;
    const content = detail.model_contents[modelKey] || (modelKey === "ground-truth" && detail.ground_truth ? detail.ground_truth.raw : "");
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
    // Close the panel and reload so the new variant appears in the compare dropdowns
    setRefineOpen(false);
    setRefineBaseModel(null);
    setRefineInitialHtml("");
    await load();
  }

  if (error) return <div className="error-banner">{error}</div>;
  if (!detail || detail.stem !== stem) return <div className="loading">Loading…</div>;

  return (
    <div className={`workspace${refineOpen ? " workspace-refining" : ""}`}>
      <div className="workspace-toolbar">
        <div className="toolbar-left">
          <Link to="/" className="btn ghost">
            ← Dashboard
          </Link>
          <div className="nav-cluster">
            <button
              className="btn icon"
              disabled={!detail.prev_stem}
              onClick={() => detail.prev_stem && navigate(`/workspace/${encodeURIComponent(detail.prev_stem)}`)}
              title="Previous (←)"
            >
              ‹
            </button>
            <span className="nav-position">
              <span className="mono">{detail.stem}</span>
              <span className="dim">
                {detail.index}/{detail.total}
              </span>
            </span>
            <button
              className="btn icon"
              disabled={!detail.next_stem}
              onClick={() => detail.next_stem && navigate(`/workspace/${encodeURIComponent(detail.next_stem)}`)}
              title="Next (→)"
            >
              ›
            </button>
          </div>
        </div>

        <div className="toolbar-center">
          <label className="zoom-control">
            Zoom
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.05}
              value={zoom}
              onChange={(e) => setZoom(parseFloat(e.target.value))}
            />
            <span>{Math.round(zoom * 100)}%</span>
          </label>
          <button className="btn small" onClick={() => setZoom(1)}>
            Reset
          </button>
        </div>

        <div className="toolbar-right">
          <button className="btn" onClick={() => setShowManual(!showManual)}>
            Manual paste
          </button>
        </div>
      </div>

      {message && (
        <div className="toast" role="status">
          <span>{message}</span>
          <button type="button" className="toast-close" onClick={() => setMessage(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      {!refineOpen && (
        <div className="workspace-actions">
          <OcrPanel
            stems={[stem]}
            existingModelsByStem={detail ? { [stem]: detail.models } : undefined}
            onComplete={load}
            compact
          />
          {detail && (detail.models.length > 0 || detail.ground_truth) && (
            <button
              className="btn accent"
              style={{ marginLeft: 12 }}
              onClick={() => {
                let candidate = rightView;
                if (candidate !== "ground-truth" && !detail.models.includes(candidate)) {
                  candidate = detail.models[0] || "ground-truth";
                }
                startRefinementFrom(candidate);
              }}
              title="Compare original vs previous vs proposed HTML in a dedicated refinement view"
            >
              Refine / self-critique…
            </button>
          )}
        </div>
      )}

      {showManual && (
        <div className="manual-panel">
          <p className="hint">
            Paste HTML from ChatGPT, Gemini, etc. Markdown fences are stripped automatically.
            {!detail.has_gt && " Saving will also create the ground truth."}
          </p>
          <textarea
            value={manualText}
            onChange={(e) => setManualText(e.target.value)}
            placeholder="Paste OCR HTML here…"
            rows={8}
          />
          <div className="manual-actions">
            <button className="btn primary" onClick={handleManualSave}>
              {detail.has_gt ? "Save as manual" : "Save as ground truth"}
            </button>
            <button className="btn ghost" onClick={() => setShowManual(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

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
      <div className="compare-container">
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
      )}

      {!refineOpen && (
        <div className="workspace-hint">
          {detail.has_gt
            ? "Edit the ground truth HTML directly — Save updates the ground truth file."
            : "Edit a model result and Save — it becomes the ground truth. Arrow keys navigate between images."}
        </div>
      )}
    </div>
  );
}

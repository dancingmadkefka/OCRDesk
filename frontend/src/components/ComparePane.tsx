import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { wrapForIframe } from "../htmlUtils";
import { useAutoFrame } from "../useAutoFrame";

export interface ViewOption {
  key: string;
  label: string;
  type: "image" | "html";
  html?: string;
  /** Raw file content for editing (may differ from rendered html for .md GT). */
  raw?: string;
  editable?: boolean;
}

interface Props {
  stem: string;
  side: "L" | "R";
  selected: string;
  options: ViewOption[];
  onChange: (key: string) => void;
  zoom: number;
  onSave?: (key: string, html: string) => Promise<void>;
  onLiveContent?: (key: string, html: string | null) => void;
}

function frameStyle(layout: { widthPx: number | null }) {
  return layout.widthPx ? { width: `${layout.widthPx}px` } : undefined;
}

export default function ComparePane({ stem, side, selected, options, onChange, zoom, onSave, onLiveContent }: Props) {
  const current = useMemo(() => options.find((o) => o.key === selected), [options, selected]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  const previewHtml = editing ? draft : current?.html || "";
  const showHtml = current?.type === "html" && previewHtml;
  const iframeDoc = useMemo(
    () => (showHtml ? wrapForIframe(previewHtml) : ""),
    [showHtml, previewHtml]
  );

  const previewWrapRef = useRef<HTMLDivElement>(null);
  const editWrapRef = useRef<HTMLDivElement>(null);

  const previewFrame = useAutoFrame(iframeDoc, Boolean(showHtml && !editing), previewWrapRef);
  const editFrame = useAutoFrame(iframeDoc, Boolean(showHtml && editing), editWrapRef);

  const canEdit = current?.type === "html" && current.editable && onSave;

  const exitEdit = useCallback(() => {
    setEditing(false);
    setDraft("");
    setDirty(false);
  }, []);

  useEffect(() => {
    exitEdit();
  }, [selected, exitEdit]);

  useEffect(() => {
    if (!onLiveContent) return;
    if (editing && current?.type === "html") {
      onLiveContent(selected, draft);
    } else {
      onLiveContent(selected, null);
    }
  }, [editing, selected, draft, current?.type, onLiveContent]);

  function startEdit() {
    if (!current) return;
    setDraft(current.raw ?? current.html ?? "");
    setDirty(false);
    setEditing(true);
  }

  const handleSave = useCallback(async () => {
    if (!onSave || !canEdit) return;
    setSaving(true);
    try {
      await onSave(selected, draft);
      exitEdit();
    } finally {
      setSaving(false);
    }
  }, [onSave, canEdit, selected, draft, exitEdit]);

  function handleCancel() {
    if (dirty && !confirm("Discard unsaved edits?")) return;
    exitEdit();
  }

  useEffect(() => {
    if (!editing) return;
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        void handleSave();
      }
      if (e.key === "Escape") handleCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, dirty, exitEdit, handleSave]);

  return (
    <div className={`compare-pane pane-${side.toLowerCase()}${editing ? " is-editing" : ""}`}>
      <div className="pane-header">
        <select
          value={selected}
          onChange={(e) => onChange(e.target.value)}
          className="pane-select"
          disabled={editing}
        >
          {options.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
        {canEdit && !editing && (
          <button type="button" className="btn small pane-edit-btn" onClick={startEdit}>
            Edit
          </button>
        )}
        {editing && (
          <div className="pane-edit-actions">
            {dirty && <span className="dirty-dot" title="Unsaved changes" />}
            <button type="button" className="btn small primary" onClick={handleSave} disabled={saving || !dirty}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn small ghost" onClick={handleCancel} disabled={saving}>
              Cancel
            </button>
          </div>
        )}
      </div>

      <div className={`pane-body${showHtml ? " with-auto-frame" : " pane-body-image"}`}>
        {current?.type === "image" ? (
          <div
            className="pane-zoom-wrap image-wrap"
            style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}
          >
            <img src={api.imageUrl(stem)} alt="Original document" className="doc-image" />
          </div>
        ) : editing ? (
          <div className="edit-split">
            <div ref={editWrapRef} className="edit-preview with-auto-frame">
              <div className="edit-preview-label">Preview</div>
              <iframe
                ref={editFrame.iframeRef}
                className="rendered-frame edit-preview-frame with-auto-frame"
                style={frameStyle(editFrame.layout)}
                srcDoc={iframeDoc}
                sandbox="allow-same-origin"
                title={`${side} edit preview`}
                onLoad={editFrame.onIframeLoad}
              />
            </div>
            <div className="edit-source">
              <div className="edit-preview-label">Source · Ctrl+S save · Esc cancel</div>
              <textarea
                className="edit-textarea"
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setDirty(true);
                }}
                spellCheck={false}
              />
            </div>
          </div>
        ) : (
          <div ref={previewWrapRef} className="pane-zoom-wrap html-wrap with-auto-frame">
            <iframe
              ref={previewFrame.iframeRef}
              className="rendered-frame with-auto-frame"
              style={{
                ...frameStyle(previewFrame.layout),
                transform: zoom !== 1 ? `scale(${zoom})` : undefined,
                transformOrigin: "top center",
              }}
              srcDoc={iframeDoc}
              sandbox="allow-same-origin"
              title={`${side} document view`}
              onLoad={previewFrame.onIframeLoad}
            />
          </div>
        )}
      </div>
    </div>
  );
}

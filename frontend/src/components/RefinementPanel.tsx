import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  api,
  type FeedbackBBox,
  type FeedbackItem,
  type RefineImprovement,
  type Settings,
} from "../api";
import { wrapForIframe, stripFences } from "../htmlUtils";
import html2canvas from "html2canvas";

export interface RefinementIteration {
  id: number;
  satisfied: boolean;
  html: string;
  note?: string;
  improvements: RefineImprovement[];
  feedbacksUsed: FeedbackItem[];
  screenshotUsed?: string | null;
}

type AnnotateTarget = "proposed" | "before" | null;

interface Props {
  stem: string;
  baseModel: string;
  initialHtml: string;
  onClose: () => void;
  onAccept: (html: string, targetModelName?: string) => Promise<void> | void;
  onSaved?: () => void;
}

const BACKENDS = [
  { id: "lmstudio", label: "LM Studio" },
  { id: "openai", label: "OpenAI" },
  { id: "anthropic", label: "Anthropic" },
] as const;

function safeName(s: string): string {
  return s.replace(/[^a-zA-Z0-9._-]/g, "_").replace(/^_|_$/g, "");
}

function looksLikeHtml(text: string): boolean {
  const s = stripFences(text).trim();
  if (s.length < 24) return false;
  return /<!DOCTYPE\s+html|<html[\s>]|<body[\s>]|<table[\s>]|<div[\s>]|<p[\s>]|<h[1-6][\s>]/i.test(s);
}

async function compressDataUrl(dataUrl: string, maxWidth = 1200, quality = 0.82): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = img.width > maxWidth ? maxWidth / img.width : 1;
      const w = Math.max(1, Math.round(img.width * scale));
      const h = Math.max(1, Math.round(img.height * scale));
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        reject(new Error("Canvas not available"));
        return;
      }
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      resolve(canvas.toDataURL("image/jpeg", quality));
    };
    img.onerror = () => reject(new Error("Failed to load screenshot for compression"));
    img.src = dataUrl;
  });
}

const RENDER_SHOT_WIDTH = 1200;

/** Drag boxes on the rendered HTML; each drag adds a flag (parent accumulates). */
function regionAnnotateScript() {
  return `
<script>
(function(){
  function post(payload){
    try { window.parent.postMessage(Object.assign({type:'refine-annotate'}, payload), '*'); } catch(e){}
  }
  var start=null, active=false, overlay=null, box=null, flagCount=0;

  function norm(r){
    var l=Math.min(r.x1,r.x2), t=Math.min(r.y1,r.y2);
    var w=Math.abs(r.x2-r.x1), h=Math.abs(r.y2-r.y1);
    return {left:l, top:t, width:w, height:h, right:l+w, bottom:t+h};
  }
  function viewport(){
    var de=document.documentElement, body=document.body;
    return {
      w: Math.max(body.scrollWidth, de.scrollWidth, de.clientWidth, 1),
      h: Math.max(body.scrollHeight, de.scrollHeight, de.clientHeight, 1)
    };
  }
  function centerInRect(el, R){
    var br=el.getBoundingClientRect();
    if(br.width<1||br.height<1) return false;
    var cx=br.left+br.width/2, cy=br.top+br.height/2;
    return cx>=R.left&&cx<=R.right&&cy>=R.top&&cy<=R.bottom;
  }
  function leafElements(els){
    return els.filter(function(el){
      return !els.some(function(other){ return other!==el&&el.contains(other); });
    });
  }
  function textInRect(r){
    var R=norm(r);
    var matched=[];
    var nodes=document.body.querySelectorAll('td,th,p,li,span,h1,h2,h3,h4,h5,h6');
    nodes.forEach(function(el){
      if(el.classList&&el.classList.contains('refine-flag-mark')) return;
      if(!centerInRect(el,R)) return;
      matched.push(el);
    });
    var leaves=leafElements(matched);
    var parts=[], seen=new Set();
    leaves.forEach(function(el){
      var t=(el.textContent||'').replace(/\\s+/g,' ').trim();
      if(t.length<1||seen.has(t)) return;
      seen.add(t);
      parts.push(t);
    });
    var out=parts.join(' | ');
    if(out.length>400) out=out.slice(0,397)+'…';
    return out;
  }
  function addFlagMark(r){
    var R=norm(r);
    flagCount+=1;
    var mark=document.createElement('div');
    mark.className='refine-flag-mark';
    mark.style.cssText='position:fixed;left:'+R.left+'px;top:'+R.top+'px;width:'+R.width+'px;height:'+R.height+'px;border:2px solid #e8a838;background:rgba(232,168,56,0.18);pointer-events:none;z-index:99997;box-sizing:border-box;';
    var label=document.createElement('span');
    label.textContent=String(flagCount);
    label.style.cssText='position:absolute;top:-1px;left:-1px;min-width:18px;height:18px;padding:0 4px;background:#e8a838;color:#1a1200;font:bold 11px/18px system-ui,sans-serif;text-align:center;border-radius:0 0 4px 0;';
    mark.appendChild(label);
    document.body.appendChild(mark);
  }
  function teardownDrag(){
    if(overlay&&overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if(box&&box.parentNode) box.parentNode.removeChild(box);
    overlay=box=null;
    active=false;
    start=null;
  }
  function mountDrag(){
    teardownDrag();
    overlay=document.createElement('div');
    overlay.style.cssText='position:fixed;inset:0;z-index:99999;cursor:crosshair;background:rgba(0,0,0,0.04);';
    box=document.createElement('div');
    box.style.cssText='position:fixed;border:2px solid #e8a838;background:rgba(232,168,56,0.18);pointer-events:none;display:none;z-index:100000;';
    document.body.appendChild(overlay);
    document.body.appendChild(box);
    overlay.addEventListener('mousedown',onDown);
    overlay.addEventListener('mousemove',onMove);
    overlay.addEventListener('mouseup',onUp);
    overlay.addEventListener('mouseleave',function(e){ if(active) onUp(e); });
  }
  function onDown(e){
    e.preventDefault();
    start={x:e.clientX,y:e.clientY};
    active=true;
    box.style.display='block';
    box.style.left=e.clientX+'px';
    box.style.top=e.clientY+'px';
    box.style.width='0';
    box.style.height='0';
  }
  function onMove(e){
    if(!active||!start) return;
    var l=Math.min(start.x,e.clientX), t=Math.min(start.y,e.clientY);
    box.style.left=l+'px';
    box.style.top=t+'px';
    box.style.width=Math.abs(e.clientX-start.x)+'px';
    box.style.height=Math.abs(e.clientY-start.y)+'px';
  }
  function onUp(e){
    if(!active||!start) return;
    active=false;
    var r={x1:start.x,y1:start.y,x2:e.clientX,y2:e.clientY};
    start=null;
    if(Math.abs(r.x2-r.x1)<8&&Math.abs(r.y2-r.y1)<8){
      var sel=window.getSelection&&window.getSelection();
      var t=(sel&&sel.toString()||'').trim();
      teardownDrag();
      if(t.length>1){ post({text:t, mode:'text', keepAnnotating:true}); mountDrag(); return; }
      post({text:'', mode:'cancel'});
      return;
    }
    addFlagMark(r);
    var R=norm(r);
    var vp=viewport();
    var text=textInRect(r);
    teardownDrag();
    post({
      text:text||'(flagged region)',
      mode:'region',
      keepAnnotating:true,
      bbox:{x:R.left/vp.w,y:R.top/vp.h,w:R.width/vp.w,h:R.height/vp.h}
    });
    mountDrag();
  }
  mountDrag();
})();
</script>`;
}

async function renderHtmlToCanvas(html: string): Promise<{ canvas: HTMLCanvasElement; cleanup: () => void }> {
  const wrapped = wrapForIframe(html);
  const iframe = document.createElement("iframe");
  iframe.style.cssText = `position:fixed;left:-99999px;top:0;width:${RENDER_SHOT_WIDTH}px;height:1600px`;
  iframe.setAttribute("sandbox", "allow-same-origin");
  document.body.appendChild(iframe);
  const idoc = iframe.contentDocument!;
  idoc.open();
  idoc.write(wrapped);
  idoc.close();
  await new Promise((r) => setTimeout(r, 120));
  const target = idoc.body || idoc.documentElement;
  const canvas = await html2canvas(target as HTMLElement, {
    backgroundColor: "#ffffff",
    scale: 1,
    logging: false,
  });
  return {
    canvas,
    cleanup: () => {
      document.body.removeChild(iframe);
    },
  };
}

function drawFeedbackBoxes(canvas: HTMLCanvasElement, feedbacks: FeedbackItem[]): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  feedbacks.forEach((fb, i) => {
    if (!fb.bbox) return;
    const { x, y, bw, bh } = {
      x: fb.bbox.x * w,
      y: fb.bbox.y * h,
      bw: fb.bbox.w * w,
      bh: fb.bbox.h * h,
    };
    ctx.strokeStyle = "#e8a838";
    ctx.lineWidth = Math.max(2, Math.round(w / 600));
    ctx.fillStyle = "rgba(232,168,56,0.22)";
    ctx.fillRect(x, y, bw, bh);
    ctx.strokeRect(x, y, bw, bh);
    const label = String(i + 1);
    const fontSize = Math.max(14, Math.round(w / 85));
    ctx.font = `bold ${fontSize}px system-ui, sans-serif`;
    const tw = ctx.measureText(label).width + 10;
    ctx.fillStyle = "#e8a838";
    ctx.fillRect(x, y, tw, fontSize + 6);
    ctx.fillStyle = "#1a1200";
    ctx.fillText(label, x + 5, y + fontSize);
  });
}

async function captureAnnotatedScreenshot(html: string, feedbacks: FeedbackItem[]): Promise<string> {
  const { canvas, cleanup } = await renderHtmlToCanvas(html);
  try {
    drawFeedbackBoxes(canvas, feedbacks);
    return compressDataUrl(canvas.toDataURL("image/png"));
  } finally {
    cleanup();
  }
}

function injectAnnotate(html: string): string {
  const base = wrapForIframe(html);
  const script = regionAnnotateScript();
  if (/<\/body>/i.test(base)) return base.replace(/<\/body>/i, script + "</body>");
  return base.replace(/<\/html>/i, script + "</html>");
}

function CompareColumn({
  label,
  sublabel,
  variant,
  annotating,
  children,
}: {
  label: string;
  sublabel?: string;
  variant?: "before" | "proposed" | "original";
  annotating?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`refine-col refine-col-${variant || "default"}${annotating ? " is-annotating" : ""}`}>
      <div className="refine-col-header">
        <span className="refine-col-title">{label}</span>
        {sublabel && <span className="refine-col-sub">{sublabel}</span>}
        {annotating && <span className="refine-annotate-badge">drag to flag</span>}
      </div>
      <div className="refine-col-body">{children}</div>
    </div>
  );
}

function ChangelogSection({
  items,
  satisfied,
  critiqueRan,
  onShowRaw,
}: {
  items: RefineImprovement[];
  satisfied?: boolean;
  critiqueRan: boolean;
  onShowRaw?: () => void;
}) {
  if (!critiqueRan) return null;

  if (!items.length) {
    return (
      <div className="refine-changelog refine-changelog-empty">
        <span className="refine-changelog-title">Model changelog</span>
        <p className="refine-changelog-empty-text">
          The model did not return a structured list of changes. Compare the Before and Proposed columns visually.
          {onShowRaw && (
            <>
              {" "}
              <button type="button" className="refine-link-btn" onClick={onShowRaw}>
                View raw response
              </button>
            </>
          )}
        </p>
      </div>
    );
  }

  return (
    <div className={`refine-changelog${satisfied ? " refine-changelog-verified" : ""}`}>
      <div className="refine-changelog-header">
        <span className="refine-changelog-title">{satisfied ? "Verified" : "What changed"}</span>
        <span className="refine-changelog-count">{items.length}</span>
      </div>
      <div className="refine-changelog-list">
        {items.map((item, i) => (
          <article key={i} className="refine-changelog-item">
            {item.area && <span className="refine-changelog-area">{item.area}</span>}
            <div className="refine-changelog-change">{item.change}</div>
            <div className="refine-changelog-reason">{item.reason}</div>
          </article>
        ))}
      </div>
    </div>
  );
}

export default function RefinementPanel({
  stem,
  baseModel,
  initialHtml,
  onClose,
  onAccept,
  onSaved,
}: Props) {
  const seed = stripFences(initialHtml);

  const [backend, setBackend] = useState("lmstudio");
  const [model, setModel] = useState("");
  const [lmModels, setLmModels] = useState<string[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [zoom, setZoom] = useState(1);

  const [workingHtml, setWorkingHtml] = useState(seed);
  const [beforeHtml, setBeforeHtml] = useState(seed);
  const [proposedHtml, setProposedHtml] = useState<string | null>(null);

  const [iterations, setIterations] = useState<RefinementIteration[]>([]);
  const [feedbacks, setFeedbacks] = useState<FeedbackItem[]>([]);
  const [freeNote, setFreeNote] = useState("");
  const [screenshotDataUrl, setScreenshotDataUrl] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);

  const [annotateTarget, setAnnotateTarget] = useState<AnnotateTarget>(null);

  const [running, setRunning] = useState(false);
  const [satisfied, setSatisfied] = useState(false);
  const [satisfiedNote, setSatisfiedNote] = useState<string | undefined>();
  const [improvements, setImprovements] = useState<RefineImprovement[]>([]);
  const [critiqueRan, setCritiqueRan] = useState(false);
  const [lastRaw, setLastRaw] = useState<string | undefined>();
  const [parseError, setParseError] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showRaw, setShowRaw] = useState(false);

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

  const targetModel = useMemo(() => {
    if (model) return model;
    if (backend === "lmstudio" && lmModels.length > 0) return lmModels[0];
    if (backend === "openai") return settings?.default_openai_model ?? "gpt-4o";
    if (backend === "anthropic") return settings?.default_anthropic_model ?? "claude-sonnet-4-20250514";
    return null;
  }, [model, backend, lmModels, settings]);

  useEffect(() => {
    function onMsg(ev: MessageEvent) {
      const data = ev.data;
      if (data?.type !== "refine-annotate") return;
      if (data.mode === "cancel") {
        setAnnotateTarget(null);
        return;
      }
      const txt = typeof data.text === "string" ? data.text.trim() : "";
      if (txt.length > 0) {
        const item: FeedbackItem = { excerpt: txt };
        const b = data.bbox;
        if (
          b &&
          typeof b.x === "number" &&
          typeof b.y === "number" &&
          typeof b.w === "number" &&
          typeof b.h === "number"
        ) {
          item.bbox = { x: b.x, y: b.y, w: b.w, h: b.h } satisfies FeedbackBBox;
        }
        setFeedbacks((prev) => [...prev, item]);
      }
      if (!data.keepAnnotating) setAnnotateTarget(null);
    }
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  function addFreeNote() {
    const c = freeNote.trim();
    if (!c) return;
    setFeedbacks((prev) => [...prev, { comment: c }]);
    setFreeNote("");
  }

  function updateFeedbackComment(idx: number, comment: string) {
    setFeedbacks((prev) => prev.map((fb, i) => (i === idx ? { ...fb, comment: comment || undefined } : fb)));
  }

  function removeFeedback(idx: number) {
    setFeedbacks((prev) => prev.filter((_, i) => i !== idx));
  }

  async function captureScreenshot() {
    setCapturing(true);
    setError(null);
    try {
      const htmlForShot = proposedHtml || workingHtml;
      const { canvas, cleanup } = await renderHtmlToCanvas(htmlForShot);
      try {
        if (feedbacks.some((fb) => fb.bbox)) drawFeedbackBoxes(canvas, feedbacks);
        setScreenshotDataUrl(await compressDataUrl(canvas.toDataURL("image/png")));
      } finally {
        cleanup();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to capture screenshot");
    } finally {
      setCapturing(false);
    }
  }

  async function runRefine() {
    if (!workingHtml.trim()) return;
    setRunning(true);
    setError(null);
    setParseError(undefined);
    setSatisfied(false);
    setSatisfiedNote(undefined);
    setProposedHtml(null);
    setImprovements([]);
    setAnnotateTarget(null);
    setShowRaw(false);

    const sentHtml = workingHtml;
    setBeforeHtml(sentHtml);

    try {
      const flagged = feedbacks.filter((fb) => fb.bbox);
      let screenshot_b64: string | undefined;
      if (flagged.length > 0) {
        screenshot_b64 = await captureAnnotatedScreenshot(sentHtml, feedbacks);
      } else if (screenshotDataUrl) {
        screenshot_b64 = await compressDataUrl(screenshotDataUrl);
      }

      const res = await api.refine({
        stem,
        backend,
        model: targetModel || undefined,
        prev_html: sentHtml,
        screenshot_b64,
        feedbacks: feedbacks.length ? feedbacks : undefined,
      });

      const raw = res.raw || "";
      setLastRaw(raw);
      setCritiqueRan(true);

      const resImprovements = res.improvements ?? [];

      if (res.satisfied) {
        const note = (res.note || res.output || "").trim() || undefined;
        setSatisfied(true);
        setSatisfiedNote(note);
        setImprovements(resImprovements);
        setFeedbacks([]);
        setIterations((prev) => [
          ...prev,
          {
            id: (prev[prev.length - 1]?.id ?? 0) + 1,
            satisfied: true,
            html: "",
            note,
            improvements: resImprovements,
            feedbacksUsed: [...feedbacks],
            screenshotUsed: screenshotDataUrl,
          },
        ]);
        return;
      }

      const produced = stripFences(res.output || "");
      if (!looksLikeHtml(produced)) {
        const errMsg = res.error || `No usable HTML (${res.raw_chars ?? raw.length} raw chars).`;
        setParseError(errMsg);
        setShowRaw(true);
        setShowAdvanced(true);
        setError(errMsg);
        setImprovements(resImprovements);
        setIterations((prev) => [
          ...prev,
          {
            id: (prev[prev.length - 1]?.id ?? 0) + 1,
            satisfied: false,
            html: "",
            note: res.error || undefined,
            improvements: resImprovements,
            feedbacksUsed: [...feedbacks],
            screenshotUsed: screenshotDataUrl,
          },
        ]);
        return;
      }

      setProposedHtml(produced);
      setImprovements(resImprovements);
      setFeedbacks([]);
      setScreenshotDataUrl(null);
      setIterations((prev) => [
        ...prev,
        {
          id: (prev[prev.length - 1]?.id ?? 0) + 1,
          satisfied: false,
          html: produced,
          improvements: resImprovements,
          feedbacksUsed: [...feedbacks],
          screenshotUsed: screenshotDataUrl,
        },
      ]);
    } catch (e) {
      let msg = e instanceof Error ? e.message : "Refinement request failed";
      if (msg === "Not Found" || /not found/i.test(msg)) {
        msg =
          "Refinement endpoint not found — restart the server (start.ps1 or Restart server button), then try again.";
      }
      setError(msg);
    } finally {
      setRunning(false);
    }
  }

  function resetAll() {
    setWorkingHtml(seed);
    setBeforeHtml(seed);
    setProposedHtml(null);
    setIterations([]);
    setFeedbacks([]);
    setFreeNote("");
    setScreenshotDataUrl(null);
    setSatisfied(false);
    setSatisfiedNote(undefined);
    setImprovements([]);
    setCritiqueRan(false);
    setLastRaw(undefined);
    setParseError(undefined);
    setError(null);
    setAnnotateTarget(null);
  }

  function useProposedAsWorking() {
    if (!proposedHtml) return;
    setWorkingHtml(proposedHtml);
    setProposedHtml(null);
    setImprovements([]);
    setCritiqueRan(false);
  }

  async function acceptHtml(html: string) {
    const name = `${safeName(baseModel)}-refined-${Date.now().toString(36)}`;
    await onAccept(html, name);
    onSaved?.();
  }

  const hasProposal = Boolean(proposedHtml);
  const beforeDoc = wrapForIframe(beforeHtml);
  const proposedDoc = proposedHtml ? wrapForIframe(proposedHtml) : "";
  const proposedAnnotateDoc = proposedHtml ? injectAnnotate(proposedHtml) : "";
  const beforeAnnotateDoc = injectAnnotate(beforeHtml);

  function toggleAnnotate(target: "proposed" | "before") {
    setAnnotateTarget((cur) => (cur === target ? null : target));
  }

  return (
    <div className="refine-overlay">
      <div className="refine-topbar">
        <div className="refine-topbar-left">
          <span className="refine-title">Refine</span>
          <span className="refine-meta mono">
            {baseModel} · {stem}
          </span>
        </div>

        <div className="refine-topbar-controls">
          <label className="refine-inline-label">
            Backend
            <select value={backend} onChange={(e) => setBackend(e.target.value)} disabled={running}>
              {BACKENDS.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.label}
                </option>
              ))}
            </select>
          </label>
          <label className="refine-inline-label">
            Model
            {backend === "lmstudio" && lmModels.length > 0 ? (
              <select value={model} onChange={(e) => setModel(e.target.value)} disabled={running}>
                <option value="">Auto</option>
                {lmModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={targetModel || "default"}
                disabled={running}
              />
            )}
          </label>
          <label className="refine-zoom-label">
            Zoom
            <input
              type="range"
              min={0.5}
              max={1.5}
              step={0.05}
              value={zoom}
              onChange={(e) => setZoom(parseFloat(e.target.value))}
            />
            <span>{Math.round(zoom * 100)}%</span>
          </label>
        </div>

        <div className="refine-topbar-actions">
          <button className="btn small ghost" onClick={() => setShowAdvanced((v) => !v)} disabled={running}>
            {showAdvanced ? "Hide advanced" : "Advanced"}
          </button>
          <button className="btn small ghost" onClick={resetAll} disabled={running}>
            Reset
          </button>
          <button className="btn small" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      <p className="refine-flow-hint">
        Compare the three columns. If the proposal needs fixes, flag the problem area and add a note, then run critique
        again. Accept when it matches the original.
      </p>

      {error && <div className="refine-error">{error}</div>}

      {satisfied && (
        <div className="refine-banner refine-banner-ok">
          Model says this version is good enough.
          {satisfiedNote && <span className="refine-banner-note"> {satisfiedNote}</span>}
          <button className="btn small" style={{ marginLeft: 12 }} onClick={() => acceptHtml(beforeHtml)}>
            Save this version
          </button>
        </div>
      )}

      {annotateTarget && (
        <div className="refine-annotate-hint">
          Drag boxes on <strong>{annotateTarget}</strong> to flag problem areas — each drag adds a note (the VLM also
          gets numbered boxes on the render shot). Add comments below, then click the button again when done.
          {feedbacks.length > 0 && (
            <span className="refine-annotate-count">
              {" "}
              {feedbacks.length} flag{feedbacks.length === 1 ? "" : "s"} so far
            </span>
          )}
        </div>
      )}

      <div className="refine-compare">
        <CompareColumn label="Original" sublabel="source photo" variant="original">
          <div
            className="refine-preview-scroll"
            style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}
          >
            <img src={api.imageUrl(stem)} alt="Original document" className="refine-doc-image" />
          </div>
        </CompareColumn>

        <CompareColumn
          label="Before"
          sublabel={hasProposal ? "what you sent" : "starting HTML"}
          variant="before"
          annotating={annotateTarget === "before"}
        >
          <iframe
            key={annotateTarget === "before" ? "before-annotate" : "before-view"}
            className="refine-preview-frame"
            srcDoc={annotateTarget === "before" ? beforeAnnotateDoc : beforeDoc}
            sandbox={annotateTarget === "before" ? "allow-same-origin allow-scripts" : "allow-same-origin"}
            title="Before"
          />
        </CompareColumn>

        <CompareColumn
          label="Proposed"
          sublabel={hasProposal ? "model revision" : "appears after critique"}
          variant="proposed"
          annotating={annotateTarget === "proposed"}
        >
          {hasProposal ? (
            <iframe
              key={annotateTarget === "proposed" ? `prop-a-${proposedHtml!.length}` : `prop-${proposedHtml!.length}`}
              className="refine-preview-frame"
              srcDoc={annotateTarget === "proposed" ? proposedAnnotateDoc : proposedDoc}
              sandbox={annotateTarget === "proposed" ? "allow-same-origin allow-scripts" : "allow-same-origin"}
              title="Proposed"
            />
          ) : (
            <div className="refine-placeholder">
              <p>Run critique to fill this column with the model&apos;s revision.</p>
            </div>
          )}
        </CompareColumn>
      </div>

      <ChangelogSection
        items={improvements}
        satisfied={satisfied}
        critiqueRan={critiqueRan}
        onShowRaw={lastRaw ? () => { setShowAdvanced(true); setShowRaw(true); } : undefined}
      />

      <div className="refine-feedback-panel">
        <div className="refine-section-label">Corrections for next critique run</div>
        <p className="refine-feedback-hint">
          These notes are sent with the next run. Flagged areas auto-attach a render screenshot with numbered boxes for
          the VLM. Cleared after each successful critique.
        </p>
        <div className="refine-feedback-row">
          <input
            className="refine-input"
            placeholder="Describe what's still wrong (e.g. total should be 48.90)"
            value={freeNote}
            onChange={(e) => setFreeNote(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addFreeNote()}
            disabled={running}
          />
          <button className="btn small" onClick={addFreeNote} disabled={running || !freeNote.trim()}>
            Add note
          </button>
        </div>

        {feedbacks.length > 0 ? (
          <div className="refine-feedback-list">
            {feedbacks.map((fb, i) => (
              <div key={i} className="refine-fb-chip">
                <span className="refine-fb-num">{fb.bbox ? `#${i + 1}` : "•"}</span>
                {fb.excerpt && (
                  <span className="refine-fb-excerpt" title={fb.excerpt}>
                    “{fb.excerpt.slice(0, 72)}
                    {fb.excerpt.length > 72 ? "…" : ""}”
                  </span>
                )}
                <input
                  className="refine-fb-comment-input"
                  placeholder="What's wrong here?"
                  value={fb.comment || ""}
                  onChange={(e) => updateFeedbackComment(i, e.target.value)}
                  disabled={running}
                />
                <button type="button" className="refine-fb-x" onClick={() => removeFeedback(i)} aria-label="Remove">
                  ×
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="refine-feedback-empty">No notes yet — add one above or flag an area in Proposed.</p>
        )}
      </div>

      <div className="refine-actionbar">
        <div className="refine-actionbar-left">
          <button className="btn primary" onClick={runRefine} disabled={running || !workingHtml.trim()}>
            {running ? "Running…" : hasProposal ? "Re-run critique" : "Run critique"}
          </button>
          <button className="btn small" onClick={captureScreenshot} disabled={capturing || running}>
            {capturing ? "Capturing…" : screenshotDataUrl ? "Recapture shot" : "Attach render shot"}
          </button>
          {screenshotDataUrl && (
            <button className="btn small ghost" onClick={() => setScreenshotDataUrl(null)} disabled={running}>
              Clear shot
            </button>
          )}
          {hasProposal && (
            <button
              type="button"
              className={`btn small${annotateTarget === "proposed" ? " accent" : ""}`}
              onClick={() => toggleAnnotate("proposed")}
              disabled={running}
            >
              {annotateTarget === "proposed" ? "Done flagging" : "Flag areas in Proposed"}
            </button>
          )}
          <button
            type="button"
            className={`btn small ghost${annotateTarget === "before" ? " accent" : ""}`}
            onClick={() => toggleAnnotate("before")}
            disabled={running}
          >
            {annotateTarget === "before" ? "Done flagging" : "Flag areas in Before"}
          </button>
          {screenshotDataUrl && <span className="tag">screenshot attached</span>}
        </div>

        <div className="refine-actionbar-right">
          {hasProposal && (
            <>
              <button className="btn accent" onClick={() => acceptHtml(proposedHtml!)} disabled={running}>
                Accept proposed
              </button>
              <button className="btn" onClick={useProposedAsWorking} disabled={running} title="Copy proposed into the next critique input without saving">
                Iterate on proposed
              </button>
              <button className="btn ghost" onClick={() => { setProposedHtml(null); setImprovements([]); }} disabled={running}>
                Dismiss
              </button>
            </>
          )}
        </div>
      </div>

      {iterations.length > 0 && (
        <div className="refine-history-bar">
          <span className="dim">Past runs:</span>
          {iterations.map((it) => (
            <button
              key={it.id}
              type="button"
              className="refine-iter"
              onClick={() => {
                setImprovements(it.improvements ?? []);
                setCritiqueRan(true);
                if (it.html) {
                  setProposedHtml(it.html);
                  setSatisfied(false);
                  setSatisfiedNote(undefined);
                } else if (it.satisfied) {
                  setSatisfied(true);
                  setSatisfiedNote(it.note);
                  setProposedHtml(null);
                }
              }}
            >
              #{it.id} {it.satisfied ? "ok" : "proposed"}
            </button>
          ))}
        </div>
      )}

      {showAdvanced && (
        <div className="refine-advanced">
          <div className="refine-advanced-section">
            <div className="refine-section-label">HTML sent on next critique</div>
            <p className="refine-feedback-hint">
              Power-user edit only. Normally use &quot;Iterate on proposed&quot; instead of editing raw HTML.
            </p>
            <textarea
              className="refine-textarea"
              value={workingHtml}
              onChange={(e) => setWorkingHtml(e.target.value)}
              spellCheck={false}
              disabled={running}
            />
          </div>
          {lastRaw && (
            <div className="refine-advanced-section">
              <button type="button" className="btn small ghost" onClick={() => setShowRaw((v) => !v)}>
                {showRaw ? "Hide raw model response" : "Show raw model response"}
              </button>
              {parseError && <div className="refine-parse-error">{parseError}</div>}
              {showRaw && <textarea className="refine-raw-text" readOnly value={lastRaw} spellCheck={false} />}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

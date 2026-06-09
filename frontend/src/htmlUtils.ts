import DOMPurify from "dompurify";

/** Strip markdown code fences from model OCR output. */
export function stripFences(text: string): string {
  let t = text.trim();
  t = t.replace(/^```(?:html|HTML)?\s*\r?\n?/, "");
  t = t.replace(/\r?\n?```\s*$/, "");
  return t.trim();
}

/** Normalize stored HTML for equality checks (promote vs ground truth). */
export function normalizeForCompare(text: string): string {
  return stripFences(text).replace(/\r\n/g, "\n").trim();
}

/** Remove scripts and inline handlers; keep document structure and CSS. */
export function stripUnsafe(html: string): string {
  const withoutExecutableBlocks = html
    .replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, "")
    .replace(/<noscript\b[^>]*>[\s\S]*?<\/noscript\s*>/gi, "");

  return DOMPurify.sanitize(withoutExecutableBlocks, {
    // Browser DOMPurify drops <head>/<style> without this; GT docs render as plain text.
    WHOLE_DOCUMENT: true,
    ADD_TAGS: ["style"],
    ADD_ATTR: ["style", "class", "id"],
    FORBID_TAGS: ["script", "iframe", "object", "embed", "link"],
    FORBID_ATTR: ["srcdoc"],
  });
}

const FRAGMENT_CSS = `
  body { margin: 0; padding: 16px; background: #fff; color: #111; font-size: 14px; line-height: 1.6; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0; }
  td, th { border: 1px solid #ccc; padding: 6px 8px; text-align: left; vertical-align: top; }
  img { max-width: 100%; }
`;

/**
 * Prepare HTML for iframe srcDoc.
 * Full documents keep their <head> styles; fragments get a minimal shell.
 */
export function wrapForIframe(html: string): string {
  let doc = stripUnsafe(stripFences(html));

  if (/<html[\s>]/i.test(doc)) {
    if (!/<!DOCTYPE/i.test(doc)) {
      doc = `<!DOCTYPE html>\n${doc}`;
    }
    if (/<head[\s>]/i.test(doc) && !/<meta[^>]+charset/i.test(doc)) {
      doc = doc.replace(/<head([^>]*)>/i, '<head$1><meta charset="utf-8">');
    }
  } else {
    doc = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${FRAGMENT_CSS}</style></head><body>${doc}</body></html>`;
  }

  return doc;
}

/** @deprecated use wrapForIframe */
export function sanitizeForPreview(html: string): string {
  const wrapped = wrapForIframe(html);
  const bodyMatch = wrapped.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  return bodyMatch ? bodyMatch[1].trim() : wrapped;
}

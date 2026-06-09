/** Horizontal padding added around measured content inside the preview card. */
export const FRAME_PADDING = 40;

/** Content width at or above this fraction of the pane uses full-width card. */
export const FULL_BLEED_RATIO = 0.94;

export type ContentBBox = { width: number; height: number };

/**
 * Bounding box of rendered content, ignoring the full-width body shell.
 * Centers correctly for left-aligned and centered narrow blocks alike.
 */
export function measureDocumentContentBBox(doc: Document): ContentBBox {
  const body = doc.body;
  const root = doc.documentElement;
  if (!body) return { width: 0, height: 0 };

  let minLeft = Infinity;
  let maxRight = -Infinity;
  let minTop = Infinity;
  let maxBottom = -Infinity;

  const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
  let node = walker.currentNode as Element | null;
  while (node) {
    if (node === body || node === root) {
      node = walker.nextNode() as Element | null;
      continue;
    }

    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      node = walker.nextNode() as Element | null;
      continue;
    }

    minLeft = Math.min(minLeft, rect.left);
    maxRight = Math.max(maxRight, rect.right);
    minTop = Math.min(minTop, rect.top);
    maxBottom = Math.max(maxBottom, rect.bottom);
    node = walker.nextNode() as Element | null;
  }

  if (minLeft === Infinity) {
    return {
      width: Math.ceil(body.clientWidth),
      height: Math.ceil(body.clientHeight),
    };
  }

  return {
    width: Math.ceil(maxRight - minLeft),
    height: Math.ceil(maxBottom - minTop),
  };
}

export type AutoFrameLayout = {
  /** Pixel width for the iframe card, or null for full-width within the pane padding. */
  widthPx: number | null;
};

export function layoutForFrame(contentWidth: number, containerWidth: number): AutoFrameLayout {
  if (containerWidth <= 0 || contentWidth <= 0) {
    return { widthPx: null };
  }

  const padded = contentWidth + FRAME_PADDING;

  if (contentWidth >= containerWidth * FULL_BLEED_RATIO) {
    return { widthPx: null };
  }

  return {
    widthPx: Math.min(Math.max(padded, 160), containerWidth),
  };
}

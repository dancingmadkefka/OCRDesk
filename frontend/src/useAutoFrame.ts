import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import {
  type AutoFrameLayout,
  layoutForFrame,
  measureDocumentContentBBox,
} from "./iframeMeasure";

const DEFAULT_LAYOUT: AutoFrameLayout = { widthPx: null };

export function useAutoFrame(
  iframeDoc: string,
  enabled: boolean,
  containerRef: RefObject<HTMLElement | null>
) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [layout, setLayout] = useState<AutoFrameLayout>(DEFAULT_LAYOUT);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const update = () => setContainerWidth(el.clientWidth);
    update();

    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [containerRef, enabled]);

  const measure = useCallback(() => {
    if (!enabled) {
      setLayout(DEFAULT_LAYOUT);
      return;
    }

    const doc = iframeRef.current?.contentDocument;
    if (!doc?.body) return;

    const bbox = measureDocumentContentBBox(doc);
    const wrapWidth = containerRef.current?.clientWidth ?? containerWidth;
    setLayout(layoutForFrame(bbox.width, wrapWidth));
  }, [enabled, containerRef, containerWidth]);

  useEffect(() => {
    setLayout(DEFAULT_LAYOUT);
  }, [iframeDoc, enabled]);

  const onIframeLoad = useCallback(() => {
    requestAnimationFrame(() => {
      measure();
      requestAnimationFrame(() => measure());
    });
  }, [measure]);

  return { iframeRef, layout, onIframeLoad };
}

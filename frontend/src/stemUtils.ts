/** Human-readable label from a document stem (e.g. aldi-receipt__ef9dc9d97e97). */
export function displayStem(stem: string): { title: string; hash: string } {
  const idx = stem.lastIndexOf("__");
  const raw = idx > 0 ? stem.slice(0, idx) : stem;
  const hash = idx > 0 ? stem.slice(idx + 2) : "";
  const title = raw.replace(/_/g, " ").replace(/-/g, " ");
  return { title, hash };
}

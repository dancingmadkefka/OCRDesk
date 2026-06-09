export interface ImageRow {
  stem: string;
  has_gt: boolean;
  gt_file: string | null;
  models: string[];
  image_file: string;
}

export interface Summary {
  total: number;
  gt_count: number;
  remaining_gt: number;
  model_coverage: Record<string, number>;
  images: ImageRow[];
}

export interface Settings {
  images_dir: string;
  lm_studio_url: string;
  lm_studio_key_set: boolean;
  openai_api_key_set: boolean;
  anthropic_api_key_set: boolean;
  openai_api_key_preview: string;
  anthropic_api_key_preview: string;
  default_openai_model: string;
  default_anthropic_model: string;
  ocr_delay_seconds: number;
}

export interface ImageDetail {
  stem: string;
  image_file: string;
  has_gt: boolean;
  ground_truth: { file: string; raw: string; html: string } | null;
  models: string[];
  model_contents: Record<string, string>;
  prev_stem: string | null;
  next_stem: string | null;
  index: number;
  total: number;
}

export interface OcrJob {
  id: string;
  status: string;
  total: number;
  done: number;
  current: string | null;
  results: Array<{
    stem: string;
    status: string;
    model?: string;
    error?: string;
    reason?: string;
  }>;
  backend: string;
  model: string | null;
}

export interface FeedbackBBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FeedbackItem {
  excerpt?: string | null;
  comment?: string | null;
  /** Normalized 0–1 rect on the rendered HTML viewport (for VLM screenshot overlays). */
  bbox?: FeedbackBBox | null;
}

export interface RefineImprovement {
  area: string;
  change: string;
  reason: string;
}

export interface RefineResponse {
  model: string;
  output: string;
  satisfied: boolean;
  raw: string;
  note?: string | null;
  improvements?: RefineImprovement[];
  error?: string | null;
  raw_chars?: number;
  output_chars?: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  restartServer: () => request<{ status: string }>("/api/restart", { method: "POST" }),
  getSummary: () => request<Summary>("/api/summary"),
  getImage: (stem: string) => request<ImageDetail>(`/api/images/${encodeURIComponent(stem)}`),
  getSettings: () => request<Settings>("/api/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  getLmModels: () => request<{ models: string[] }>("/api/lmstudio/models"),
  createOcrJob: (body: {
    backend: string;
    model?: string;
    stems: string[];
    overwrite?: boolean;
  }) => request<{ job_id: string }>("/api/ocr/jobs", { method: "POST", body: JSON.stringify(body) }),
  getOcrJob: (id: string) => request<OcrJob>(`/api/ocr/jobs/${id}`),
  saveManual: (stem: string, html: string) =>
    request<{ model: string }>("/api/ocr/manual", {
      method: "POST",
      body: JSON.stringify({ stem, html }),
    }),
  promote: (stem: string, model: string, html?: string) =>
    request<{ ground_truth: string }>("/api/promote", {
      method: "POST",
      body: JSON.stringify({ stem, model, html }),
    }),
  saveResult: (stem: string, model: string, html: string) =>
    request<{ model: string }>(`/api/results/${encodeURIComponent(stem)}/${encodeURIComponent(model)}`, {
      method: "PUT",
      body: JSON.stringify({ html }),
    }),
  saveGroundTruth: (stem: string, html: string) =>
    request<{ ground_truth: string }>(`/api/ground-truth/${encodeURIComponent(stem)}`, {
      method: "PUT",
      body: JSON.stringify({ html }),
    }),
  imageUrl: (stem: string) => `/api/files/image/${encodeURIComponent(stem)}`,
  refine: (body: {
    stem: string;
    backend: string;
    model?: string;
    prev_html: string;
    screenshot_b64?: string | null;
    feedbacks?: FeedbackItem[] | null;
  }) => request<RefineResponse>("/api/refine", { method: "POST", body: JSON.stringify(body) }),
};

import { api, type OcrJob } from "./api";

type OcrJobRequest = {
  backend: string;
  model?: string;
  stems: string[];
  overwrite?: boolean;
};

export type TrackedOcrJob = OcrJob & {
  requestedStems: string[];
};

type Listener = () => void;

const POLL_MS = 800;

let jobs: TrackedOcrJob[] = [];
const listeners = new Set<Listener>();
let pollTimer: number | null = null;

function notify() {
  listeners.forEach((listener) => listener());
}

function upsert(job: TrackedOcrJob) {
  const idx = jobs.findIndex((j) => j.id === job.id);
  if (idx >= 0) {
    jobs = jobs.map((j) => (j.id === job.id ? job : j));
  } else {
    jobs = [job, ...jobs].slice(0, 12);
  }
  notify();
}

function hasActiveJobs() {
  return jobs.some((job) => job.status !== "completed");
}

async function pollActiveJobs() {
  pollTimer = null;
  const active = jobs.filter((job) => job.status !== "completed");
  if (!active.length) return;

  const updates = await Promise.allSettled(active.map((job) => api.getOcrJob(job.id)));
  let changed = false;
  updates.forEach((result, i) => {
    if (result.status !== "fulfilled") return;
    const prev = active[i];
    const next: TrackedOcrJob = { ...result.value, requestedStems: prev.requestedStems };
    jobs = jobs.map((job) => (job.id === next.id ? next : job));
    changed = true;
  });
  if (changed) notify();
  schedulePolling();
}

function schedulePolling() {
  if (pollTimer !== null || !hasActiveJobs()) return;
  pollTimer = window.setTimeout(() => {
    void pollActiveJobs();
  }, POLL_MS);
}

export function getOcrJobSnapshot(): TrackedOcrJob[] {
  return jobs;
}

export function subscribeOcrJobs(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export async function startTrackedOcrJob(req: OcrJobRequest): Promise<TrackedOcrJob> {
  const { job_id } = await api.createOcrJob(req);
  const job = await api.getOcrJob(job_id);
  const tracked = { ...job, requestedStems: req.stems };
  upsert(tracked);
  schedulePolling();
  return tracked;
}


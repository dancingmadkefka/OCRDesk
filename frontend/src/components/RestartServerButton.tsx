import { useState } from "react";
import { api } from "../api";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForServer(maxAttempts = 60) {
  for (let i = 0; i < maxAttempts; i++) {
    await sleep(500);
    try {
      const res = await fetch("/api/health", { cache: "no-store" });
      if (res.ok) return;
    } catch {
      /* server still down */
    }
  }
  throw new Error("Server did not come back within 30 seconds");
}

export default function RestartServerButton() {
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRestart() {
    if (restarting) return;
    if (!confirm("Restart the server? The page will reload when it is back.")) return;

    setRestarting(true);
    setError(null);
    try {
      try {
        await api.restartServer();
      } catch {
        /* connection may drop before the response completes */
      }
      await waitForServer();
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Restart failed");
      setRestarting(false);
    }
  }

  return (
    <div className="restart-server-wrap">
      <button
        type="button"
        className="btn small ghost"
        onClick={() => void handleRestart()}
        disabled={restarting}
        title="Stop and start the API server (reloads frontend assets)"
      >
        {restarting ? "Restarting…" : "Restart server"}
      </button>
      {error && <span className="restart-server-error dim">{error}</span>}
    </div>
  );
}

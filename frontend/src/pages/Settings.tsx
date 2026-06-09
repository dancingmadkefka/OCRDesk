import { useEffect, useState } from "react";
import { api, type Settings as SettingsType } from "../api";

export default function Settings() {
  const [settings, setSettings] = useState<SettingsType | null>(null);
  const [form, setForm] = useState({
    images_dir: "",
    lm_studio_url: "",
    lm_studio_key: "",
    openai_api_key: "",
    anthropic_api_key: "",
    default_openai_model: "",
    default_anthropic_model: "",
    ocr_delay_seconds: 2,
  });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSettings().then((s) => {
      setSettings(s);
      setForm((f) => ({
        ...f,
        images_dir: s.images_dir,
        lm_studio_url: s.lm_studio_url,
        default_openai_model: s.default_openai_model,
        default_anthropic_model: s.default_anthropic_model,
        ocr_delay_seconds: s.ocr_delay_seconds,
      }));
    });
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const body: Record<string, unknown> = {
        images_dir: form.images_dir,
        lm_studio_url: form.lm_studio_url,
        default_openai_model: form.default_openai_model,
        default_anthropic_model: form.default_anthropic_model,
        ocr_delay_seconds: form.ocr_delay_seconds,
      };
      if (form.lm_studio_key) body.lm_studio_key = form.lm_studio_key;
      if (form.openai_api_key) body.openai_api_key = form.openai_api_key;
      if (form.anthropic_api_key) body.anthropic_api_key = form.anthropic_api_key;

      const updated = await api.updateSettings(body);
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    }
  }

  if (!settings) return <div className="loading">Loading…</div>;

  return (
    <div className="page settings-page">
      <div className="page-header">
        <h1>Settings</h1>
        <p className="subtitle">Configure image folder, API keys, and defaults</p>
      </div>

      <form className="settings-form panel" onSubmit={handleSave}>
        <fieldset>
          <legend>Image folder</legend>
          <label>
            Path to folder containing <code>*.ocr_ready.jpg</code> files
            <input
              type="text"
              value={form.images_dir}
              onChange={(e) => setForm({ ...form, images_dir: e.target.value })}
              className="wide"
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>LM Studio</legend>
          <label>
            API URL
            <input
              type="text"
              value={form.lm_studio_url}
              onChange={(e) => setForm({ ...form, lm_studio_url: e.target.value })}
            />
          </label>
          <label>
            API key {settings.lm_studio_key_set && <span className="dim">(configured)</span>}
            <input
              type="password"
              placeholder="Leave blank to keep current"
              value={form.lm_studio_key}
              onChange={(e) => setForm({ ...form, lm_studio_key: e.target.value })}
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>OpenAI</legend>
          <label>
            API key{" "}
            {settings.openai_api_key_set && (
              <span className="dim">({settings.openai_api_key_preview})</span>
            )}
            <input
              type="password"
              placeholder="Leave blank to keep current"
              value={form.openai_api_key}
              onChange={(e) => setForm({ ...form, openai_api_key: e.target.value })}
            />
          </label>
          <label>
            Default model
            <input
              type="text"
              value={form.default_openai_model}
              onChange={(e) => setForm({ ...form, default_openai_model: e.target.value })}
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Anthropic</legend>
          <label>
            API key{" "}
            {settings.anthropic_api_key_set && (
              <span className="dim">({settings.anthropic_api_key_preview})</span>
            )}
            <input
              type="password"
              placeholder="Leave blank to keep current"
              value={form.anthropic_api_key}
              onChange={(e) => setForm({ ...form, anthropic_api_key: e.target.value })}
            />
          </label>
          <label>
            Default model
            <input
              type="text"
              value={form.default_anthropic_model}
              onChange={(e) => setForm({ ...form, default_anthropic_model: e.target.value })}
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Batch OCR</legend>
          <label>
            Delay between API calls (seconds)
            <input
              type="number"
              min={0}
              step={0.5}
              value={form.ocr_delay_seconds}
              onChange={(e) => setForm({ ...form, ocr_delay_seconds: parseFloat(e.target.value) })}
            />
          </label>
        </fieldset>

        {error && <div className="error-banner">{error}</div>}

        <div className="form-actions">
          <button type="submit" className="btn primary">
            Save settings
          </button>
          {saved && <span className="saved-msg">Saved</span>}
        </div>
      </form>
    </div>
  );
}

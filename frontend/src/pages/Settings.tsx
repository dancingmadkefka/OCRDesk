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

  if (!settings) return <div className="loading">Loading settings…</div>;

  const keyBadge = (isSet: boolean) => (
    <span className={`key-badge ${isSet ? "set" : "unset"}`}>{isSet ? "Configured" : "Not set"}</span>
  );

  // Collapse the long run of bullets from the masked preview (e.g. "sk-1••••••••3xyz").
  const shortPreview = (p: string) => p.replace(/•{2,}/g, "••••");

  return (
    <div className="page settings-page">
      <div className="page-header">
        <p className="dash-eyebrow">Configuration</p>
        <h1>Settings</h1>
        <p className="subtitle">Image folder, API keys, and batch defaults.</p>
      </div>

      <form className="settings-form panel" onSubmit={handleSave}>
        <fieldset>
          <legend>Image folder</legend>
          <p className="fieldset-desc">
            Folder containing your <code>*.ocr_ready.jpg</code> source images.
          </p>
          <label>
            Folder path
            <input
              type="text"
              value={form.images_dir}
              onChange={(e) => setForm({ ...form, images_dir: e.target.value })}
              className="wide"
              placeholder="C:\\path\\to\\images"
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>
            LM Studio {keyBadge(settings.lm_studio_key_set)}
          </legend>
          <p className="fieldset-desc">Local model server for running OCR without cloud APIs.</p>
          <label>
            API URL
            <input
              type="text"
              value={form.lm_studio_url}
              onChange={(e) => setForm({ ...form, lm_studio_url: e.target.value })}
              placeholder="http://localhost:1234/v1"
            />
          </label>
          <label>
            API key
            <input
              type="password"
              placeholder={settings.lm_studio_key_set ? "•••••• — leave blank to keep current" : "Optional"}
              value={form.lm_studio_key}
              onChange={(e) => setForm({ ...form, lm_studio_key: e.target.value })}
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>
            OpenAI {keyBadge(settings.openai_api_key_set)}
          </legend>
          <label>
            API key{" "}
            {settings.openai_api_key_set && settings.openai_api_key_preview && (
              <span className="dim mono">· current {shortPreview(settings.openai_api_key_preview)}</span>
            )}
            <input
              type="password"
              placeholder={settings.openai_api_key_set ? "Leave blank to keep current" : "sk-…"}
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
              placeholder="gpt-4o"
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>
            Anthropic {keyBadge(settings.anthropic_api_key_set)}
          </legend>
          <label>
            API key{" "}
            {settings.anthropic_api_key_set && settings.anthropic_api_key_preview && (
              <span className="dim mono">· current {shortPreview(settings.anthropic_api_key_preview)}</span>
            )}
            <input
              type="password"
              placeholder={settings.anthropic_api_key_set ? "Leave blank to keep current" : "sk-ant-…"}
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
              placeholder="claude-sonnet-4-20250514"
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Batch OCR</legend>
          <p className="fieldset-desc">Pause between API calls when running OCR on multiple documents.</p>
          <label>
            Delay between calls (seconds)
            <input
              type="number"
              min={0}
              step={0.5}
              value={form.ocr_delay_seconds}
              onChange={(e) => setForm({ ...form, ocr_delay_seconds: parseFloat(e.target.value) })}
              style={{ maxWidth: 160 }}
            />
          </label>
        </fieldset>

        {error && <div className="error-banner">{error}</div>}

        <div className="form-actions">
          <button type="submit" className="btn primary">
            Save settings
          </button>
          {saved && <span className="saved-msg">✓ Saved</span>}
        </div>
      </form>
    </div>
  );
}

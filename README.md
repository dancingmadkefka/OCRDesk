# OCR Benchmark

Browser-based tool for benchmarking OCR quality of vision-language models against real-world document images.

## Setup

```powershell
# Python dependencies
pip install -r requirements.txt

# Frontend
cd frontend
npm install
npm run build
cd ..
```

## Run

```powershell
python run.py
```

Open http://127.0.0.1:8877 in your browser.

### Development (hot reload frontend)

```powershell
# Terminal 1 — API server
python run.py

# Terminal 2 — Vite dev server (proxies /api to :8877)
cd frontend
npm run dev
```

Open http://localhost:5173 during development.

## Configuration

Default image folder: `C:\Users\daniel\OneDrive\Pictures\benchmark\final`

Change via **Settings** in the app, or edit `settings.json`. API keys can also be set via environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LM_STUDIO_URL`, `LM_STUDIO_KEY`).

## Results storage

Model outputs are saved to `<image-folder>/results/<stem>/<model-name>.html`.

## Iterative refinement (self-critique loop)

In the workspace, after you have at least one model result (or ground truth), click **"Refine / self-critique…"**.

- The panel lets you send the **original image + the current HTML + an optional screenshot of the rendered HTML** back to any supported vision model.
- The model is asked to review its own work and either reply `[[SATISFIED]]` (with optional note) or output a corrected full HTML.
- You can add free-form notes or **select text/areas directly in the live preview** (click "Select text in preview") and attach a comment such as "price column is mis-aligned" or "total should be 48.90 per the photo".
- Each iteration is recorded in a local history; you can jump back to any previous base.
- When satisfied with an improved version, "Accept & save as new variant" writes it to disk (e.g. `gpt-4o-refined-xxx.html`) so the old attempt is preserved and the new one appears in the compare dropdowns for side-by-side review or further passes.

You can repeat the loop as many times as you want, using the same or a different backend/model for the critique pass.

## Documentation

Design reasoning (prompt rules, ground truth vs presentation, compare UI): **[docs/DECISIONS.md](docs/DECISIONS.md)**

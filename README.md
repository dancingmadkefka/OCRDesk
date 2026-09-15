# OCRDesk

OCRDesk is a local web app for running OCR/VLM reads on document images, inspecting the rendered output, and iterating until you have trustworthy ground truth.

It exists because OCR evaluation is hard to do from raw text dumps. Real documents have tables, columns, handwritten marks, typography, and layout quirks. OCRDesk keeps the source image, model candidates, critique notes, refined outputs, and human-reviewed ground truth in one workspace.

## What it does

- Tracks a folder of source images named `*.ocr_ready.jpg`.
- Runs OCR with LM Studio, OpenAI, or Anthropic vision models.
- Compares the source photo, model candidates, and ground truth side by side.
- Lets you mark a candidate as ground truth or edit the HTML directly.
- Supports an iterative critique workflow with user notes, flagged render areas, raw model responses, and saved refined variants.
- Stores outputs locally next to your image corpus.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd frontend
npm install
npm run build
cd ..
```

## Run

```powershell
python run.py
```

Open `http://127.0.0.1:8877`.

## Development

Run the API server:

```powershell
python run.py
```

Run the Vite frontend in another terminal:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:5173`. The dev server proxies API requests to `:8877`.

## First Setup

Put source images in the local `images/` folder or choose another folder in Settings. You can also set:

```powershell
$env:OCRDESK_IMAGES_DIR = "C:\path\to\images"
```

Each source image should be named `*.ocr_ready.jpg`.

API keys can be entered in Settings or provided through environment variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `LM_STUDIO_URL` (defaults to `http://localhost:1234/v1`)
- `LM_STUDIO_KEY` (defaults to `lm-studio`)

## Stored Files

OCRDesk writes results into your image folder:

- Model candidates: `<image-folder>/results/<stem>/<model>.html`
- Ground truth: `<image-folder>/<stem>_ground_truth.html`
- Local settings: `settings.json`

`settings.json`, logs, build output, dependencies, and the default `images/` folder are ignored by Git.

## Notes

The app stores OCR output as HTML because document structure matters. Tables, columns, typography, and handwritten sections are easier to inspect and grade when they are represented structurally instead of flattened into plain text.

Design reasoning and implementation notes live in [docs/DECISIONS.md](docs/DECISIONS.md).

## Grader

`ocrgrade` is a deterministic, structure-aware grader for image-to-HTML OCR/VLM
output: table structure, financial-token exact match, and critical-field
correctness assertions, not a text diff. It scores OCRDesk's own output, AI
Financial Advisor's `html_vlm_*.json` benchmark results, or a plain directory
of hypothesis files.

```powershell
.\.venv\Scripts\python.exe -m ocrgrade score --ocrdesk-images-dir images --model my-model --out-dir out
```

See [docs/grader.md](docs/grader.md) for the full workflow (annotation,
sidecars, output files) and [docs/grader-plan.md](docs/grader-plan.md) for the
scoring design while it is under active implementation.

## License

MIT. See [LICENSE](LICENSE).

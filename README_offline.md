# Local AI Studio — Fully Offline, No Cloud API

No OpenAI, no API key, no internet connection required. Every response comes
from a model running on your own Windows PC through **Ollama**.

## New: Scanned PDF support
Regular PDFs have selectable text baked in, so `pypdf` can just read it out.
**Scanned PDFs are actually pictures of pages** — there's no text to read, so
the old version returned nothing for them. Now:

- Every PDF is checked automatically: if it has almost no extractable text
  (e.g. under ~20 characters per page on average), the app assumes it's
  scanned and switches to OCR automatically — no action needed from you.
- Each page is rendered as an image (via **PyMuPDF**) and OCR'd with the same
  Tesseract engine used for the Image tab — fully offline, page by page,
  with live progress in the status bar ("OCR'ing scanned PDF — page 3 of 12...").
- There's also a **"Force OCR"** checkbox next to the Ingest PDF button, for
  cases where a PDF has a *little* embedded text (so auto-detection doesn't
  trigger) but is still mostly a scan — check it to force OCR on the whole file.
- Large scanned PDFs can take a while (OCR is CPU-bound); the button disables
  itself and the status bar shows progress so it's clear the app hasn't frozen.

## What changed from the cloud version
- Removed OpenAI/cloud code entirely — nothing in the app talks to the internet.
- The old "local vs. cloud" debate is now a **local vs. local** debate: pick
  any two models you've pulled with Ollama (they can even be the same model)
  and watch them talk to each other.
- Added a live **model dropdown** that reads the models actually installed
  on your machine (via `ollama list`), instead of a hardcoded name, so you
  never type a model name that doesn't exist on your system.
- Settings tab now has a **Refresh Model List** button and tells you plainly
  if Ollama isn't running or no model has been pulled yet.

## 1. Install Ollama (one-time)
Download and install from https://ollama.com — this is the only "server"
piece, and it runs locally on your PC, not in the cloud.

Then pull at least one model:
```
ollama run gemma2
```
(You can pull more, e.g. `ollama run llama3.1`, to use in the debate tab.)

## 2. Install Python dependencies
```bash
pip install -r requirements_offline.txt
```

`requirements_offline.txt` now includes `pymupdf`, which is used to render
scanned PDF pages as images before OCR — it's a pure pip package, no extra
system installer needed for that part.

For image OCR (and scanned-PDF OCR), also install the Tesseract engine
itself (separate from the Python wrapper):
- Windows: https://github.com/UB-Mannheim/tesseract/wiki
- macOS: `brew install tesseract`
- Linux: `sudo apt install tesseract-ocr`

## 3. Run the app
```bash
python local_ai_studio_offline.py
```

Open the **Settings** tab first and click **Refresh Model List** to confirm
the app can see your installed models.

## 4. Build a small standalone .exe (optional)
```bash
pyinstaller --onefile --windowed --clean local_ai_studio_offline.py
```
Plain Tkinter keeps the resulting `.exe` in `dist/` around 15–25MB.

## Nothing here calls the cloud
- Chat → Ollama (local)
- PDF text extraction → pypdf (local)
- Image OCR → Tesseract (local)
- Model list → `ollama list` (local)

If a feature ever fails, the popup will tell you it's because Ollama isn't
running or a model isn't pulled — never because of a missing API key,
since there isn't one anymore.

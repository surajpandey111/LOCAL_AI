"""
Local-Only AI Studio (No Cloud / No API Key Required)
------------------------------------------------------
100% offline. Every AI response comes from models running on YOUR machine
through Ollama. There is no OpenAI/cloud dependency anywhere in this app.

Features:
  1. Direct Chat  - talk to any local model you've pulled with Ollama
  2. Dual-Model Debate - two local models (e.g. gemma2 vs llama3.1) talk to
     each other automatically, entirely offline
  3. PDF & Image Studio - extract text from a PDF or OCR an image (both run
     locally) and ask a local model questions about it

Requirements on this Windows machine:
  - Ollama installed and running (https://ollama.com)
  - At least one model pulled, e.g.:  ollama run gemma2
  - Python packages: ollama, pypdf, pillow, pytesseract  (see requirements.txt)
  - For OCR: the Tesseract OCR engine installed and on PATH
             (https://github.com/UB-Mannheim/tesseract/wiki)

Nothing in this file calls the internet. If Ollama isn't running, or a model
isn't pulled, the app tells you exactly what to do instead of failing silently.
"""

import os
import json
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, scrolledtext

# ---------------------------------------------------------------------------
# Optional dependencies - app must still open even if some are missing.
# ---------------------------------------------------------------------------
try:
    import ollama
except ImportError:
    ollama = None

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None

try:
    import fitz  # PyMuPDF - used to render scanned PDF pages as images for OCR
except ImportError:
    fitz = None

import io

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".local_ai_studio_config.json")

# If normal text extraction yields fewer than this many characters per page
# on average, we assume the PDF is scanned/image-based and OCR it instead.
MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK = 20


# ===========================================================================
# Lightweight Markdown renderer for tk.Text widgets.
# Tkinter has no built-in markdown support, so model output (which often
# includes **bold**, `inline code`, ```fenced code blocks```, headers, and
# bullet lists) would otherwise show up as raw text with stray asterisks and
# backticks. This renders those into real formatting, and every fenced code
# block gets its own "Copy" button so code is easy to grab on its own.
# ===========================================================================

_MD_CODE_FENCE_RE = re.compile(r"```(\w*)\n?(.*?)```", re.DOTALL)
_MD_INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def configure_markdown_tags(text_widget):
    """Call once per Text/ScrolledText widget before rendering markdown into it."""
    text_widget.tag_configure("md_sender", font=("Segoe UI", 10, "bold"), foreground="#2b5797",
                               spacing1=6)
    text_widget.tag_configure("md_bold", font=("Segoe UI", 10, "bold"))
    text_widget.tag_configure("md_header", font=("Segoe UI", 12, "bold"), spacing1=8, spacing3=4)
    text_widget.tag_configure("md_inline_code", font=("Consolas", 10), background="#eeeeee",
                               foreground="#c7254e")
    text_widget.tag_configure("md_code_block", font=("Consolas", 10), background="#272822",
                               foreground="#f8f8f2", lmargin1=14, lmargin2=14,
                               spacing1=4, spacing3=6, wrap=tk.NONE)
    text_widget.tag_configure("md_code_lang", font=("Consolas", 8, "italic"), foreground="#888888")
    text_widget.tag_configure("md_bullet", lmargin1=20, lmargin2=34)


def add_copy_context_menu(text_widget):
    """Right-click menu with Copy Selected / Copy All, since these widgets hold
    model output people will want to reuse elsewhere."""
    menu = tk.Menu(text_widget, tearoff=0)

    def copy_selection():
        try:
            text_widget.clipboard_clear()
            text_widget.clipboard_append(text_widget.get(tk.SEL_FIRST, tk.SEL_LAST))
        except tk.TclError:
            pass

    def copy_all():
        text_widget.clipboard_clear()
        text_widget.clipboard_append(text_widget.get("1.0", tk.END))

    menu.add_command(label="Copy Selected", command=copy_selection)
    menu.add_command(label="Copy All", command=copy_all)

    def show_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    text_widget.bind("<Button-3>", show_menu)


def _insert_code_block(text_widget, code, lang):
    def copy_code():
        text_widget.clipboard_clear()
        text_widget.clipboard_append(code)

    text_widget.insert(tk.END, f"{lang or 'code'}  ", "md_code_lang")
    copy_btn = tk.Button(text_widget, text="📋 Copy", font=("Segoe UI", 8), relief=tk.FLAT,
                          bg="#44475a", fg="white", activebackground="#5a5d70",
                          activeforeground="white", padx=6, pady=0, bd=0, command=copy_code,
                          cursor="hand2")
    text_widget.window_create(tk.END, window=copy_btn)
    text_widget.insert(tk.END, "\n")
    text_widget.insert(tk.END, code + "\n", "md_code_block")


def _render_inline(text_widget, line, extra_tags=()):
    parts = _MD_INLINE_RE.split(line)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            text_widget.insert(tk.END, part[2:-2], ("md_bold",) + extra_tags)
        elif part.startswith("`") and part.endswith("`") and len(part) >= 2:
            text_widget.insert(tk.END, part[1:-1], ("md_inline_code",) + extra_tags)
        else:
            text_widget.insert(tk.END, part, extra_tags)


def _render_line(text_widget, line):
    header_match = re.match(r"^(#{1,6})\s+(.*)", line)
    if header_match:
        text_widget.insert(tk.END, header_match.group(2), "md_header")
        text_widget.insert(tk.END, "\n")
        return

    bullet_match = re.match(r"^\s*[\-\*]\s+(.*)", line)
    numbered_match = re.match(r"^\s*(\d+)\.\s+(.*)", line)

    if bullet_match:
        text_widget.insert(tk.END, "•  ", ("md_bullet",))
        _render_inline(text_widget, bullet_match.group(1), ("md_bullet",))
    elif numbered_match:
        text_widget.insert(tk.END, f"{numbered_match.group(1)}.  ", ("md_bullet",))
        _render_inline(text_widget, numbered_match.group(2), ("md_bullet",))
    else:
        _render_inline(text_widget, line)
    text_widget.insert(tk.END, "\n")


def _render_plain_markdown(text_widget, text):
    for line in text.split("\n"):
        if line.strip() == "":
            text_widget.insert(tk.END, "\n")
        else:
            _render_line(text_widget, line)


def render_markdown_body(text_widget, md_text):
    """Insert markdown text into text_widget at the current end position,
    expanding fenced code blocks (with a Copy button), bold, inline code,
    headers, and bullet/numbered lists."""
    pos = 0
    for m in _MD_CODE_FENCE_RE.finditer(md_text):
        before = md_text[pos:m.start()]
        if before:
            _render_plain_markdown(text_widget, before)
        lang = m.group(1)
        code = m.group(2).rstrip("\n")
        _insert_code_block(text_widget, code, lang)
        pos = m.end()
    remainder = md_text[pos:]
    if remainder:
        _render_plain_markdown(text_widget, remainder)


def insert_markdown_message(text_widget, sender, md_text):
    """Insert a full chat message: a bold sender label, then the rendered
    markdown body, then a blank line separator. Caller is responsible for
    toggling widget state (NORMAL/DISABLED) around this call if needed."""
    text_widget.insert(tk.END, f"{sender}\n", "md_sender")
    render_markdown_body(text_widget, md_text)
    text_widget.insert(tk.END, "\n")
    text_widget.see(tk.END)


class LocalAIStudio:
    def __init__(self, root):
        self.root = root
        self.root.title("Local AI Studio (Offline - No Cloud API)")
        self.root.geometry("950x720")
        self.root.minsize(780, 560)

        self.extracted_text_context = ""
        self.direct_chat_history = []
        self.available_models = []

        self.setup_ui()
        self.load_settings()
        self.refresh_model_list(startup=True)

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tab_config = ttk.Frame(self.notebook)
        self.tab_chat = ttk.Frame(self.notebook)
        self.tab_agents = ttk.Frame(self.notebook)
        self.tab_docs = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_config, text=" ⚙️ Settings ")
        self.notebook.add(self.tab_chat, text=" 💬 Direct Chat ")
        self.notebook.add(self.tab_agents, text=" 🤝 Dual-Model Debate ")
        self.notebook.add(self.tab_docs, text=" 📄 PDF & Image Studio ")

        self.build_config_tab()
        self.build_direct_chat_tab()
        self.build_agent_tab()
        self.build_docs_tab()

        self.status_var = tk.StringVar(value="Ready.")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                                relief=tk.SUNKEN, padding=(6, 2))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ---- Settings tab ----------------------------------------------------
    def build_config_tab(self):
        frame = ttk.LabelFrame(self.tab_config, text=" Local Engine (Ollama) ", padding=15)
        frame.pack(fill=tk.X, padx=15, pady=15)

        ttk.Label(frame, text="Installed models on this PC:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.lbl_models = ttk.Label(frame, text="(checking...)", foreground="#555555")
        self.lbl_models.grid(row=0, column=1, sticky=tk.W, pady=5)

        ttk.Button(frame, text="Refresh Model List", command=self.refresh_model_list).grid(
            row=1, column=1, sticky=tk.W, pady=(5, 0))

        diag_frame = ttk.LabelFrame(self.tab_config, text=" System Dependency Check ", padding=10)
        diag_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        deps = [
            ("ollama (Python package)", ollama, "pip install ollama"),
            ("pypdf (text PDF parsing)", pypdf, "pip install pypdf"),
            ("pillow + pytesseract (OCR)", pytesseract, "pip install pillow pytesseract"),
            ("PyMuPDF (scanned PDF rendering)", fitz, "pip install pymupdf"),
        ]
        for idx, (name, obj, hint) in enumerate(deps):
            status = "✅ Installed" if obj is not None else f"❌ Missing — {hint}"
            ttk.Label(diag_frame, text=f"{name}:").grid(row=idx, column=0, sticky=tk.W, pady=4)
            ttk.Label(diag_frame, text=status, font=("Arial", 9, "bold")).grid(
                row=idx, column=1, sticky=tk.W, padx=20, pady=4)

        ttk.Label(
            self.tab_config,
            text=("This app is fully offline. All chat runs through Ollama on this machine — "
                  "no API key, no internet connection, no cloud account needed. "
                  "If the model list above is empty, open a terminal and run e.g. "
                  "'ollama run gemma2' once to download a model, then click Refresh. "
                  "Scanned PDFs (photos/scans of pages, no selectable text) are auto-detected "
                  "and OCR'd page-by-page using PyMuPDF + Tesseract — both run locally, no "
                  "internet needed. Image OCR and scanned-PDF OCR both need the Tesseract "
                  "engine installed on your system, separate from the Python packages."),
            wraplength=850, justify=tk.LEFT, foreground="#555555"
        ).pack(fill=tk.X, padx=20, pady=(0, 15))

    def refresh_model_list(self, startup=False):
        if ollama is None:
            self.available_models = []
            self.lbl_models.config(text="ollama package not installed")
            if not startup:
                messagebox.showerror("Missing Library", "The 'ollama' package is not installed.\n"
                                                           "Run: pip install ollama")
            return
        try:
            result = ollama.list()
            models = result.get("models", []) if isinstance(result, dict) else getattr(result, "models", [])
            names = []
            for m in models:
                name = m.get("name") if isinstance(m, dict) else getattr(m, "model", None)
                if name:
                    names.append(name)
            self.available_models = names
            self.lbl_models.config(text=", ".join(names) if names else "No models pulled yet")
            self._refresh_model_dropdowns()
        except Exception as e:
            self.available_models = []
            self.lbl_models.config(text="Could not reach Ollama — is it running?")
            if not startup:
                messagebox.showerror(
                    "Ollama Not Reachable",
                    f"Couldn't get the model list from Ollama.\n"
                    f"Make sure the Ollama app/service is running on this PC.\n\nDetails: {e}"
                )

    def _refresh_model_dropdowns(self):
        values = self.available_models or ["gemma2"]
        for combo in (self.combo_direct_model, self.combo_model_a, self.combo_model_b, self.combo_doc_model):
            current = combo.get()
            combo["values"] = values
            if not current and values:
                combo.set(values[0])

    def save_settings(self):
        data = {
            "direct_model": self.combo_direct_model.get(),
            "model_a": self.combo_model_a.get(),
            "model_b": self.combo_model_b.get(),
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(data, f)
            self.set_status("Settings saved.")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    def load_settings(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    data = json.load(f)
                if data.get("direct_model"):
                    self.combo_direct_model.set(data["direct_model"])
                if data.get("model_a"):
                    self.combo_model_a.set(data["model_a"])
                if data.get("model_b"):
                    self.combo_model_b.set(data["model_b"])
            except Exception:
                pass

    # ---- Direct Chat tab --------------------------------------------------
    def build_direct_chat_tab(self):
        top = ttk.Frame(self.tab_chat, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Model:").pack(side=tk.LEFT, padx=(0, 5))
        self.combo_direct_model = ttk.Combobox(top, width=25, state="readonly")
        self.combo_direct_model.pack(side=tk.LEFT, padx=5)

        ttk.Button(top, text="Clear Chat", command=self.clear_direct_chat).pack(side=tk.RIGHT, padx=5)

        self.direct_chat_display = scrolledtext.ScrolledText(
            self.tab_chat, wrap=tk.WORD, font=("Segoe UI", 10), state=tk.DISABLED)
        self.direct_chat_display.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        configure_markdown_tags(self.direct_chat_display)
        add_copy_context_menu(self.direct_chat_display)

        bottom = ttk.Frame(self.tab_chat, padding=8)
        bottom.pack(fill=tk.X)

        self.entry_direct_msg = ttk.Entry(bottom)
        self.entry_direct_msg.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.entry_direct_msg.bind("<Return>", lambda e: self.send_direct_message())

        self.btn_direct_send = ttk.Button(bottom, text="Send", command=self.send_direct_message)
        self.btn_direct_send.pack(side=tk.RIGHT)

    def clear_direct_chat(self):
        self.direct_chat_history = []
        self.direct_chat_display.config(state=tk.NORMAL)
        self.direct_chat_display.delete("1.0", tk.END)
        self.direct_chat_display.config(state=tk.DISABLED)

    def _append_direct(self, sender, text):
        self.direct_chat_display.config(state=tk.NORMAL)
        insert_markdown_message(self.direct_chat_display, sender, text)
        self.direct_chat_display.config(state=tk.DISABLED)

    def send_direct_message(self):
        msg = self.entry_direct_msg.get().strip()
        if not msg:
            return
        if not self._local_ready(show_error=True):
            return
        model_name = self.combo_direct_model.get().strip() or "gemma2"

        self.entry_direct_msg.delete(0, tk.END)
        self._append_direct("You", msg)
        self.direct_chat_history.append({"role": "user", "content": msg})
        self.btn_direct_send.config(state=tk.DISABLED)
        self.set_status(f"Waiting for local model '{model_name}'...")

        threading.Thread(target=self._direct_worker, args=(model_name,), daemon=True).start()

    def _direct_worker(self, model_name):
        try:
            resp = ollama.chat(model=model_name, messages=self.direct_chat_history)["message"]["content"]
            self.direct_chat_history.append({"role": "assistant", "content": resp})
            self.root.after(0, lambda: self._append_direct(f"Local {model_name}", resp))
            self.root.after(0, lambda: self.set_status("Ready."))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror(
                "Request Failed",
                f"Couldn't get a response from '{model_name}'.\n"
                f"Make sure Ollama is running and the model is pulled "
                f"(e.g. 'ollama run {model_name}').\n\nDetails: {e}"))
            self.root.after(0, lambda: self.set_status("Error — see popup."))
        finally:
            self.root.after(0, lambda: self.btn_direct_send.config(state=tk.NORMAL))

    # ---- Dual-Model Debate tab (two LOCAL models, no cloud) ----------------
    def build_agent_tab(self):
        top = ttk.Frame(self.tab_agents, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Model A:").pack(side=tk.LEFT)
        self.combo_model_a = ttk.Combobox(top, width=16, state="readonly")
        self.combo_model_a.pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text="Model B:").pack(side=tk.LEFT, padx=(10, 0))
        self.combo_model_b = ttk.Combobox(top, width=16, state="readonly")
        self.combo_model_b.pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text="Rounds:").pack(side=tk.LEFT, padx=(10, 2))
        self.spin_rounds = ttk.Spinbox(top, from_=1, to=10, width=4)
        self.spin_rounds.set(3)
        self.spin_rounds.pack(side=tk.LEFT)

        self.btn_run = ttk.Button(top, text="Start Debate", command=self.start_agent_loop)
        self.btn_run.pack(side=tk.RIGHT, padx=5)

        topic_frame = ttk.Frame(self.tab_agents, padding=(8, 0, 8, 8))
        topic_frame.pack(fill=tk.X)
        ttk.Label(topic_frame, text="Topic:").pack(side=tk.LEFT, padx=(0, 5))
        self.entry_topic = ttk.Entry(topic_frame)
        self.entry_topic.insert(0, "The pros and cons of running AI fully offline.")
        self.entry_topic.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.chat_area = scrolledtext.ScrolledText(self.tab_agents, wrap=tk.WORD, font=("Segoe UI", 10),
                                                    state=tk.DISABLED)
        self.chat_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        configure_markdown_tags(self.chat_area)
        add_copy_context_menu(self.chat_area)

    def _clear_chat_area(self):
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.delete("1.0", tk.END)
        self.chat_area.config(state=tk.DISABLED)

    def _log_round_header(self, round_num):
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.insert(tk.END, f"🔔 ROUND {round_num}\n" + "-" * 40 + "\n")
        self.chat_area.config(state=tk.DISABLED)

    def start_agent_loop(self):
        if not self._local_ready(show_error=True):
            return
        model_a = self.combo_model_a.get().strip()
        model_b = self.combo_model_b.get().strip()
        if not model_a or not model_b:
            messagebox.showwarning("Pick Two Models", "Choose a model for both A and B in the dropdowns.\n"
                                                          "They can be the same model or different ones.")
            return
        self.btn_run.config(state=tk.DISABLED)
        threading.Thread(target=self._async_agent_loop, args=(model_a, model_b), daemon=True).start()

    def _log_agent(self, author, data):
        self.chat_area.config(state=tk.NORMAL)
        insert_markdown_message(self.chat_area, f"🚀 {author}", data)
        self.chat_area.config(state=tk.DISABLED)

    def _async_agent_loop(self, model_a, model_b):
        topic = self.entry_topic.get().strip()
        try:
            rounds = max(1, int(self.spin_rounds.get()))
        except ValueError:
            rounds = 3

        self.root.after(0, lambda: self._clear_chat_area())

        try:
            history_a = [{"role": "system",
                          "content": f"You are {model_a}. Converse concisely with {model_b} (under 3 sentences)."}]
            history_b = [{"role": "system",
                          "content": f"You are {model_b}. Converse concisely with {model_a} (under 3 sentences)."}]

            payload = f"Let's discuss this topic: {topic}. What are your initial thoughts?"

            for r in range(1, rounds + 1):
                self.root.after(0, lambda rr=r: self._log_round_header(rr))

                history_a.append({"role": "user", "content": payload})
                resp_a = ollama.chat(model=model_a, messages=history_a)["message"]["content"]
                history_a.append({"role": "assistant", "content": resp_a})
                self.root.after(0, lambda r=resp_a, m=model_a: self._log_agent(m, r))

                history_b.append({"role": "user", "content": resp_a})
                resp_b = ollama.chat(model=model_b, messages=history_b)["message"]["content"]
                history_b.append({"role": "assistant", "content": resp_b})
                self.root.after(0, lambda r=resp_b, m=model_b: self._log_agent(m, r))

                payload = resp_b
        except Exception as ex:
            self.root.after(0, lambda: messagebox.showerror("Execution Aborted", str(ex)))
        finally:
            self.root.after(0, lambda: self.btn_run.config(state=tk.NORMAL))

    # ---- PDF & Image tab (fully local) --------------------------------------
    def build_docs_tab(self):
        actions = ttk.Frame(self.tab_docs, padding=10)
        actions.pack(fill=tk.X)

        self.btn_pdf = ttk.Button(actions, text="📁 Ingest PDF", command=self.parse_pdf)
        self.btn_pdf.pack(side=tk.LEFT, padx=5)
        ttk.Button(actions, text="🖼️ Ingest Image (OCR)", command=self.parse_image).pack(side=tk.LEFT, padx=5)

        self.var_force_ocr = tk.BooleanVar(value=False)
        ttk.Checkbutton(actions, text="Force OCR (use for scanned PDFs)",
                         variable=self.var_force_ocr).pack(side=tk.LEFT, padx=10)

        self.lbl_file_info = ttk.Label(actions, text="No file imported yet.", font=("Arial", 9, "italic"))
        self.lbl_file_info.pack(side=tk.LEFT, padx=15)

        query_f = ttk.LabelFrame(self.tab_docs, text=" Ask a Local Model About the Imported File ", padding=8)
        query_f.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(query_f, text="Model:").pack(side=tk.LEFT, padx=(5, 5))
        self.combo_doc_model = ttk.Combobox(query_f, width=16, state="readonly")
        self.combo_doc_model.pack(side=tk.LEFT, padx=(0, 10))

        self.entry_query = ttk.Entry(query_f, width=45)
        self.entry_query.insert(0, "Provide a high-density summary of this document.")
        self.entry_query.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, pady=5)

        self.btn_query = ttk.Button(query_f, text="Ask", command=self.query_doc_engine)
        self.btn_query.pack(side=tk.RIGHT, padx=4)

        splitter = ttk.PanedWindow(self.tab_docs, orient=tk.VERTICAL)
        splitter.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.txt_raw = scrolledtext.ScrolledText(splitter, height=7, wrap=tk.WORD, bg="#fafafa", fg="#333333")
        splitter.add(self.txt_raw, weight=1)
        self.txt_raw.insert(tk.END, "--- Extracted file text will appear here ---")
        add_copy_context_menu(self.txt_raw)

        self.txt_output = scrolledtext.ScrolledText(splitter, height=12, wrap=tk.WORD, bg="#f5f7f8", fg="#111111",
                                                      font=("Segoe UI", 10))
        splitter.add(self.txt_output, weight=2)
        configure_markdown_tags(self.txt_output)
        add_copy_context_menu(self.txt_output)

    def parse_pdf(self):
        if not pypdf:
            messagebox.showerror("Missing Library", "pypdf is not installed.\nRun: pip install pypdf")
            return
        target = filedialog.askopenfilename(filetypes=[("PDF Documents", "*.pdf")])
        if not target:
            return

        self.btn_pdf.config(state=tk.DISABLED)
        self.txt_raw.delete("1.0", tk.END)
        self.txt_raw.insert(tk.END, "Reading PDF...")
        force_ocr = self.var_force_ocr.get()
        threading.Thread(target=self._pdf_worker, args=(target, force_ocr), daemon=True).start()

    def _pdf_worker(self, target, force_ocr):
        try:
            page_count = 0
            text_extracted = ""

            if not force_ocr:
                reader = pypdf.PdfReader(target)
                page_count = len(reader.pages)
                text_extracted = "".join(page.extract_text() or "" for page in reader.pages).strip()

            avg_chars_per_page = (len(text_extracted) / page_count) if page_count else 0
            looks_scanned = force_ocr or avg_chars_per_page < MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK

            if not looks_scanned:
                self.extracted_text_context = text_extracted
                self.root.after(0, lambda: self._finish_pdf_load(target, text_extracted, was_ocr=False))
                return

            # --- Scanned PDF path: render each page to an image, then OCR it ---
            if fitz is None:
                self.root.after(0, lambda: messagebox.showerror(
                    "Missing Library",
                    "This PDF looks scanned (little/no selectable text), but PyMuPDF isn't "
                    "installed, so it can't be rendered for OCR.\nRun: pip install pymupdf"))
                self.root.after(0, lambda: self._finish_pdf_load(target, text_extracted, was_ocr=False))
                return
            if not Image or not pytesseract:
                self.root.after(0, lambda: messagebox.showerror(
                    "Missing Library",
                    "This PDF looks scanned, but pillow/pytesseract aren't installed.\n"
                    "Run: pip install pillow pytesseract (and install the Tesseract engine itself)."))
                self.root.after(0, lambda: self._finish_pdf_load(target, text_extracted, was_ocr=False))
                return

            doc = fitz.open(target)
            total_pages = doc.page_count
            ocr_chunks = []
            zoom = 2.0  # ~144 DPI; raise to 3.0 for small/blurry scans at the cost of speed

            for i, page in enumerate(doc, start=1):
                self.root.after(0, lambda i=i, t=total_pages: self.set_status(
                    f"OCR'ing scanned PDF — page {i} of {t}..."))
                self.root.after(0, lambda i=i, t=total_pages: self._set_raw_preview(
                    f"OCR'ing scanned PDF — page {i} of {t}...\n(this can take a while for long documents)"))

                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                page_text = pytesseract.image_to_string(img)
                ocr_chunks.append(f"--- Page {i} ---\n{page_text.strip()}")

            doc.close()
            full_text = "\n\n".join(ocr_chunks).strip()
            self.extracted_text_context = full_text
            self.root.after(0, lambda: self._finish_pdf_load(target, full_text, was_ocr=True))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("PDF Parsing Error", str(e)))
            self.root.after(0, lambda: self.btn_pdf.config(state=tk.NORMAL))

    def _set_raw_preview(self, text):
        self.txt_raw.delete("1.0", tk.END)
        self.txt_raw.insert(tk.END, text)

    def _finish_pdf_load(self, target, text, was_ocr):
        preview = text or "(No text could be extracted from this file, even with OCR.)"
        self._set_raw_preview(preview)
        mode = "OCR (scanned PDF)" if was_ocr else "text extraction"
        self.lbl_file_info.config(text=f"Loaded PDF via {mode}: {os.path.basename(target)}")
        self.set_status(f"Extracted {len(text)} characters from PDF via {mode}.")
        self.btn_pdf.config(state=tk.NORMAL)

    def parse_image(self):
        if not Image or not pytesseract:
            messagebox.showerror("Missing Library", "pillow and/or pytesseract not installed.\n"
                                                       "Run: pip install pillow pytesseract")
            return
        # Extensions must be SPACE separated for Tk file dialogs, not ";" separated.
        target = filedialog.askopenfilename(
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff")])
        if not target:
            return
        try:
            img_obj = Image.open(target)
            ocr_text = pytesseract.image_to_string(img_obj)
            self.extracted_text_context = ocr_text.strip()
            self.txt_raw.delete("1.0", tk.END)
            preview = self.extracted_text_context or "(No text detected in image.)"
            self.txt_raw.insert(tk.END, preview)
            self.lbl_file_info.config(text=f"Loaded Image: {os.path.basename(target)}")
            self.set_status(f"OCR extracted {len(self.extracted_text_context)} characters.")
        except Exception as e:
            messagebox.showerror(
                "OCR Error",
                f"Failed to read text from the image.\n"
                f"Make sure the Tesseract OCR engine is installed and on your system PATH.\n\nDetails: {e}"
            )

    def query_doc_engine(self):
        if not self.extracted_text_context:
            messagebox.showwarning("No Content", "Import a PDF or image first.")
            return
        if not self._local_ready(show_error=True):
            return
        model_name = self.combo_doc_model.get().strip() or "gemma2"
        query_str = self.entry_query.get().strip()
        self.txt_output.delete("1.0", tk.END)
        self.txt_output.insert(tk.END, "Thinking...")
        self.btn_query.config(state=tk.DISABLED)

        threading.Thread(target=self._doc_worker, args=(model_name, query_str), daemon=True).start()

    def _doc_worker(self, model_name, query_str):
        try:
            prompt = (f"Context document:\n\"\"\"\n{self.extracted_text_context[:7000]}\n\"\"\"\n\n"
                      f"Question: {query_str}")
            answer = ollama.chat(
                model=model_name,
                messages=[{"role": "user", "content": prompt}]
            )["message"]["content"]
            self.root.after(0, lambda: self._set_doc_output(answer))
        except Exception as err:
            self.root.after(0, lambda: self._set_doc_output(f"Error: {err}"))
        finally:
            self.root.after(0, lambda: self.btn_query.config(state=tk.NORMAL))

    def _set_doc_output(self, text):
        self.txt_output.delete("1.0", tk.END)
        render_markdown_body(self.txt_output, text)

    # ---- Shared helpers -----------------------------------------------------
    def set_status(self, text):
        self.status_var.set(text)

    def _local_ready(self, show_error=False):
        if ollama is None:
            if show_error:
                messagebox.showerror("Missing Library", "The 'ollama' package is not installed.\n"
                                                           "Run: pip install ollama")
            return False
        return True


if __name__ == "__main__":
    tk_root = tk.Tk()
    app = LocalAIStudio(tk_root)
    tk_root.mainloop()

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SQLITE_PATH = DATA_DIR / "krillion.db"


def read_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.casefold(), flags=re.UNICODE)).strip()


COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda", "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan",
    "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso", "Burundi",
    "Cabo Verde", "Cambodia", "Cameroon", "Canada", "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros", "Costa Rica", "Croatia", "Cuba", "Cyprus", "Czechia",
    "Democratic Republic of the Congo", "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia",
    "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guyana", "Haiti", "Honduras", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan", "Jordan",
    "Kazakhstan", "Kenya", "Kiribati", "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar",
    "Namibia", "Nauru", "Nepal", "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Republic of the Congo", "Romania", "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia", "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe"
]

LEXICON = {
    "countries": COUNTRIES,
    "planets": ["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"],
    "continents": ["Africa", "Antarctica", "Asia", "Europe", "North America", "Oceania", "South America"],
    "oceans": ["Arctic Ocean", "Atlantic Ocean", "Indian Ocean", "Pacific Ocean", "Southern Ocean"],
    "colors": ["Red", "Orange", "Yellow", "Green", "Blue", "Indigo", "Violet", "Black", "White", "Pink", "Brown", "Gray"],
    "months": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "days of the week": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "common animals": ["Dog", "Cat", "Horse", "Cow", "Sheep", "Goat", "Pig", "Lion", "Tiger", "Elephant", "Giraffe", "Zebra", "Dolphin", "Whale", "Eagle", "Penguin", "Shark", "Octopus"],
    "common foods": ["Apple", "Banana", "Bread", "Cheese", "Chocolate", "Pizza", "Rice", "Pasta", "Potato", "Tomato", "Carrot", "Chicken", "Salmon", "Egg", "Yogurt"],
}


class Store:
    def __init__(self) -> None:
        self.pg = bool(os.environ.get("DATABASE_URL", "").strip())
        self.conn = None
        if self.pg:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL is configured but psycopg is missing. Run: pip install -r requirements.txt") from exc
            self.conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
        else:
            DATA_DIR.mkdir(exist_ok=True)
            self.conn = sqlite3.connect(SQLITE_PATH)
            self.conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        if self.pg:
            sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
            with self.conn.cursor() as cur:
                cur.execute(sql)
            self.conn.commit()
        else:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, category TEXT NOT NULL,
                    normalized_category TEXT NOT NULL, UNIQUE(mode, normalized_category));
                CREATE TABLE IF NOT EXISTS answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_id INTEGER NOT NULL REFERENCES prompts(id),
                    answer TEXT NOT NULL, normalized_answer TEXT NOT NULL,
                    status TEXT NOT NULL, level TEXT, source TEXT NOT NULL DEFAULT 'player',
                    reporter TEXT, note TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS answers_lookup_idx ON answers(prompt_id, normalized_answer);
                CREATE TABLE IF NOT EXISTS lexicon (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, answer TEXT NOT NULL,
                    normalized_answer TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'seed',
                    UNIQUE(topic, normalized_answer));
            """)
            self.conn.commit()
        self.seed_lexicon()

    def _execute(self, sql: str, params=()):
        if self.pg:
            cur = self.conn.cursor()
            cur.execute(sql.replace("?", "%s"), params)
            return cur
        return self.conn.execute(sql, params)

    def seed_lexicon(self) -> None:
        for topic, answers in LEXICON.items():
            for answer in answers:
                self._execute("INSERT INTO lexicon(topic, answer, normalized_answer) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (topic, answer, normalize(answer)))
        self.conn.commit()

    def ensure_prompt(self, mode: str, category: str) -> int:
        norm = normalize(category)
        self._execute("INSERT INTO prompts(mode, category, normalized_category) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (mode, category.strip(), norm))
        row = self._execute("SELECT id FROM prompts WHERE mode = ? AND normalized_category = ?", (mode, norm)).fetchone()
        self.conn.commit()
        return row["id"] if isinstance(row, dict) else row[0]

    def lookup(self, mode: str, category: str) -> list[dict]:
        norm = normalize(category)
        rows = self._execute("""SELECT a.answer, a.status, a.level, a.source, a.reporter, a.note, a.created_at
            FROM answers a JOIN prompts p ON p.id=a.prompt_id
            WHERE p.mode=? AND p.normalized_category=? ORDER BY CASE a.status WHEN 'accepted' THEN 0 WHEN 'unverified' THEN 1 ELSE 2 END, a.created_at DESC""", (mode, norm)).fetchall()
        return [dict(r) for r in rows]

    def lexicon_answers(self, category: str) -> list[str]:
        cat = normalize(category)
        topic_map = {normalize(topic): topic for topic in LEXICON}
        topic = topic_map.get(cat)
        if topic:
            rows = self._execute("SELECT answer FROM lexicon WHERE topic=? ORDER BY answer", (topic,)).fetchall()
            return [r["answer"] if isinstance(r, dict) else r[0] for r in rows]
        return []

    def save_report(self, mode: str, category: str, answer: str, status: str, level: str, reporter: str, note: str) -> None:
        pid = self.ensure_prompt(mode, category)
        self._execute("INSERT INTO answers(prompt_id, answer, normalized_answer, status, level, reporter, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (pid, answer.strip(), normalize(answer), status, level.strip() or None, reporter.strip() or None, note.strip() or None))
        self.conn.commit()


AI_CACHE: dict[tuple, tuple[float, list[str]]] = {}
AI_CACHE_TTL = 12 * 60 * 60


def ollama_suggest(category: str, known: list[str], count: int = 8, mode: str = "Krillion") -> list[str]:
    endpoint = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    cache_key = (model, mode, normalize(category), count, tuple(sorted(normalize(x) for x in known)))
    cached = AI_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < AI_CACHE_TTL:
        return cached[1]
    prompt = (f"Give {count} distinct plausible answers for the {mode} category: {category!r}. "
              "Return only one short answer per line; no numbering, explanations, or introductions. "
              "Prefer valid, recognizable, somewhat uncommon answers and include a familiar fallback. "
              "Do not repeat these known answers: " + ", ".join(known[:40]))
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "10m"),
        "options": {"num_predict": int(os.getenv("OLLAMA_NUM_PREDICT", "120")), "temperature": 0.35},
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=45) as response:
        result = json.loads(response.read().decode("utf-8"))
    lines = result.get("response", "").splitlines()
    answers = []
    for line in lines:
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip("\"'` ")
        if line and normalize(line) not in {normalize(x) for x in known + answers}:
            answers.append(line)
    answers = answers[:10]
    AI_CACHE[cache_key] = (time.time(), answers)
    return answers


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Krillion Solver — early version")
        self.geometry("700x520")
        self.minsize(600, 480)
        try:
            self.store = Store()
        except Exception as exc:
            messagebox.showerror("Database connection", str(exc))
            self.destroy()
            return
        self._build()

    def _build(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Krillion Solver", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="Manual lookup for Krillion and Puffer Party. Suggestions are not guaranteed accepted.").pack(anchor="w", pady=(0, 12))
        self.always_on_top = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="Always on top", variable=self.always_on_top, command=self._toggle_topmost).pack(anchor="w", pady=(0, 8))
        form = ttk.Frame(root)
        form.pack(fill="x")
        ttk.Label(form, text="Mode").grid(row=0, column=0, sticky="w")
        self.mode = tk.StringVar(value="Krillion")
        ttk.Combobox(form, textvariable=self.mode, values=["Krillion", "Puffer Party"], state="readonly", width=18).grid(row=1, column=0, sticky="w", padx=(0, 12))
        ttk.Label(form, text="Category / prompt").grid(row=0, column=1, sticky="w")
        self.category = tk.StringVar()
        cat = ttk.Entry(form, textvariable=self.category)
        cat.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        ttk.Button(form, text="Find answers", command=self.search).grid(row=1, column=2)
        form.columnconfigure(1, weight=1)
        self.use_ai = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="Ollama AI suggestions (optional; unverified)", variable=self.use_ai).pack(anchor="w", pady=(8, 4))
        self.results = tk.Listbox(root, height=8, font=("Segoe UI", 10))
        self.results.pack(fill="both", expand=True, pady=(4, 10))
        self.results.insert("end", "Enter a category, then choose Find answers.")
        ttk.Label(root, text="Record a result / player-reported answer", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        report = ttk.Frame(root)
        report.pack(fill="x", pady=(5, 0))
        self.answer = tk.StringVar()
        ttk.Entry(report, textvariable=self.answer).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.status = tk.StringVar(value="accepted")
        ttk.Combobox(report, textvariable=self.status, values=["accepted", "rejected", "unverified"], state="readonly", width=12).grid(row=0, column=1, padx=(0, 8))
        self.level = tk.StringVar()
        ttk.Entry(report, textvariable=self.level, width=14).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(report, text="Save report", command=self.save).grid(row=0, column=3)
        report.columnconfigure(0, weight=1)
        details = ttk.Frame(root)
        details.pack(fill="x", pady=(5, 0))
        self.reporter = tk.StringVar()
        self.note = tk.StringVar()
        ttk.Label(details, text="Reported by").grid(row=0, column=0, sticky="w")
        ttk.Entry(details, textvariable=self.reporter).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(details, text="Note (optional)").grid(row=0, column=1, sticky="w")
        ttk.Entry(details, textvariable=self.note).grid(row=1, column=1, sticky="ew")
        details.columnconfigure(0, weight=1)
        details.columnconfigure(1, weight=2)
        ttk.Label(root, text="Level is optional. Reports are scoped to the selected mode and category.", foreground="#555").pack(anchor="w", pady=(4, 0))
        self.bind("<Return>", lambda _event: self.search())

    def _toggle_topmost(self):
        self.attributes("-topmost", self.always_on_top.get())

    def search(self):
        category = self.category.get().strip()
        if not category:
            messagebox.showinfo("Category required", "Enter a category or prompt first.")
            return
        known = self.store.lookup(self.mode.get(), category)
        lex = self.store.lexicon_answers(category)
        self.results.delete(0, "end")
        shown = set()
        for row in known:
            label = f"[{row['status'].upper()}] {row['answer']}" + (f" — level: {row['level']}" if row["level"] else "") + (f" — by {row['reporter']}" if row["reporter"] else "")
            self.results.insert("end", label)
            shown.add(normalize(row["answer"]))
        added_lex = 0
        for answer in lex:
            if normalize(answer) not in shown:
                self.results.insert("end", f"[LEXICON / UNVERIFIED] {answer}")
                shown.add(normalize(answer)); added_lex += 1
        self.results.insert("end", f"--- {len(known)} stored reports; {added_lex} lexicon suggestions ---")
        useful_answers = {normalize(row["answer"]) for row in known if row["status"] == "accepted"}
        useful_answers.update(normalize(answer) for answer in lex)
        useful_count = len(useful_answers)
        if self.use_ai.get() and useful_count < 5:
            self.results.insert("end", "Asking Ollama…")
            needed = max(3, 8 - useful_count)
            blocked = {normalize(row["answer"]) for row in known if row["status"] != "accepted"}
            ai_known = [answer for answer in shown if normalize(answer) not in blocked]
            threading.Thread(target=self._ai_worker, args=(category, ai_known, needed, self.mode.get()), daemon=True).start()
        elif not known and not lex:
            self.results.insert("end", "No stored answers or matching lexicon. Enable Ollama or add a player report.")
        elif self.use_ai.get() and useful_count >= 5:
            self.results.insert("end", "AI skipped: lookup already found at least five accepted or lexicon suggestions.")

    def _ai_worker(self, category: str, known: list[str], count: int, mode: str):
        try:
            answers = ollama_suggest(category, known, count, mode)
            self.after(0, lambda: self._show_ai(answers))
        except Exception as exc:
            self.after(0, lambda: self.results.insert("end", f"[AI unavailable] {exc}"))

    def _show_ai(self, answers: list[str]):
        self.results.insert("end", "--- AI candidates (unverified; verify before relying on them) ---")
        for answer in answers:
            self.results.insert("end", f"[AI / UNVERIFIED] {answer}")
        if not answers:
            self.results.insert("end", "Ollama returned no usable candidates.")

    def save(self):
        category, answer = self.category.get().strip(), self.answer.get().strip()
        if not category or not answer:
            messagebox.showinfo("Missing information", "Enter a category and answer before saving.")
            return
        try:
            self.store.save_report(self.mode.get(), category, answer, self.status.get(), self.level.get(), self.reporter.get(), self.note.get())
            self.answer.set("")
            self.search()
            messagebox.showinfo("Saved", "Player report saved.")
        except Exception as exc:
            messagebox.showerror("Could not save", str(exc))


if __name__ == "__main__":
    read_env()
    app = App()
    if app.winfo_exists():
        app.mainloop()

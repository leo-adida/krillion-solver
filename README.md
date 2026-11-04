# Krillion Solver (early version)

A small Windows desktop helper for looking up and recording answers for Krillion and Puffer Party.

## What it does

- Search previously reported answers for a game mode and prompt.
- Show reported level and whether an answer was accepted, rejected, or is unverified.
- Suggest country names and a small set of common topic examples.
- Optionally ask a local Ollama model for 5–10 more candidates when the stored list is short.
- Record your own result so the answer database improves over time.
- Run with SQLite out of the box, or PostgreSQL when `DATABASE_URL` is configured.

This is a companion, not an automatic game client. Enter the category/prompt manually. A model suggestion is not proof that Krillion accepts an answer. The app labels stored accepted reports separately from generated/unverified suggestions.

## Windows quick start (no PostgreSQL or AI required)

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/). During install, enable **Add Python to PATH**.
2. Open PowerShell in this folder.
3. Run:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   py app.py
   ```

   If PowerShell blocks activation, use `\.venv\Scripts\python.exe app.py` instead.

The default SQLite database is created at `data/krillion.db`.

## PostgreSQL setup (optional)

1. Install Docker Desktop, start it, then from this folder run:

   ```powershell
   docker compose up -d
   ```

   This starts PostgreSQL on `localhost:5432` with database `krillion`, user `krillion`, password `change-me-local`.
2. Copy `.env.example` to `.env`, then set `DATABASE_URL=postgresql://krillion:change-me-local@localhost:5432/krillion` for this local container. Leave it blank to use SQLite instead.
3. Install dependencies and launch the app. The schema and seed lexicon are initialized automatically at startup.

To use a PostgreSQL server you already have, put its connection URL in `DATABASE_URL`, for example:
`postgresql://username:password@localhost:5432/krillion`.

Do not use the example password for a server exposed to the internet. Keep credentials private. The database stores answer reports and does not need any game account credentials.

## Optional local AI with Ollama

1. Install [Ollama for Windows](https://ollama.com/download/windows).
2. In PowerShell, download a small instruct model, for example:

   ```powershell
   ollama pull qwen2.5:3b
   ```

3. Keep Ollama running (normally it runs as a background service). The app calls `http://localhost:11434/api/generate` and uses `qwen2.5:3b` by default.
4. In the app, select **Ollama AI suggestions**. To change the model or endpoint, set `OLLAMA_MODEL` or `OLLAMA_URL` in `.env` and restart.

The request asks Ollama to keep the model loaded for 10 minutes and caps generation at 120 tokens (`OLLAMA_KEEP_ALIVE`, `OLLAMA_NUM_PREDICT`). AI is skipped when lookup already finds at least five distinct accepted or lexicon answers. Otherwise it requests only enough candidates to approach eight total. Generated lists are cached in memory for 12 hours per model, mode, prompt, and known-answer set; restarting the app clears this cache.

No model is bundled or trained in this early version. The model generates candidate answers only; it cannot guarantee acceptance or rarity. Reports you record are the evidence the solver can use to identify known accepted answers.

## Notes / next steps

- Main Krillion and Puffer Party are separate scopes in storage. The app does not read or write the game website.
- The seeded lexicon is intentionally modest. Add curated sources and aliases before treating it as comprehensive.
- The next useful improvement is a review/export flow for player reports and better topic-specific lexicons. Fine-tuning should wait until there is a sizeable, quality-reviewed dataset.
- PostgreSQL and Ollama are optional. Without them, SQLite lookup, lexicon suggestions, and manual feedback still work.

# Krillion Solver (early version)

A small Windows desktop helper for looking up and recording answers for Krillion and Puffer Party.

## What it does

- Search previously reported answers for a game mode and prompt.
- Show reported level and whether an answer was accepted, rejected, or is unverified.
- Copy a selected answer or record it as accepted/rejected directly from the result list.
- Suggest country names and common aliases for country prompts, including letter-constrained prompts.
- Regenerate AI candidates without reusing the current suggestions or cached response.
- Switch to a compact window after prompt detection, or return to full view; clear the current results when needed.
- Suggest a seeded list of real coastal cities for city prompts.
- Optionally ask a local Ollama model for 5–10 more candidates when the stored list is short.
- Show complete AI answer lines as they arrive and ignore results from prompts you have since changed.
- Check explicit letter, word-count, syllable-count, compound-word, and supported country-region/coastline rules against local and AI suggestions.
- Hide answers that are already visible in the current game round and exclude them from later AI requests.
- Inspect the detected page text, URL, selected prompt, and detection errors in the diagnostics window.
- Record your own result so the answer database improves over time.
- Run with SQLite out of the box, or PostgreSQL when `DATABASE_URL` is configured.

This is a companion, not an automatic game client. It can detect visible prompts in its separate browser window or accept manual prompt entry. A model suggestion is not proof that Krillion accepts an answer. The app labels stored accepted reports separately from generated/unverified suggestions.

## Windows quick start (no PostgreSQL or AI required)

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/). During install, enable **Add Python to PATH**.
2. Open PowerShell in this folder.
3. Run:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   py -m playwright install chromium
   py app.py
   ```

   If PowerShell blocks activation, use `\.venv\Scripts\python.exe app.py` instead.

The default SQLite database is created at `data/krillion.db`.

## Detect the current game prompt

Choose **Krillion** or **Puffer Party**, then click **Detect prompt**. The app opens a separate Chromium window, navigates to that game, watches visible page text, and searches automatically when a question/category prompt appears. The Chromium profile is stored under `data/browser-profile` and persists between launches. Click **Stop detection** to close the monitoring browser.

This browser window is separate from Chrome and has its own sign-in/profile. If Chromium is missing, install it with `py -m playwright install chromium`. Detection works from visible text; prompts rendered only as images or canvas text may not be recognized. Use **Stop detection** to close the monitoring browser.

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

Ollama keeps the model loaded for 10 minutes by default (`OLLAMA_KEEP_ALIVE`). Generation starts with a short request for one familiar fallback, then makes a second request for additional answers, up to eight total suggestions. The longer request has no fixed output-token cap. AI is skipped when lookup already finds at least five distinct accepted or lexicon answers. Ollama is asked for schema-constrained JSON containing answer strings, and the app drops entries that look like explanations instead of concise answers. Answer strings appear as they arrive. Generated lists are cached in memory for 12 hours per model, mode, prompt, and known-answer set; restarting the app clears this cache.

Select an answer row to copy it, or use **Accepted** / **Rejected** to save feedback directly. Rejected answers are hidden from suggestions and excluded from later AI requests. After two AI answers are judged, then three on the next cycle, the app automatically asks Ollama for replacements; judged rows remain visible as accepted or disappear as rejected until that refresh. When browser detection is running, visible answer rows that match suggestions are hidden as already used; this is only an in-memory round list and is not treated as rejection feedback. Explicit visible game messages such as “answer was accepted/rejected” and “player: answer ✓/✕” are saved to the current prompt; detected reports are deduplicated. **Diagnostics** shows the current URL, extracted page text, selected prompt, matching method, and any browser error. Expand **Add player report** to enter a report manually, including its level, reporter, and note. **Regenerate** requests a fresh set and excludes the currently displayed and already-used answers. **Clear** empties the results and cancels pending AI work. Results from an earlier search are ignored after you start a new one, and the app signals cancellation to stop later generation passes. The first AI result is labeled as a model fallback, but remains unverified; only a recorded accepted answer is confirmed by prior play. The AI prompt prioritizes factual, independently existing entities, tells the model not to invent names or use game-related terms, and allows it to abstain. The local topic lexicon is also passed as examples to Ollama. CMUdict checks syllable-count rules when a prompt gives an exact count; unknown words are left unfiltered. Country hints include approximate coastline and continent filters. Prompt detection switches the app to compact view; **Full view** restores the mode controls and player-report section.

No model is bundled or trained in this early version. The model generates candidate answers only; it cannot guarantee acceptance or rarity. Reports you record are the evidence the solver can use to identify known accepted answers.

## Notes / next steps

- Main Krillion and Puffer Party are separate scopes in storage. Browser detection reads visible page text only; it does not submit answers or otherwise control game play. Feedback capture depends on the game's visible message wording and may need adjustment if that wording changes.
- The seeded lexicon is intentionally modest. Add curated sources and aliases before treating it as comprehensive.
- The next useful improvement is a review/export flow for player reports and better topic-specific lexicons. Fine-tuning should wait until there is a sizeable, quality-reviewed dataset.
- PostgreSQL and Ollama are optional. Without them, SQLite lookup, lexicon suggestions, and manual feedback still work.

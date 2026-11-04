CREATE TABLE IF NOT EXISTS prompts (
    id BIGSERIAL PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('Krillion', 'Puffer Party')),
    category TEXT NOT NULL,
    normalized_category TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(mode, normalized_category)
);

CREATE TABLE IF NOT EXISTS answers (
    id BIGSERIAL PRIMARY KEY,
    prompt_id BIGINT NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    answer TEXT NOT NULL,
    normalized_answer TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'unverified')),
    level TEXT,
    source TEXT NOT NULL DEFAULT 'player',
    reporter TEXT,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS answers_lookup_idx ON answers(prompt_id, normalized_answer);
CREATE INDEX IF NOT EXISTS prompts_category_idx ON prompts(mode, normalized_category);

CREATE TABLE IF NOT EXISTS lexicon (
    id BIGSERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    answer TEXT NOT NULL,
    normalized_answer TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'seed',
    UNIQUE(topic, normalized_answer)
);

-- Daybook memory vault (plan 8.3). One SQLite file per user. PRAGMA user_version = 1.
-- Dates are YYYY-MM-DD in the user's timezone; timestamps are ISO 8601 UTC.
-- docs_vec (sqlite-vec) is created by daybook/memory/db.py only when the extension loads (DB-06).

CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK (id = 1), display_name TEXT,
  timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata', org TEXT, role_title TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL CHECK (json_valid(value)));

CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  aliases TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases)),
  kind TEXT NOT NULL DEFAULT 'work' CHECK (kind IN ('work','side','learning','community')),
  summary TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done','paused')),
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  category TEXT NOT NULL DEFAULT 'other' CHECK (category IN ('language','framework','database','cloud',
  'devops','data','ai-ml','tool','practice','domain','soft','other')),
  aliases TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases)));

CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY,
  mode TEXT NOT NULL CHECK (mode IN ('guided','dump','quick','edit','close_loops')),
  local_date TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('active','ended','finalizing','finalized','failed')),
  end_reason TEXT CHECK (end_reason IN ('user','time_limit','idle','budget','error')),
  turn_count INTEGER NOT NULL DEFAULT 0, energy INTEGER CHECK (energy BETWEEN 1 AND 5), mood_word TEXT,
  stats TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(stats)),
  prompt_versions TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(prompt_versions)),
  feedback TEXT CHECK (feedback IN ('up','down')));

CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
  seq INTEGER NOT NULL, role TEXT NOT NULL CHECK (role IN ('user','assistant')), text TEXT NOT NULL,
  intent TEXT, dictated_chars INTEGER, created_at TEXT NOT NULL, UNIQUE (session_id, seq));

CREATE TABLE IF NOT EXISTS memory_items (id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
  kind TEXT NOT NULL CHECK (kind IN ('achievement','progress','learning','challenge','soft_skill',
  'decision','blocker','praise','plan')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','hidden','merged')),
  occurred_on TEXT NOT NULL, project_id INTEGER REFERENCES projects(id),
  title TEXT NOT NULL, what TEXT NOT NULL, how TEXT, result TEXT,
  bullet TEXT CHECK (bullet IS NULL OR length(bullet) BETWEEN 30 AND 220),
  scope TEXT CHECK (scope IN ('individual','team','org','company','customers','public')),
  impact INTEGER CHECK (impact BETWEEN 1 AND 5),
  quantifiable TEXT NOT NULL DEFAULT 'maybe' CHECK (quantifiable IN ('yes','maybe','no')),
  ownership TEXT CHECK (ownership IN ('led','did','helped','team')),
  source_quote TEXT NOT NULL,
  source_turn_seqs TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_turn_seqs)),
  validation_note TEXT, merged_into INTEGER REFERENCES memory_items(id),
  version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS memory_item_versions (id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES memory_items(id), version INTEGER NOT NULL,
  snapshot TEXT NOT NULL CHECK (json_valid(snapshot)),
  changed_by TEXT NOT NULL CHECK (changed_by IN ('analyzer','user_ui','user_voice','pending_answer','restore')),
  reason TEXT, created_at TEXT NOT NULL, UNIQUE (item_id, version));

CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
  name TEXT NOT NULL, category TEXT NOT NULL CHECK (category IN ('latency','throughput','cost','revenue',
  'reliability','time','scale','quality','adoption','other')), unit TEXT NOT NULL,
  baseline REAL, final REAL, delta_abs REAL, delta_pct REAL,
  better_when TEXT NOT NULL CHECK (better_when IN ('higher','lower')),
  period TEXT CHECK (period IS NULL OR period IN ('one-time','per-day','per-week','per-month','per-year')),
  approx INTEGER NOT NULL DEFAULT 0, is_derived INTEGER NOT NULL DEFAULT 0, source_text TEXT NOT NULL,
  CHECK (coalesce(baseline, final, delta_abs, delta_pct) IS NOT NULL));

CREATE TABLE IF NOT EXISTS item_skills (item_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
  skill_id INTEGER NOT NULL REFERENCES skills(id), PRIMARY KEY (item_id, skill_id)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS item_links (from_id INTEGER NOT NULL REFERENCES memory_items(id),
  to_id INTEGER NOT NULL REFERENCES memory_items(id),
  kind TEXT NOT NULL CHECK (kind IN ('continues','same_as','caused','blocked_by')),
  PRIMARY KEY (from_id, to_id, kind)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS pending_questions (id INTEGER PRIMARY KEY,
  item_id INTEGER REFERENCES memory_items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('metric','outcome','ownership','date','clarify')), question TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','not_quantifiable','dismissed','expired')),
  times_asked INTEGER NOT NULL DEFAULT 0, snooze_until TEXT, answer_text TEXT,
  created_at TEXT NOT NULL, last_asked_at TEXT, resolved_at TEXT);

CREATE TABLE IF NOT EXISTS digests (id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('day','week','month')),
  period_start TEXT NOT NULL, period_end TEXT NOT NULL, text TEXT NOT NULL,
  item_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(item_ids)), created_at TEXT NOT NULL,
  UNIQUE (kind, period_start));

CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('item','digest','turn')),
  ref_id INTEGER NOT NULL, text TEXT NOT NULL, occurred_on TEXT, project_id INTEGER,
  embedding BLOB, embed_model TEXT, content_hash TEXT NOT NULL, UNIQUE (doc_type, ref_id));

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(text, content='docs', content_rowid='id',
  tokenize='porter unicode61');

CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO docs_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS push_subscriptions (id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL, auth TEXT NOT NULL, user_agent TEXT, created_at TEXT NOT NULL,
  last_success_at TEXT, failures INTEGER NOT NULL DEFAULT 0);

CREATE INDEX IF NOT EXISTS idx_items_date ON memory_items(occurred_on);
CREATE INDEX IF NOT EXISTS idx_items_project ON memory_items(project_id);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_questions(status);
CREATE INDEX IF NOT EXISTS idx_metrics_item ON metrics(item_id);

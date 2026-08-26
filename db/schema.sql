-- The Gallery — client book (Cloudflare D1)
--
-- This is the half of the desk that must never touch the public repo: collector
-- holdings, budgets and conversation notes are sensitive personal and commercial
-- data under India's DPDP Act. It lives here, behind Cloudflare Access, and the
-- only copy is Cloudflare's.
--
-- Apply with:  wrangler d1 execute gallery-crm --file=db/schema.sql --remote

CREATE TABLE IF NOT EXISTS client (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  title         TEXT,
  city          TEXT,
  tier          TEXT,                   -- Principal | Senior | Growth
  since         TEXT,
  lifetime_inr  INTEGER,                -- paise-free rupees; format at the edge
  focus         TEXT,
  brief         TEXT,
  wants         TEXT,                   -- JSON array
  next_when     TEXT,
  next_what     TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holding (
  id          TEXT PRIMARY KEY,
  client_id   TEXT NOT NULL REFERENCES client(id) ON DELETE CASCADE,
  artist_key  TEXT,                     -- joins to the public artist data
  artist_name TEXT NOT NULL,
  work        TEXT,
  acquired    TEXT,
  paid_inr    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_holding_client ON holding(client_id);
CREATE INDEX IF NOT EXISTS idx_holding_artist ON holding(artist_key);

CREATE TABLE IF NOT EXISTS referral (
  id        TEXT PRIMARY KEY,
  client_id TEXT NOT NULL REFERENCES client(id) ON DELETE CASCADE,
  name      TEXT NOT NULL,
  tie       TEXT
);
CREATE INDEX IF NOT EXISTS idx_referral_client ON referral(client_id);

CREATE TABLE IF NOT EXISTS log (
  id        TEXT PRIMARY KEY,
  client_id TEXT NOT NULL REFERENCES client(id) ON DELETE CASCADE,
  happened  TEXT NOT NULL,              -- ISO date
  channel   TEXT,                       -- Call | Email | Viewing | Sale | Lunch | WhatsApp
  note      TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_client ON log(client_id, happened DESC);

CREATE TABLE IF NOT EXISTS followup (
  id        TEXT PRIMARY KEY,
  client_id TEXT NOT NULL REFERENCES client(id) ON DELETE CASCADE,
  due       TEXT NOT NULL,              -- ISO date
  reason    TEXT,
  done      INTEGER NOT NULL DEFAULT 0,
  done_at   TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_followup_due ON followup(done, due);

-- Every write is recorded. A client book is commercially sensitive; knowing who
-- read or changed what, and when, is part of holding it responsibly.
--
-- `detail` carries a JSON snapshot of the row as it stood BEFORE an update or a
-- delete. Deletes here are hard — there is no tombstone column — so this is the
-- only thing standing between a mis-tapped delete and a conversation note that
-- is simply gone. Read it back with:
--   SELECT at, who, action, entity, detail FROM audit
--    WHERE action = 'delete' ORDER BY at DESC;
CREATE TABLE IF NOT EXISTS audit (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  who        TEXT,                      -- the Access-verified email
  action     TEXT NOT NULL,
  entity     TEXT,
  entity_id  TEXT,
  detail     TEXT                       -- JSON of the row before it changed
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at DESC);

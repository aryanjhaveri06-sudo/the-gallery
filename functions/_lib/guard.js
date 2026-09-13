/**
 * The desk's guard rails: request size, field shape, and the lock that stops
 * someone guessing the key.
 *
 * Everything under /api already binds every value (no SQL built from input),
 * compares the key in constant time, and never echoes an exception. This adds
 * the layer a professional backend has around that:
 *
 *   readJson    a body cap (64 KB) before parsing, and a shape check after —
 *               a 50 MB body or a JSON array where an object was expected is
 *               refused before it reaches a handler
 *   clean       one place that says what each field may be: a string of at
 *               most N characters, an ISO date, a whole number of rupees, a
 *               tier from the fixed list. Anything else is dropped or refused,
 *               so a row can never carry a script, a novel, or a negative price
 *   lockout     ten wrong keys from one address in fifteen minutes and that
 *               address is refused for an hour — and the owner is told on
 *               Telegram, because a lockout on a one-person desk is news
 *
 * The lock lives in D1 (`auth_fail`) so it holds across the Worker's many
 * isolates. The address is Cloudflare's `CF-Connecting-IP`, which a client
 * cannot forge through Cloudflare. It is kept only as long as the lock.
 */

export const MAX_BODY = 64 * 1024;
const FAILS_TO_LOCK = 10;
const WINDOW_MIN = 15;
const LOCK_MIN = 60;

export class BadRequest extends Error {
  constructor(message) { super(message); this.status = 400; }
}

export async function readJson(request) {
  const len = +(request.headers.get("Content-Length") || 0);
  if (len > MAX_BODY) throw new BadRequest("That request is too large.");
  const text = await request.text();
  if (text.length > MAX_BODY) throw new BadRequest("That request is too large.");
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { throw new BadRequest("Expected a JSON body."); }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new BadRequest("Expected a JSON object.");
  return body;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ID = /^[A-Za-z0-9_.:-]{1,80}$/;

/** Field rules. `str:N` a string cut at N; `date` an ISO day or null; `int` a
 *  non-negative whole number or null; `id` a safe identifier; `enum:a|b` one of. */
export const RULES = {
  client: { name: "str:120", title: "str:160", city: "str:80", tier: "enum:Principal|Senior|Growth",
            since: "str:40", lifetime_inr: "int", focus: "str:400", brief: "str:2000",
            next_when: "date", next_what: "str:300", wants: "list:80:20", id: "id" },
  holding: { client_id: "id", artist_key: "str:120", artist_name: "str:120", work: "str:200",
             acquired: "str:40", paid_inr: "int" },
  log: { client_id: "id", happened: "date", channel: "str:40", note: "str:4000" },
  followup: { client_id: "id", due: "date", reason: "str:400", done: "bool" },
  watch: { artist_name: "str:120", upcoming: "bool", results: "bool", min_inr: "int", note: "str:300" },
};

function one(rule, v, field) {
  const [kind, arg, arg2] = rule.split(":");
  if (v == null || v === "") return null;
  switch (kind) {
    case "str": {
      if (typeof v !== "string" && typeof v !== "number") throw new BadRequest(`${field} must be text.`);
      const s = String(v).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim();
      return s.slice(0, +arg) || null;
    }
    case "date": {
      const s = String(v).slice(0, 10);
      if (!ISO_DATE.test(s) || isNaN(Date.parse(s))) throw new BadRequest(`${field} must be a date (YYYY-MM-DD).`);
      return s;
    }
    case "int": {
      const n = Number(v);
      if (!Number.isFinite(n) || n < 0 || n > 1e13) throw new BadRequest(`${field} must be a whole number of rupees.`);
      return Math.round(n);
    }
    case "bool": return v === true || v === 1 || v === "1" || v === "true";
    case "id": {
      const s = String(v);
      if (!ID.test(s)) throw new BadRequest(`${field} is not a valid id.`);
      return s;
    }
    case "enum": {
      const s = String(v);
      if (!arg.split("|").includes(s)) throw new BadRequest(`${field} must be one of ${arg.replace(/\|/g, ", ")}.`);
      return s;
    }
    case "list": {
      const items = Array.isArray(v) ? v : String(v).split(/[;,&]/);
      return items.map(x => String(x).trim().slice(0, +arg)).filter(Boolean).slice(0, +arg2);
    }
  }
  return null;
}

/** Keep only the fields the entity allows, each in its permitted shape. */
export function clean(entity, body) {
  const rules = RULES[entity];
  const out = {};
  for (const [field, rule] of Object.entries(rules)) {
    if (field in body) out[field] = one(rule, body[field], field);
  }
  return out;
}

/* ---- lockout ---------------------------------------------------------- */

const ip = request => request.headers.get("CF-Connecting-IP") || "unknown";

async function ensureTable(env) {
  await env.DB.prepare(
    "CREATE TABLE IF NOT EXISTS auth_fail (ip TEXT NOT NULL, at TEXT NOT NULL, locked_until TEXT)"
  ).run();
}

/** True when this address is currently locked out. */
export async function isLocked(request, env) {
  try {
    await ensureTable(env);
    const row = await env.DB.prepare(
      "SELECT MAX(locked_until) AS until FROM auth_fail WHERE ip = ?").bind(ip(request)).first();
    return !!(row && row.until && row.until > new Date().toISOString());
  } catch { return false; }                        // the lock must never lock the desk itself
}

/** Record a wrong key; lock the address on the tenth in fifteen minutes. */
export async function noteFailure(context, env) {
  const { request } = context;
  const addr = ip(request), nowIso = new Date().toISOString();
  try {
    await ensureTable(env);
    const since = new Date(Date.now() - WINDOW_MIN * 60e3).toISOString();
    await env.DB.prepare("INSERT INTO auth_fail (ip, at) VALUES (?, ?)").bind(addr, nowIso).run();
    const { n } = await env.DB.prepare(
      "SELECT COUNT(*) AS n FROM auth_fail WHERE ip = ? AND at > ?").bind(addr, since).first();
    if (n >= FAILS_TO_LOCK) {
      const until = new Date(Date.now() + LOCK_MIN * 60e3).toISOString();
      await env.DB.prepare("UPDATE auth_fail SET locked_until = ? WHERE ip = ?").bind(until, addr).run();
      context.waitUntil(tell(env, `AG Newsroom: ${n} wrong desk keys from one address in ${WINDOW_MIN} minutes — locked out for an hour. If that was not Aashna or you, the key may be worth rotating.`));
    }
    // Housekeeping: rows older than a day are useless.
    await env.DB.prepare("DELETE FROM auth_fail WHERE at < ?").bind(new Date(Date.now() - 864e5).toISOString()).run();
  } catch (e) {
    console.error("auth_fail", e && e.message);
  }
}

/** A Telegram line to the owner, if the bot is configured on this origin. */
export async function tell(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text }),
    });
  } catch {}
}

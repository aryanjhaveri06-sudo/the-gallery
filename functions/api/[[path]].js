/**
 * The Gallery — private API (Cloudflare Pages Functions).
 *
 * Served from the same origin as the app on purpose. Safari on iPad blocks
 * third-party cookies, so an API on a separate workers.dev domain would lose the
 * Access session on exactly the device this is built for.
 *
 * Everything under /api is behind Cloudflare Access and fails closed: if Access
 * is not configured, no request is served rather than the client book being
 * exposed by a misconfiguration.
 */

import { authenticate, unauthorised } from "../_lib/auth.js";
import { parseIcs } from "../_lib/ics.js";
import { liveNews } from "../_lib/news.js";

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      // A client book must never sit in a shared cache or a browser's disk cache.
      "Cache-Control": "no-store, private",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    },
  });

const now = () => new Date().toISOString();
const id = () => crypto.randomUUID();

async function audit(env, who, action, entity, entityId, before) {
  try {
    await env.DB.prepare(
      "INSERT INTO audit (at, who, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)"
    ).bind(now(), who, action, entity, entityId ?? null,
           before ? JSON.stringify(before) : null).run();
  } catch {
    /* auditing must never break the request it is recording */
  }
}

/* -------------------------------------------------- rows hanging off a client

   Holdings, log entries and follow-ups are all leaf rows owned by one client,
   so they edit and delete identically. The table and column names below are
   the ONLY thing interpolated into SQL, and they come from this frozen map via
   a regex-matched key — never from the request. Values always go through bind().
   ---------------------------------------------------------------------------- */
const CHILD = {
  holdings: {
    table: "holding", entity: "holding",
    editable: ["artist_key", "artist_name", "work", "acquired", "paid_inr"],
    numeric: ["paid_inr"],
    required: { artist_name: "A holding needs an artist." },
  },
  log: {
    table: "log", entity: "log",
    editable: ["happened", "channel", "note"],
    numeric: [],
    required: { happened: "A note needs a date." },
  },
  followups: {
    table: "followup", entity: "followup",
    editable: ["due", "reason"],          // `done` is a state change, handled apart
    numeric: [],
    required: { due: "A follow-up needs a date." },
  },
};

const numOrNull = v => {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(String(v).replace(/[^0-9.-]/g, ""));
  return Number.isFinite(n) ? Math.round(n) : null;
};

/* ------------------------------------------------------------------ clients */

async function listClients(env) {
  const { results } = await env.DB.prepare(
    `SELECT c.*,
            (SELECT COUNT(*) FROM holding h WHERE h.client_id = c.id) AS holdings,
            (SELECT COUNT(*) FROM followup f
              WHERE f.client_id = c.id AND f.done = 0) AS open_followups
       FROM client c
      ORDER BY CASE c.tier WHEN 'Principal' THEN 0 WHEN 'Senior' THEN 1 ELSE 2 END,
               c.name`
  ).all();
  return results.map(r => ({ ...r, wants: safeJson(r.wants) }));
}

async function getClient(env, cid) {
  const client = await env.DB.prepare("SELECT * FROM client WHERE id = ?").bind(cid).first();
  if (!client) return null;
  const [holdings, referrals, log, followups] = await Promise.all([
    env.DB.prepare("SELECT * FROM holding WHERE client_id = ? ORDER BY acquired DESC").bind(cid).all(),
    env.DB.prepare("SELECT * FROM referral WHERE client_id = ?").bind(cid).all(),
    env.DB.prepare("SELECT * FROM log WHERE client_id = ? ORDER BY happened DESC LIMIT 50").bind(cid).all(),
    env.DB.prepare("SELECT * FROM followup WHERE client_id = ? ORDER BY done, due").bind(cid).all(),
  ]);
  return {
    ...client,
    wants: safeJson(client.wants),
    holdings: holdings.results,
    referrals: referrals.results,
    log: log.results,
    followups: followups.results,
  };
}

function safeJson(v) {
  try { return v ? JSON.parse(v) : []; } catch { return []; }
}

/* ---------------------------------------------------------------- follow-ups */

async function dueFollowups(env) {
  const { results } = await env.DB.prepare(
    `SELECT f.*, c.name AS client_name, c.tier
       FROM followup f JOIN client c ON c.id = f.client_id
      WHERE f.done = 0
      ORDER BY f.due`
  ).all();
  const today = now().slice(0, 10);
  return results.map(f => ({
    ...f,
    overdue: f.due < today,
    age: f.due < today ? `overdue ${daysBetween(f.due, today)}d`
       : f.due === today ? "due today" : `due ${f.due}`,
  }));
}

function daysBetween(a, b) {
  return Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 86400000));
}

/* ------------------------------------------------------------------ calendar */

async function todaysDiary(env) {
  // The feed URL is a Worker secret. It is a bearer capability for her whole
  // calendar, so it is fetched here and never sent to the browser.
  const url = env.CALENDAR_ICS_URL;
  if (!url) return { configured: false, events: [] };

  const from = new Date(); from.setHours(0, 0, 0, 0);
  const to = new Date(from); to.setDate(to.getDate() + 7);

  const r = await fetch(url, { cf: { cacheTtl: 300, cacheEverything: true } });
  if (!r.ok) return { configured: true, error: `calendar feed returned ${r.status}`, events: [] };

  const events = parseIcs(await r.text(), from.getTime(), to.getTime());
  const todayIso = from.toISOString().slice(0, 10);
  return {
    configured: true,
    today: events.filter(e => e.date === todayIso),
    week: events,
  };
}

/* -------------------------------------------------------------------- router */

export async function onRequest(context) {
  const { request, env, params } = context;
  const path = "/" + (Array.isArray(params.path) ? params.path.join("/") : (params.path || ""));
  const method = request.method.toUpperCase();

  if (method === "OPTIONS") return new Response(null, { status: 204 });

  // Lets the app tell "wrong key" from "no desk here" without exposing anything.
  if (method === "GET" && path === "/health") {
    return json({ ok: true, guard: env.ACCESS_AUD ? "access" : (env.CRM_TOKEN ? "key" : "unconfigured") });
  }

  // News is public market information, not her book, so it sits BEFORE the auth
  // gate — the same reasoning as /health. It also must not need the D1 binding.
  if (method === "GET" && path === "/news") {
    try {
      const payload = await liveNews(context);
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          // The edge holds it for 15 minutes; let the browser hold it briefly
          // so a tab switch does not re-request on every render.
          "Cache-Control": "public, max-age=120",
          "X-Content-Type-Options": "nosniff",
        },
      });
    } catch (err) {
      console.error("news", err && err.message);
      return json({ error: "Could not reach the news feeds.", on_market: [] }, 502);
    }
  }

  if (!env.DB) {
    return json({ error: "The client book is not connected yet (no D1 binding)." }, 503);
  }

  const who = await authenticate(request, env);
  if (!who) return unauthorised();

  try {
    /* --- read ------------------------------------------------------------ */
    if (method === "GET" && path === "/me") {
      return json({ email: who.email, dev: !!who.dev });
    }
    if (method === "GET" && path === "/clients") {
      return json({ clients: await listClients(env) });
    }
    if (method === "GET" && path.startsWith("/clients/")) {
      const c = await getClient(env, path.slice("/clients/".length));
      return c ? json(c) : json({ error: "No such client." }, 404);
    }
    if (method === "GET" && path === "/followups") {
      return json({ followups: await dueFollowups(env) });
    }
    if (method === "GET" && path === "/diary") {
      return json(await todaysDiary(env));
    }
    if (method === "GET" && path === "/brief") {
      const [followups, diary] = await Promise.all([dueFollowups(env), todaysDiary(env)]);
      return json({ followups, diary, as_of: now() });
    }

    /* --- write ----------------------------------------------------------- */
    if (method === "POST" && path === "/clients") {
      const b = await request.json();
      if (!b.name) return json({ error: "A client needs a name." }, 400);
      const cid = b.id || id();
      await env.DB.prepare(
        `INSERT INTO client (id,name,title,city,tier,since,lifetime_inr,focus,brief,wants,
                             next_when,next_what,created_at,updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`
      ).bind(cid, b.name, b.title ?? null, b.city ?? null, b.tier ?? "Growth",
             b.since ?? null, b.lifetime_inr ?? null, b.focus ?? null, b.brief ?? null,
             JSON.stringify(b.wants || []), b.next_when ?? null, b.next_what ?? null,
             now(), now()).run();
      await audit(env, who.email, "create", "client", cid);
      return json({ id: cid }, 201);
    }

    if (method === "PATCH" && path.startsWith("/clients/")) {
      const cid = path.slice("/clients/".length);
      const b = await request.json();
      const allowed = ["name", "title", "city", "tier", "since", "lifetime_inr",
                       "focus", "brief", "next_when", "next_what"];
      const sets = [], vals = [];
      for (const k of allowed) if (k in b) { sets.push(`${k} = ?`); vals.push(b[k]); }
      if ("wants" in b) { sets.push("wants = ?"); vals.push(JSON.stringify(b.wants || [])); }
      if (!sets.length) return json({ error: "Nothing to update." }, 400);
      sets.push("updated_at = ?"); vals.push(now(), cid);
      await env.DB.prepare(`UPDATE client SET ${sets.join(", ")} WHERE id = ?`).bind(...vals).run();
      await audit(env, who.email, "update", "client", cid);
      return json({ ok: true });
    }

    if (method === "POST" && path === "/log") {
      const b = await request.json();
      if (!b.client_id) return json({ error: "A note needs a client." }, 400);
      const lid = id();
      await env.DB.prepare(
        "INSERT INTO log (id,client_id,happened,channel,note,created_at) VALUES (?,?,?,?,?,?)"
      ).bind(lid, b.client_id, b.happened || now().slice(0, 10),
             b.channel ?? null, b.note ?? null, now()).run();
      await audit(env, who.email, "create", "log", lid);
      return json({ id: lid }, 201);
    }

    if (method === "POST" && path === "/followups") {
      const b = await request.json();
      if (!b.client_id || !b.due) return json({ error: "A follow-up needs a client and a date." }, 400);
      const fid = id();
      await env.DB.prepare(
        "INSERT INTO followup (id,client_id,due,reason,done,created_at) VALUES (?,?,?,?,0,?)"
      ).bind(fid, b.client_id, b.due, b.reason ?? null, now()).run();
      await audit(env, who.email, "create", "followup", fid);
      return json({ id: fid }, 201);
    }

    if (method === "POST" && path.match(/^\/followups\/[^/]+\/done$/)) {
      const fid = path.split("/")[2];
      const b = await request.json().catch(() => ({}));
      const done = b.done === false ? 0 : 1;
      await env.DB.prepare("UPDATE followup SET done = ?, done_at = ? WHERE id = ?")
        .bind(done, done ? now() : null, fid).run();
      await audit(env, who.email, done ? "clear" : "reopen", "followup", fid);
      return json({ ok: true, done: !!done });
    }

    if (method === "POST" && path === "/holdings") {
      const b = await request.json();
      if (!b.client_id || !b.artist_name) return json({ error: "A holding needs a client and an artist." }, 400);
      const hid = id();
      await env.DB.prepare(
        "INSERT INTO holding (id,client_id,artist_key,artist_name,work,acquired,paid_inr) VALUES (?,?,?,?,?,?,?)"
      ).bind(hid, b.client_id, b.artist_key ?? null, b.artist_name,
             b.work ?? null, b.acquired ?? null, b.paid_inr ?? null).run();
      await audit(env, who.email, "create", "holding", hid);
      return json({ id: hid }, 201);
    }

    /* --- edit or remove one row under a client --------------------------- */
    const child = path.match(/^\/(holdings|log|followups)\/([^/]+)$/);
    if (child && (method === "PATCH" || method === "DELETE")) {
      const spec = CHILD[child[1]];
      const rowId = decodeURIComponent(child[2]);

      // Read it first: it decides 404 vs 200, and it is what goes in the audit
      // trail. A delete here is permanent, so the snapshot is the only record
      // of what the row said.
      const before = await env.DB.prepare(
        `SELECT * FROM ${spec.table} WHERE id = ?`).bind(rowId).first();
      if (!before) return json({ error: "No such record." }, 404);

      if (method === "DELETE") {
        await env.DB.prepare(`DELETE FROM ${spec.table} WHERE id = ?`).bind(rowId).run();
        await audit(env, who.email, "delete", spec.entity, rowId, before);
        return json({ ok: true, deleted: rowId });
      }

      const b = await request.json().catch(() => null);
      if (!b || typeof b !== "object") return json({ error: "Expected a JSON body." }, 400);

      // Refuse to blank a NOT NULL column rather than letting SQLite throw.
      for (const [field, message] of Object.entries(spec.required)) {
        if (field in b && !String(b[field] ?? "").trim()) return json({ error: message }, 400);
      }

      const sets = [], vals = [];
      for (const k of spec.editable) {
        if (!(k in b)) continue;                      // absent means "leave alone"
        sets.push(`${k} = ?`);
        vals.push(spec.numeric.includes(k) ? numOrNull(b[k])
                : (b[k] === "" ? null : b[k]));       // blank means cleared
      }
      // done and done_at move together, so the timestamp can never disagree
      // with the flag. POST /followups/:id/done stays as the one-tap route.
      if (spec.table === "followup" && "done" in b) {
        const done = b.done ? 1 : 0;
        sets.push("done = ?", "done_at = ?");
        vals.push(done, done ? now() : null);
      }
      if (!sets.length) return json({ error: "Nothing to update." }, 400);

      vals.push(rowId);
      await env.DB.prepare(
        `UPDATE ${spec.table} SET ${sets.join(", ")} WHERE id = ?`).bind(...vals).run();
      await audit(env, who.email, "update", spec.entity, rowId, before);
      return json({ ok: true, id: rowId });
    }

    return json({ error: `No route for ${method} /api${path}` }, 404);
  } catch (err) {
    // Never echo an exception to the client — it can carry schema detail.
    console.error("api error", path, err && err.message);
    return json({ error: "Something went wrong handling that." }, 500);
  }
}

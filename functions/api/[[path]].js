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
import { chat, aiConfigured, unsupportedFigures, AiError } from "../_lib/ai.js";
import { buildFacts, bookFacts, ASK_SYSTEM } from "../_lib/ask.js";

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

/* --------------------------------------------------------------- the drafter */

/**
 * What Mistral is allowed to know about a collector.
 *
 * §14 says send the minimum, and this is the one route that leaves the origin,
 * so the minimum is enforced here rather than trusted to the caller. Deliberately
 * absent: the surname, the Background field (it names firms), what she paid, and
 * the text of any logged conversation. A first name, a stated interest and a list
 * of artists is enough to write the letter; the rest is her book, not Mistral's.
 *
 * It matters because Mistral's free Experiment tier trains on API input unless
 * the Privacy toggle in the Admin Console is turned off. See DEPLOY.md.
 */
function pitchFacts(client, comparables) {
  const firstName = String(client.name || "").trim().split(/\s+/)[0] || "the collector";
  const wants = Array.isArray(client.wants) ? client.wants : [];
  const held = [...new Set((client.holdings || [])
    .map(h => String(h.artist_name || "").trim()).filter(Boolean))];
  const lastSeen = (client.log || [])[0];

  const lines = [
    `Collector first name: ${firstName}`,
    client.tier ? `Relationship: ${client.tier} client` : null,
    client.focus ? `What they collect: ${client.focus}` : null,
    wants.length ? `Still looking for: ${wants.join(", ")}` : null,
    held.length ? `Artists they already own: ${held.join(", ")}` : null,
    lastSeen && lastSeen.happened
      ? `Last spoke: ${lastSeen.happened}${lastSeen.channel ? ` (${lastSeen.channel})` : ""}`
      : "Last spoke: not recorded",
  ].filter(Boolean);

  if (comparables.length) {
    lines.push("", "Recent auction results, quote these exactly or not at all:");
    for (const c of comparables) {
      lines.push(`- ${c.artist}${c.title ? `, "${c.title}"` : ""}: sold ${c.price}`
        + `${c.house ? ` at ${c.house}` : ""}${c.above ? ", above its high estimate" : ""}`);
    }
  } else {
    lines.push("", "Recent auction results: none in the names they own.");
  }
  return lines.join("\n");
}

/** Trust nothing from the browser: cap the list, clip the strings. */
function cleanComparables(raw) {
  const s = (v, n) => v == null ? "" : String(v).replace(/\s+/g, " ").trim().slice(0, n);
  return (Array.isArray(raw) ? raw : []).slice(0, 6).map(c => ({
    artist: s(c.artist, 80),
    title: s(c.title, 120),
    price: s(c.price, 40),
    house: s(c.house, 40),
    above: !!c.above,
  })).filter(c => c.artist && c.price);
}

const PITCH_SYSTEM = [
  "You draft short notes for an Indian art advisor writing to a collector she knows.",
  "",
  "Absolute rules:",
  "- Use ONLY the facts in the FACTS block. Invent nothing.",
  "- Never write a price, percentage, valuation, estimate or date that is not in FACTS.",
  "  When you quote a figure from FACTS, copy it character for character.",
  "- Write any count as a word (three works, not 3 works). Do not number your lines.",
  "- If FACTS is thin, write a shorter note. Never pad it with market commentary.",
  "",
  "Style: plain British English, warm, unhurried, no sales language, no superlatives,",
  "no exclamation marks. One hundred and twenty to one hundred and eighty words.",
  "Plain prose only: no markdown, no headings, no subject line, no bullet characters.",
  "Open with 'Dear <first name>,' and close with 'Warm regards,' on its own line.",
  "Write nothing after that line — she signs it herself.",
].join("\n");

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
          // GitHub Pages is static and has no Functions, so the public site
          // cannot serve this itself — it calls across to here instead. Safe to
          // open up: these are public headlines, the endpoint takes no input and
          // sits before the auth gate, and no credentials are sent with it.
          "Access-Control-Allow-Origin": "*",
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

    /* --- drafting -------------------------------------------------------- */
    if (method === "GET" && path === "/ai/status") {
      return json({ configured: aiConfigured(env), model: env.MISTRAL_MODEL || null });
    }

    if (method === "POST" && path === "/ai/ask") {
      const b = await request.json().catch(() => ({}));
      const question = String(b.question || "").trim().slice(0, 1000);
      if (!question) return json({ error: "Ask something first." }, 400);

      // The book is hers to share or withhold, and the switch is on the page —
      // this route does not guess. Off means the roster never leaves the origin.
      const withBook = b.include_book !== false;

      let book = null, diary = null, news = null;
      const jobs = [];
      // Each source fails alone. One unreachable feed must not cost her the
      // answer — the facts block just says that part is missing.
      if (withBook) {
        jobs.push((async () => {
          const clients = await listClients(env);
          // The roster alone says nothing about holdings or conversations, and
          // those are most of what she asks about. One read per collector is
          // fine at thirty; it would not be at three thousand.
          const details = {};
          for (const c of clients.slice(0, 60)) details[c.id] = await getClient(env, c.id);
          book = bookFacts(clients, details, await dueFollowups(env));
        })().catch(e => { console.error("ask/book", e && e.message); book = null; }));
      }
      jobs.push((async () => { diary = await todaysDiary(env); })().catch(() => { diary = null; }));
      // The browser sends the stories already ranked (client-relevant first),
      // already aged out at a month, and already numbered for citation — it is
      // the only side that holds both the merged feed and the client interests
      // needed to rank against. The live layer is a fallback, shaped the same
      // way so the numbers still mean something.
      if (Array.isArray(b.news) && b.news.length) {
        news = b.news.slice(0, 24).map((r, i) => ({
          n: i + 1,
          date: String(r.date || "").slice(0, 10),
          source: String(r.source || "").slice(0, 60),
          headline: String(r.headline || "").slice(0, 240),
          url: String(r.url || "").slice(0, 500),
          clients: (Array.isArray(r.clients) ? r.clients : []).slice(0, 6).map(c => String(c).slice(0, 80)),
          artists: (Array.isArray(r.artists) ? r.artists : []).slice(0, 3).map(c => String(c).slice(0, 80)),
          age_days: Number.isFinite(+r.age_days) ? Math.max(0, Math.round(+r.age_days)) : 0,
        }));
      } else {
        jobs.push((async () => {
          const live = await liveNews(context);
          const today = Date.now();
          news = [...((live && live.on_market) || []), ...((live && live.wider) || [])]
            .filter(x => x && x.headline && x.date)
            .map(x => ({ date: x.date, source: x.source, headline: x.headline, url: x.url || "",
                         clients: [], artists: [],
                         age_days: Math.round((today - Date.parse(x.date + "T00:00:00")) / 86400000) }))
            .filter(x => x.age_days <= 31 && x.age_days >= -1)
            .slice(0, 24)
            .map((x, i) => ({ ...x, n: i + 1 }));
        })().catch(() => { news = null; }));
      }
      await Promise.all(jobs);

      const facts = buildFacts({
        today: now().slice(0, 10),
        book, diary, news,
        events: Array.isArray(b.events) ? b.events.slice(0, 20) : [],
        market: b.market || null,
      });

      // A little history so follow-up questions work, but not so much that an
      // old answer's wording starts standing in for the record.
      const history = (Array.isArray(b.history) ? b.history : []).slice(-6).map(m => ({
        role: m.role === "assistant" ? "assistant" : "user",
        content: String(m.content || "").slice(0, 1500),
      }));

      let out;
      try {
        out = await chat(env, {
          system: ASK_SYSTEM,
          user: `FACTS\n=====\n${facts}\n=====\n\n`
              + (history.length ? `Earlier in this conversation:\n`
                  + history.map(h => `${h.role === "user" ? "She" : "You"}: ${h.content}`).join("\n") + "\n\n" : "")
              + `Her question: ${question}`,
          maxTokens: 900,
          temperature: 0.2,
        });
      } catch (err) {
        if (err instanceof AiError) return json({ error: err.message, code: err.code }, err.status);
        console.error("ai/ask", err && err.name, err && err.message);
        return json({ error: "The assistant did not answer in time.", code: "timeout" }, 504);
      }

      // Flagged, not discarded — see the note at the top of _lib/ask.js.
      const unverified = unsupportedFigures(out.text, facts + " " + question);

      // The handoff shows source chips under every answer. They name the record
      // sets that were actually put in front of the model — not a claim about
      // which it used, but an honest account of what it could have used.
      const sources = [];
      if (book) sources.push("Client book");
      if (diary && diary.configured && !diary.error) sources.push("Her diary");
      if (Array.isArray(b.events) && b.events.length) sources.push("Sale calendar");
      if (Array.isArray(news) && news.length) sources.push("Headlines");
      if (b.market && ((b.market.lots || []).length || (b.market.artists || []).length)) sources.push("Auction results");

      await audit(env, who.email, "ask", "desk", null);
      return json({
        answer: out.text,
        model: out.model,
        unverified: unverified.slice(0, 8),
        used_book: withBook,
        sources,
        // The citation table, so the browser can turn [3] into a real anchor
        // from a URL that never went near the model.
        cited: (Array.isArray(news) ? news : []).map(r => ({
          n: r.n, headline: r.headline, source: r.source, date: r.date, url: r.url,
        })),
      });
    }

    if (method === "POST" && path === "/ai/pitch") {
      const b = await request.json().catch(() => ({}));
      if (!b.client_id) return json({ error: "A pitch needs a collector." }, 400);

      const client = await getClient(env, b.client_id);
      if (!client) return json({ error: "No such client." }, 404);

      const comparables = cleanComparables(b.comparables);
      const facts = pitchFacts(client, comparables);

      let out;
      try {
        out = await chat(env, {
          system: PITCH_SYSTEM,
          user: `FACTS\n-----\n${facts}\n-----\nWrite the note.`,
          maxTokens: 500,
          temperature: 0.35,
        });
      } catch (err) {
        if (err instanceof AiError) {
          return json({ error: err.message, code: err.code }, err.status);
        }
        // A timeout arrives as a DOMException, not an AiError.
        console.error("ai/pitch", err && err.name, err && err.message);
        return json({ error: "The drafter did not answer in time.", code: "timeout" }, 504);
      }

      // Prompting is not a control. If a figure in the draft is not in the facts
      // it came from the model, and this desk does not send a collector a number
      // it cannot trace — so the draft is thrown away and she keeps the template.
      const invented = unsupportedFigures(out.text, facts);
      if (invented.length) {
        console.warn("ai/pitch rejected, unsupported figures:", invented.join(","));
        return json({
          error: "The draft quoted figures that are not in the record, so it was discarded.",
          code: "ungrounded",
          figures: invented.slice(0, 6),
        }, 422);
      }

      await audit(env, who.email, "draft", "client", b.client_id);
      return json({
        draft: out.text,
        model: out.model,
        grounded: true,
        comparables: comparables.length,
      });
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

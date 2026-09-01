/**
 * Mistral, held at the edge.
 *
 * The key is a Pages secret (MISTRAL_API_KEY) and never leaves the Worker — the
 * browser asks this origin for a draft, not Mistral for a completion. Putting it
 * in the page would publish it: index.html is served from a public repo and from
 * GitHub Pages, where anyone can read it.
 *
 * Two rules shape everything below, both inherited from the desk:
 *
 *   1. §11 — never invent a market fact. A model that writes "up 34% since 2019"
 *      into a letter to a collector is worse than no model at all. So the prompt
 *      carries the facts, forbids any others, and `unsupportedFigures()` checks
 *      the answer against them afterwards. Prompting alone is not a control.
 *   2. The free tier is rate limited (roughly a request a minute, and Mistral no
 *      longer publishes the number). So nothing here runs on render. Every call
 *      is one deliberate button press, and 429 is a normal outcome to report,
 *      not an error to retry into.
 */

/* Overridable so the route can be driven end to end against a local stub —
   `wrangler pages dev --binding MISTRAL_BASE_URL=http://127.0.0.1:8799/v1`.
   It is a dev affordance only; nothing sets it in production. */
const API_DEFAULT = "https://api.mistral.ai/v1";
const base = env => (env && env.MISTRAL_BASE_URL) || API_DEFAULT;

/* Mistral renames its models often — the ids moved twice between the versioned
   `mistral-small-2503` scheme and `mistral-small-4-0-26-03`. So the id is a
   variable with a fallback: if the configured one is rejected, ask the account
   what it actually has and pick the smallest sensible model. Cached per isolate
   so the discovery costs one request, not one per draft. */
const DEFAULT_MODEL = "mistral-small-latest";
let resolvedModel = null;

/** Small and cheap first: the free tier's budget is tokens per month. */
const FAMILIES = ["ministral", "mistral-small", "open-mistral-nemo", "mistral-medium"];

export function aiConfigured(env) {
  return !!(env && env.MISTRAL_API_KEY);
}

function authHeaders(env) {
  return {
    "Content-Type": "application/json",
    Accept: "application/json",
    Authorization: `Bearer ${env.MISTRAL_API_KEY}`,
  };
}

/** An error the route can turn into an honest message. Never carries the key. */
export class AiError extends Error {
  constructor(code, message, status = 502) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function listModels(env) {
  const r = await fetch(`${base(env)}/models`, {
    headers: authHeaders(env),
    signal: AbortSignal.timeout(10000),
  });
  if (!r.ok) throw new AiError("models", `Mistral would not list models (${r.status})`);
  const body = await r.json();
  return (body.data || []).map(m => m.id).filter(Boolean);
}

async function pickModel(env) {
  const ids = await listModels(env);
  for (const family of FAMILIES) {
    // Dates sort usefully in every scheme Mistral has used, so the last match wins.
    const hits = ids.filter(id => id.startsWith(family)).sort();
    if (hits.length) return hits[hits.length - 1];
  }
  if (ids.length) return ids.sort()[0];
  throw new AiError("no_model", "This key can reach no models.");
}

async function post(env, model, body) {
  return fetch(`${base(env)}/chat/completions`, {
    method: "POST",
    headers: authHeaders(env),
    body: JSON.stringify({ ...body, model }),
    // A collector letter is not worth a hung tab. Pages Functions cap at 30s.
    signal: AbortSignal.timeout(25000),
  });
}

/**
 * One completion. Returns { text, model }.
 * Throws AiError for everything the caller should say out loud.
 */
export async function chat(env, { system, user, maxTokens = 700, temperature = 0.3 }) {
  if (!aiConfigured(env)) {
    throw new AiError("unconfigured", "No Mistral key is set on this desk.", 503);
  }

  const body = {
    messages: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
    max_tokens: maxTokens,
    temperature,
  };

  let model = resolvedModel || env.MISTRAL_MODEL || DEFAULT_MODEL;
  let r = await post(env, model, body);

  // A rejected model id looks like a 400 or a 422, and reads the same as a bad
  // request otherwise — so only retry once, and only after discovery succeeds.
  if ((r.status === 400 || r.status === 404 || r.status === 422) && !resolvedModel) {
    const detail = await r.text().catch(() => "");
    if (/model/i.test(detail)) {
      model = await pickModel(env);
      resolvedModel = model;
      r = await post(env, model, body);
    } else {
      throw new AiError("request", "Mistral rejected the request.");
    }
  }

  if (r.status === 401 || r.status === 403) {
    throw new AiError("key", "Mistral refused the key — it may be revoked or wrong.", 502);
  }
  if (r.status === 429) {
    throw new AiError("rate", "Mistral's free tier is rate limited. Give it a minute.", 429);
  }
  if (!r.ok) {
    throw new AiError("upstream", `Mistral answered ${r.status}.`);
  }

  const out = await r.json();
  const text = (out.choices && out.choices[0] && out.choices[0].message
    && out.choices[0].message.content || "").trim();
  if (!text) throw new AiError("empty", "Mistral returned nothing.");

  resolvedModel = model;   // it worked; stop paying for discovery
  return { text, model, usage: out.usage || null };
}

/* ---------------------------------------------------------------------------
 * The grounding check.
 *
 * Everything the desk knows arrives as a formatted string ("₹22.5 lakh",
 * "2019"). So every run of digits in the answer must already appear in the
 * facts. A model that quotes a real comparable passes; one that rounds ₹22.5
 * lakh to "about ₹23 lakh", or invents a percentage, does not — and a draft
 * that fails is discarded rather than shown, because she sends these to named
 * collectors.
 *
 * Deliberately strict. A false rejection costs a fallback to the template she
 * already has; a false pass costs a wrong number in a letter to a buyer.
 * ------------------------------------------------------------------------- */
export function unsupportedFigures(text, facts) {
  const runs = s => new Set((String(s).replace(/[, ]/g, "").match(/\d+/g) || []));
  const allowed = runs(facts);
  allowed.add(String(new Date().getUTCFullYear()));   // "in 2026" is not a claim
  const bad = [];
  for (const n of runs(text)) {
    if (!allowed.has(n)) bad.push(n);
  }
  return bad;
}

/**
 * Cloudflare Access verification.
 *
 * Access puts a signed JWT on every request that gets through it. The lazy check
 * is to read the email header and trust it — but headers are trivially forged if
 * anyone ever reaches the Worker by a route that bypasses Access, so this
 * verifies the RS256 signature against the team's published keys, and checks
 * audience, issuer and expiry.
 *
 * Nothing here handles a password. Cloudflare runs the login (email one-time
 * code); we only confirm who it says arrived.
 */

const CERTS_TTL_MS = 60 * 60 * 1000;    // keys rotate rarely; an hour is plenty
let certsCache = { url: null, at: 0, keys: null };

function b64urlToBytes(s) {
  const pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function decodeSegment(seg) {
  return JSON.parse(new TextDecoder().decode(b64urlToBytes(seg)));
}

async function getKeys(teamDomain) {
  const url = `https://${teamDomain}/cdn-cgi/access/certs`;
  const fresh = certsCache.url === url && Date.now() - certsCache.at < CERTS_TTL_MS;
  if (fresh && certsCache.keys) return certsCache.keys;

  const r = await fetch(url);
  if (!r.ok) throw new Error(`Access certs unavailable (${r.status})`);
  const { keys } = await r.json();

  const imported = {};
  for (const jwk of keys || []) {
    imported[jwk.kid] = await crypto.subtle.importKey(
      "jwk",
      { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["verify"]
    );
  }
  certsCache = { url, at: Date.now(), keys: imported };
  return imported;
}

/**
 * @returns {Promise<{email: string} | null>} the verified identity, or null.
 */
export async function verifyAccess(request, env) {
  // Local development only, and only when explicitly switched on. Never set
  // CRM_DEV_IDENTITY in the deployed environment.
  if (env.CRM_DEV_IDENTITY) {
    return { email: env.CRM_DEV_IDENTITY, dev: true };
  }

  const team = env.ACCESS_TEAM_DOMAIN;   // e.g. yourteam.cloudflareaccess.com
  const aud = env.ACCESS_AUD;            // the Access application's AUD tag
  if (!team || !aud) return null;        // not configured: fail closed

  const token =
    request.headers.get("Cf-Access-Jwt-Assertion") ||
    (request.headers.get("Cookie") || "").match(/CF_Authorization=([^;]+)/)?.[1];
  if (!token) return null;

  const parts = token.split(".");
  if (parts.length !== 3) return null;

  let header, payload;
  try {
    header = decodeSegment(parts[0]);
    payload = decodeSegment(parts[1]);
  } catch {
    return null;
  }

  if (header.alg !== "RS256") return null;

  const keys = await getKeys(team);
  const key = keys[header.kid];
  if (!key) return null;

  const ok = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64urlToBytes(parts[2]),
    new TextEncoder().encode(`${parts[0]}.${parts[1]}`)
  );
  if (!ok) return null;

  const now = Math.floor(Date.now() / 1000);
  if (payload.exp && payload.exp < now) return null;
  if (payload.nbf && payload.nbf > now) return null;
  if (payload.iss && payload.iss !== `https://${team}`) return null;

  const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!audiences.includes(aud)) return null;

  // Access already enforces the policy; the allowlist is a second lock, so a
  // mis-scoped policy alone cannot open the client book.
  const allowed = (env.ALLOWED_EMAILS || "")
    .split(",").map(s => s.trim().toLowerCase()).filter(Boolean);
  const email = (payload.email || "").toLowerCase();
  if (allowed.length && !allowed.includes(email)) return null;

  return { email };
}

export function unauthorised(message = "Sign in to reach the client book.") {
  return new Response(JSON.stringify({ error: message }), {
    status: 401,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/* ---------------------------------------------------------------------------
 * Shared-key authentication.
 *
 * The alternative to Cloudflare Access, chosen because Access requires a card on
 * file even on its free tier. One long random key, held as a Worker secret and
 * entered once per device.
 *
 * It is honestly weaker than Access: a single shared secret, no per-person
 * identity, and revoking means rotating the key for everyone. It is reasonable
 * for a two-person desk over HTTPS with a 256-bit key, and writes are still
 * audited — just against the device rather than a verified person.
 * ------------------------------------------------------------------------- */

/** Constant-time compare, so a wrong key cannot be found one character at a time. */
function safeEqual(a, b) {
  const ab = new TextEncoder().encode(a || "");
  const bb = new TextEncoder().encode(b || "");
  // Compare a fixed number of bytes regardless of input, then fold in the length.
  let diff = ab.length ^ bb.length;
  const n = Math.max(ab.length, bb.length, 1);
  for (let i = 0; i < n; i++) diff |= (ab[i] || 0) ^ (bb[i] || 0);
  return diff === 0;
}

export function verifyKey(request, env) {
  const expected = env.CRM_TOKEN;
  if (!expected) return null;                    // not configured: fail closed

  const header = request.headers.get("Authorization") || "";
  const presented = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!presented) return null;
  if (!safeEqual(presented, expected)) return null;

  return { email: env.DESK_LABEL || "the desk", viaKey: true };
}

/**
 * Whichever guard is configured. Access wins when it is set up, so moving to it
 * later is a matter of filling in the variables — no code change.
 */
export async function authenticate(request, env) {
  if (env.CRM_DEV_IDENTITY) return { email: env.CRM_DEV_IDENTITY, dev: true };
  if (env.ACCESS_TEAM_DOMAIN && env.ACCESS_AUD) return verifyAccess(request, env);
  return verifyKey(request, env);
}

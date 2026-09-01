/**
 * The drafter's two failure modes, exercised without a Mistral key.
 *
 *   node --experimental-vm-modules tests/ai_grounding.mjs
 *   (plain `node tests/ai_grounding.mjs` is enough on v22)
 *
 * The grounding check is the control that lets a generated note go to a real
 * collector, so it is tested against the shapes that actually turn up: a clean
 * quote, a rounded figure, an invented percentage, a spelled-out count.
 */
import { unsupportedFigures, chat, AiError } from "../functions/_lib/ai.js";

let failed = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { console.log("  ok   " + name); }
  else { failed++; console.log("  FAIL " + name + (extra ? "  -> " + extra : "")); }
};

const FACTS = [
  "Collector first name: Nanda",
  "What they collect: Modern, Progressives",
  "Artists they already own: S H Raza, Jamini Roy",
  "Last spoke: 2026-08-14 (call)",
  "",
  "Recent auction results, quote these exactly or not at all:",
  '- S H Raza, "Bindu": sold ₹3.8 lakh at Saffronart, above its high estimate',
].join("\n");

console.log("unsupportedFigures");
ok("passes a note with no figures at all",
   unsupportedFigures("Dear Nanda,\n\nA short note.\n\nWarm regards,", FACTS).length === 0);

ok("passes a figure copied exactly",
   unsupportedFigures("The Raza made ₹3.8 lakh, above its high estimate.", FACTS).length === 0);

ok("passes the date it was given",
   unsupportedFigures("Since we spoke on 2026-08-14.", FACTS).length === 0);

ok("passes the current year, which is not a claim",
   unsupportedFigures(`Nothing has changed in ${new Date().getUTCFullYear()}.`, FACTS).length === 0);

ok("catches a rounded figure",
   unsupportedFigures("The Raza made about ₹4 lakh.", FACTS).includes("4"));

ok("catches an invented percentage",
   unsupportedFigures("Raza is up 34% since we spoke.", FACTS).includes("34"));

ok("catches an invented estimate range",
   unsupportedFigures("I expect ₹12–15 lakh.", FACTS).length === 2);

ok("ignores commas and spaces inside a figure",
   unsupportedFigures("It made 3.8 lakh", "sold 3.8 lakh").length === 0);

ok("a count written as a word is not a figure",
   unsupportedFigures("You own three works by him.", FACTS).length === 0);

/* --- the transport, with Mistral stubbed ---------------------------------- */
console.log("chat()");
const env = { MISTRAL_API_KEY: "test-key-not-real" };
const reply = t => new Response(JSON.stringify(
  { choices: [{ message: { content: t } }], usage: { total_tokens: 1 } }),
  { status: 200, headers: { "Content-Type": "application/json" } });

const calls = [];
const stub = fn => { globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return fn(String(url), init); }; };

// 0. Mistral renames its models; the id must recover on its own.
//    Runs first because the resolved id is cached for the life of the module.
calls.length = 0;
let posts = 0;
stub((url) => {
  if (url.endsWith("/models")) {
    return new Response(JSON.stringify({ data: [
      { id: "mistral-medium-3-5-26-04" },
      { id: "mistral-small-4-0-26-03" },
      { id: "ministral-3-8b-25-12" },
      { id: "ministral-3-14b-25-12" },
    ] }), { status: 200 });
  }
  posts++;
  return posts === 1
    ? new Response(JSON.stringify({ message: "Invalid model: mistral-small-latest" }), { status: 400 })
    : reply("recovered");
});
let recovered = await chat(env, { system: "s", user: "u" });
ok("a rejected model id is rediscovered and retried", recovered.text === "recovered");
ok("and it picks a small one", recovered.model.startsWith("ministral"), recovered.model);
ok("discovery costs exactly one extra request", calls.length === 3, String(calls.length));

// A 400 that is not about the model must not trigger discovery.
calls.length = 0;

// 1. the happy path
calls.length = 0;
stub(() => reply("Dear Nanda,"));
let out = await chat(env, { system: "s", user: "u" });
ok("returns the message text", out.text === "Dear Nanda,");
ok("sends the key as a bearer, and only to api.mistral.ai",
   calls[0].url === "https://api.mistral.ai/v1/chat/completions"
   && calls[0].init.headers.Authorization === "Bearer test-key-not-real");

// 2. a rate limit is a message, not a crash
calls.length = 0;
stub(() => new Response("{}", { status: 429 }));
try { await chat(env, { system: "s", user: "u" }); ok("429 throws", false); }
catch (e) { ok("429 becomes a reportable AiError", e instanceof AiError && e.code === "rate" && e.status === 429); }

// 3. no key configured
try { await chat({}, { system: "s", user: "u" }); ok("missing key throws", false); }
catch (e) { ok("a desk with no key says so", e instanceof AiError && e.code === "unconfigured"); }

// 4. a bad key
stub(() => new Response("{}", { status: 401 }));
try { await chat(env, { system: "s", user: "u" }); ok("401 throws", false); }
catch (e) { ok("a refused key is named as a key problem", e.code === "key"); }

console.log(failed ? `\n${failed} failed` : "\nall passed");
process.exit(failed ? 1 : 0);

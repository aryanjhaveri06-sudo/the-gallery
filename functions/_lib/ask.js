/**
 * Ask AG — one question, one answer, over the desk's own records.
 *
 * Deliberately NOT a tool-calling loop. Mistral's free tier is rate limited to
 * roughly a request a minute, so an agent that thinks in four round-trips would
 * spend four minutes answering "what's on this week". Instead the retrieval is
 * deterministic and generous: the Worker assembles everything the desk knows
 * that could bear on the question, and spends its one call on the answer.
 *
 * The facts bundle is the whole contract. If something is not in it, the answer
 * must not contain it — and `unsupportedFigures()` checks that afterwards.
 */

/* Chat and drafting are graded differently, on purpose.
 *
 * A pitch gets SENT to a named collector, so an ungrounded figure is discarded
 * and she keeps the template. An answer here is read by her, at her desk, with
 * the records beside it — so an ungrounded figure is FLAGGED rather than
 * swallowed. Throwing away a useful answer because it said "about three lakh"
 * would teach her to stop asking; showing it with the number called out keeps
 * her in the loop. Same check, different consequence. */

const money = n => {
  if (n == null || n === "") return null;
  const v = Number(n);
  if (!Number.isFinite(v)) return null;
  // Half-up on the integer rupee value, matching the app's other formatter.
  if (v >= 1e7) return `₹${Math.round(v / 1e5) / 100} cr`;
  if (v >= 1e5) return `₹${Math.round(v / 1e3) / 100} lakh`;
  return `₹${v.toLocaleString("en-IN")}`;
};

const clip = (v, n) => v == null ? "" : String(v).replace(/\s+/g, " ").trim().slice(0, n);

/**
 * Her book, flattened for reading.
 *
 * Included in full when she asks for it, because a desk assistant that cannot
 * name her collectors is a search box with extra steps. What she paid is in
 * here too: "who are my biggest buyers" is a fair question and withholding the
 * number makes the answer wrong rather than safe. The switch is hers, on the
 * page, not a guess made here — see `include_book`.
 */
function bookFacts(clients, details, followups) {
  const lines = [`Her client book holds ${clients.length} collectors.`];
  for (const c of clients) {
    const d = details[c.id] || {};
    const bits = [`${c.name} (${c.tier || "Growth"})`];
    if (c.title) bits.push(clip(c.title, 90));
    if (c.focus) bits.push(`collects ${clip(c.focus, 90)}`);
    const wants = Array.isArray(c.wants) ? c.wants : [];
    if (wants.length) bits.push(`looking for ${wants.join(", ")}`);
    const held = (d.holdings || []).map(h =>
      `${h.artist_name}${h.work ? ` "${clip(h.work, 60)}"` : ""}`
      + `${h.acquired ? ` acquired ${h.acquired}` : ""}`
      + `${money(h.paid_inr) ? ` for ${money(h.paid_inr)}` : ""}`);
    if (held.length) bits.push(`owns ${held.join("; ")}`);
    else bits.push("owns nothing recorded (a prospect)");
    const last = (d.log || [])[0];
    if (last) bits.push(`last contact ${last.happened}${last.channel ? ` by ${last.channel}` : ""}`
      + `${last.note ? `: ${clip(last.note, 140)}` : ""}`);
    lines.push("- " + bits.join(". ") + ".");
  }
  if (followups.length) {
    lines.push("", `Open follow-ups, ${followups.length} of them:`);
    for (const f of followups) {
      lines.push(`- ${f.client_name}, ${f.age}${f.reason ? `: ${clip(f.reason, 120)}` : ""}`);
    }
  } else {
    lines.push("", "No follow-ups are open.");
  }
  return lines.join("\n");
}

/** Her diary, plus the sale calendar the browser sent. */
function diaryFacts(diary, events) {
  const lines = [];
  if (!diary || !diary.configured) {
    lines.push("Her personal calendar is NOT connected to this desk, so nothing here is her diary — only the auction calendar below. Say so if asked about her own meetings.");
  } else if (diary.error) {
    lines.push(`Her calendar feed could not be read (${clip(diary.error, 80)}).`);
  } else {
    const week = diary.week || [];
    lines.push(week.length ? `Her diary, next seven days:` : "Her diary is empty for the next seven days.");
    for (const e of week.slice(0, 25)) {
      lines.push(`- ${e.date}${e.time ? ` ${e.time}` : ""}: ${clip(e.title || e.summary, 120)}`);
    }
  }
  lines.push("");
  if (events && events.length) {
    lines.push("Forthcoming auctions and fairs:");
    for (const e of events.slice(0, 20)) {
      lines.push(`- ${e.starts}${e.ends && e.ends !== e.starts ? ` to ${e.ends}` : ""}: ${clip(e.title, 110)}`
        + `${e.house ? ` (${clip(e.house, 40)})` : ""}${e.kind ? `, ${clip(e.kind, 30)}` : ""}`
        + `${e.city ? `, ${clip(e.city, 30)}` : ""}${e.lot_count ? `, ${e.lot_count} lots` : ""}`);
    }
  } else {
    lines.push("No forthcoming sales are on the calendar.");
  }
  return lines.join("\n");
}

function newsFacts(news) {
  const rows = [...((news && news.on_market) || []), ...((news && news.wider) || [])].slice(0, 24);
  if (!rows.length) return "No headlines were available.";
  return ["Headlines on the desk right now:",
    ...rows.map(n => `- ${n.date || "undated"} ${clip(n.source, 40)}: ${clip(n.headline || n.title, 160)}`)
  ].join("\n");
}

/** The market slice the browser sent — it already holds app_data, the Worker does not. */
function marketFacts(m) {
  if (!m) return "No market data was supplied with this question.";
  const lines = [];
  if (m.coverage) {
    lines.push(`Coverage: ${m.coverage.lots} lots across ${clip((m.coverage.houses || []).join(", "), 120)}, `
      + `${m.coverage.artists} artists tracked. Data generated ${clip(m.generated_at, 30)}.`);
  }
  if (m.artists && m.artists.length) {
    lines.push("", "Artists relevant to this question:");
    for (const a of m.artists.slice(0, 8)) {
      lines.push(`- ${a.name}: ${a.lots} lots recorded`
        + `${a.median ? `, median ${a.median}` : ""}${a.high ? `, highest ${a.high}` : ""}`
        + `${a.move ? `, twelve-month move ${a.move}` : ""}.`);
    }
  }
  if (m.lots && m.lots.length) {
    lines.push("", "Auction results (quote these exactly or not at all):");
    for (const l of m.lots.slice(0, 25)) {
      lines.push(`- ${l.date} ${clip(l.house, 30)}: ${clip(l.artist, 70)}`
        + `${l.title ? `, "${clip(l.title, 80)}"` : ""} sold ${clip(l.price, 30)}`
        + `${l.est ? ` against an estimate of ${clip(l.est, 40)}` : ""}`
        + `${l.above ? ", above the high estimate" : ""}.`);
    }
  }
  return lines.join("\n") || "No market data was supplied with this question.";
}

/**
 * Everything the answer is allowed to draw on, as one block.
 * Returned as a string because that is also what the figure check reads.
 */
/* Rule 4: sell-through is in the data and is NOT derivable — it reads ~100% and
   is false. It must never reach the model, which would quote it as fact. The
   browser does not send it; this is the second lock. */
const BANNED = /sell[_ -]?through/i;
function assertNoBannedStats(market) {
  if (market && BANNED.test(JSON.stringify(market))) {
    throw new Error("sell-through must never be sent to the model");
  }
}

export function buildFacts({ today, book, diary, events, news, market }) {
  assertNoBannedStats(market);
  const parts = [
    `Today is ${today}.`,
    "",
    "=== THE MARKET ===",
    marketFacts(market),
    "",
    "=== THE CALENDAR ===",
    diaryFacts(diary, events),
    "",
    "=== THE NEWS ===",
    newsFacts(news),
    "",
    "=== THE CLIENT BOOK ===",
    book || "She has switched the client book off for this question. Say so rather than guessing, if she asks about a collector.",
  ];
  return parts.join("\n");
}

export { bookFacts };

export const ASK_SYSTEM = [
  "You are the assistant on a private art sales desk. The desk belongs to an",
  "advisor who sells Indian modern and contemporary art. She is asking you about",
  "her own records.",
  "",
  "Absolute rules:",
  "- Everything you know is in the FACTS block. Use nothing else. You have no",
  "  knowledge of the art market beyond it.",
  "- Never state a price, estimate, percentage, count or date that is not in",
  "  FACTS. Copy figures character for character.",
  "- Do not add up, average, or otherwise compute new numbers. If she asks for a",
  "  total that FACTS does not contain, say it is not recorded and list what is.",
  "- Write small counts as words (three clients, not 3 clients).",
  "- If FACTS does not answer the question, say so plainly and say what is",
  "  missing. Never fill a gap with something plausible. A wrong price here goes",
  "  to a collector.",
  "",
  "Style: brief and direct, British English. She is at her desk and wants the",
  "answer, not an essay. Lead with it. Use short prose or a simple dash list; no",
  "markdown headings, no bold, no tables. Name people and works exactly as FACTS",
  "spells them. Two hundred words is usually too many.",
].join("\n");

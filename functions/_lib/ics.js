/**
 * A small iCalendar reader, enough for a personal diary feed.
 *
 * This is the inbound direction: her own calendar, fetched server-side so the
 * secret feed URL never reaches the browser and CORS never applies. Only the
 * fields the brief shows are kept, and nothing is stored.
 *
 * Recurrence is handled for the common DAILY/WEEKLY/MONTHLY cases with COUNT or
 * UNTIL, which covers a standing weekly viewing or a monthly board meeting.
 * Anything more exotic is shown on its first occurrence only rather than being
 * silently dropped.
 */

/** RFC 5545 folds long lines by starting the continuation with a space or tab. */
function unfold(text) {
  return text.replace(/\r\n/g, "\n").replace(/\n[ \t]/g, "");
}

function unescape(v) {
  return (v || "")
    .replace(/\\n/gi, "\n").replace(/\\,/g, ",")
    .replace(/\\;/g, ";").replace(/\\\\/g, "\\");
}

/** "20260827T103000Z" | "20260827" -> {date: 'YYYY-MM-DD', allDay: bool, ms} */
function parseWhen(value, params) {
  const v = (value || "").trim();
  const allDay = /VALUE=DATE(?!-TIME)/i.test(params || "") || /^\d{8}$/.test(v);
  const m = v.match(/^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$/);
  if (!m) return null;
  const [, y, mo, d, hh = "00", mm = "00", ss = "00", z] = m;
  const ms = z
    ? Date.UTC(+y, +mo - 1, +d, +hh, +mm, +ss)
    : new Date(+y, +mo - 1, +d, +hh, +mm, +ss).getTime();
  return { date: `${y}-${mo}-${d}`, allDay, ms, hhmm: `${hh}:${mm}` };
}

function parseRrule(v) {
  const out = {};
  for (const part of (v || "").split(";")) {
    const [k, val] = part.split("=");
    if (k) out[k.toUpperCase()] = val;
  }
  return out;
}

/** Expand a recurring event across [fromMs, toMs]. Bounded, so it cannot spin. */
function expand(ev, fromMs, toMs) {
  if (!ev.rrule) return [ev];
  const r = parseRrule(ev.rrule);
  const freq = (r.FREQ || "").toUpperCase();
  const step = { DAILY: 1, WEEKLY: 7 }[freq];
  if (!step && freq !== "MONTHLY") return [ev];

  const interval = Math.max(1, parseInt(r.INTERVAL || "1", 10));
  const untilMs = r.UNTIL ? parseWhen(r.UNTIL, "")?.ms ?? toMs : toMs;
  const count = r.COUNT ? parseInt(r.COUNT, 10) : Infinity;

  const out = [];
  let cursor = new Date(ev.startMs);
  for (let i = 0; i < 400 && out.length < count; i++) {
    const ms = cursor.getTime();
    if (ms > toMs || ms > untilMs) break;
    if (ms >= fromMs) {
      const d = new Date(ms);
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      out.push({ ...ev, date: iso, startMs: ms });
    }
    if (freq === "MONTHLY") cursor.setMonth(cursor.getMonth() + interval);
    else cursor.setDate(cursor.getDate() + step * interval);
  }
  return out;
}

/**
 * @param {string} text  raw .ics
 * @param {number} fromMs, toMs  window to return
 */
export function parseIcs(text, fromMs, toMs) {
  const lines = unfold(text).split("\n");
  const events = [];
  let cur = null;

  for (const raw of lines) {
    const line = raw.trim();
    if (line === "BEGIN:VEVENT") { cur = {}; continue; }
    if (line === "END:VEVENT") {
      if (cur && cur.startMs != null) events.push(cur);
      cur = null;
      continue;
    }
    if (!cur) continue;

    const idx = line.indexOf(":");
    if (idx < 0) continue;
    const left = line.slice(0, idx);
    const value = line.slice(idx + 1);
    const [name, ...paramParts] = left.split(";");
    const params = paramParts.join(";");
    const key = name.toUpperCase();

    if (key === "SUMMARY") cur.title = unescape(value);
    else if (key === "LOCATION") cur.location = unescape(value);
    else if (key === "UID") cur.uid = value;
    else if (key === "STATUS") cur.status = value.toUpperCase();
    else if (key === "TRANSP") cur.transp = value.toUpperCase();
    else if (key === "RRULE") cur.rrule = value;
    else if (key === "DTSTART") {
      const w = parseWhen(value, params);
      if (w) { cur.date = w.date; cur.allDay = w.allDay; cur.startMs = w.ms; cur.time = w.allDay ? null : w.hhmm; }
    } else if (key === "DTEND") {
      const w = parseWhen(value, params);
      if (w) cur.endMs = w.ms;
    }
  }

  const out = [];
  for (const ev of events) {
    if (ev.status === "CANCELLED") continue;
    for (const inst of expand(ev, fromMs, toMs)) {
      if (inst.startMs >= fromMs && inst.startMs <= toMs) {
        out.push({
          title: inst.title || "(no title)",
          date: inst.date,
          time: inst.time,
          allDay: !!inst.allDay,
          location: inst.location || null,
        });
      }
    }
  }
  out.sort((a, b) => (a.date + (a.time || "")).localeCompare(b.date + (b.time || "")));
  return out;
}

/* Render matrix for index.html. Paste into the browser console on the app.
 *
 *     node --check  only proves the file parses. Every runtime bug this desk has
 *     shipped lived on an axis that syntax checking cannot see:
 *
 *       viewport    renderRail() only runs when isPad, so a dangling reference
 *                   in a nav badge blanked every iPad and no phone.
 *       auth state  viewClient walked off the end of a null record when the book
 *                   was offline.
 *       data shape  c.holdings[0].artist threw for every client who owns nothing
 *                   — which is every prospect.
 *       schema      a missing REAL.coverage white-screened five tabs.
 *
 * IMPORTANT: render() wraps each section in guard(), so a failure becomes a
 * console.error rather than a throw, and stale content stays in #main. A harness
 * that only try/catches and only checks for emptiness reports everything green.
 * This one listens on console.error and clears #main first. It has been verified
 * to go red — if it never fails, distrust it before you trust the code.
 */
(() => {
  const failures = [];
  const main = document.getElementById('main');
  const savedPad = isPad, savedSplit = isSplit, savedS = Object.assign({}, S);
  const savedREAL = JSON.parse(JSON.stringify(REAL));
  const savedCRM = { live: CRM.live, needsKey: CRM.needsKey, clients: CRM.clients,
                     detail: CRM.detail, detailBusy: CRM.detailBusy,
                     detailError: CRM.detailError, activity: CRM.activity,
                     followups: CRM.followups, diary: CRM.diary };
  // render() now asks for a missing client record while it paints. That is the
  // whole point of the fix, but a harness that let it run would put ~1700
  // requests on the wire and repaint asynchronously in the middle of its own
  // measurements. Stubbed here; the three states it can leave the card in
  // (loading / failed / nothing chosen) are driven directly below instead.
  const realEnsure = ensureDetail;
  ensureDetail = () => Promise.resolve(null);
  const realErr = console.error;
  let caught = [];
  console.error = (...a) => { caught.push(a.map(String).join(' ')); };

  const attempt = (label, fn) => {
    caught = [];
    main.innerHTML = '';
    try { fn(); } catch (e) { failures.push(`THREW ${label} :: ${e.message}`); return; }
    if (caught.length) failures.push(`GUARD ${label} :: ${caught[0].slice(0, 120)}`);
    else if (main.innerHTML.length < 40) failures.push(`EMPTY ${label}`);
  };

  const CID = 'probe';
  const full = { id: CID, name: 'Probe', title: 'Founder', city: 'Mumbai', tier: 'Principal',
    since: '2019', lifetime_inr: 25000000, focus: 'Moderns', brief: 'B.', wants: ['Gaitonde'],
    next_when: 'Now', next_what: 'Call',
    holdings: [{ id: 'h1', artist_name: 'V S Gaitonde', artist_key: 'v s gaitonde', work: 'U', acquired: '2019-03-14', paid_inr: 1 }],
    referrals: [{ id: 'r1', name: 'X', tie: 'Y' }],
    log: [{ id: 'l1', happened: '2026-08-01', channel: 'Call', note: 'N' }],
    followups: [{ id: 'f1', due: '2026-09-01', reason: 'R', done: 0 }] };
  const bare = { id: CID, name: 'Bare', title: null, city: null, tier: null, since: null,
    lifetime_inr: null, focus: null, brief: null, wants: [], next_when: null, next_what: null,
    holdings: [], referrals: [], log: [], followups: [] };

  const AUTH = {
    offline:        () => Object.assign(CRM, { live: false, needsKey: false, clients: null, detail: {}, detailBusy: {}, detailError: {}, activity: null, followups: null, diary: null }),
    locked:         () => Object.assign(CRM, { live: false, needsKey: true, clients: null, detail: {}, detailBusy: {}, detailError: {}, activity: null, followups: null, diary: null }),
    'live-empty':   () => Object.assign(CRM, { live: true, needsKey: false, clients: [], detail: {}, detailBusy: {}, detailError: {}, activity: [], followups: [], diary: { configured: false, today: [] } }),
    'live-bare':    () => Object.assign(CRM, { live: true, needsKey: false, clients: [{ id: CID, name: 'Bare', tier: null, focus: null, wants: [], holdings: 0, open_followups: 0 }], detail: { [CID]: bare }, detailBusy: {}, detailError: {}, activity: [], followups: [], diary: { configured: true, today: [] } }),
    'live-full':    () => Object.assign(CRM, { live: true, needsKey: false, clients: [{ id: CID, name: 'Probe', tier: 'Principal', focus: 'M', wants: [], holdings: 1, open_followups: 1 }], detail: { [CID]: full }, detailBusy: {}, detailError: {}, activity: [{ id: 'l1', client_id: CID, client_name: 'Probe', happened: '2026-08-01', channel: 'Call', note: 'N' }], followups: [{ id: 'f1', client_id: CID, client_name: 'Probe', due: '2026-09-01', reason: 'R', done: 0, overdue: true, age: 'overdue 5d' }], diary: { configured: true, today: [{ time: '11:00', title: 'V', location: 'M' }] } }),
    'detail-missing': () => Object.assign(CRM, { live: true, needsKey: false, clients: [{ id: CID, name: 'Probe', tier: 'Principal', focus: 'M', wants: [], holdings: 1, open_followups: 1 }], detail: {}, detailBusy: { [CID]: true }, detailError: {}, activity: [], followups: [], diary: { configured: true, today: [] } }),
    // The record asked for and refused. Before the fix this and the state above
    // printed the same sentence and waited for ever.
    'detail-failed': () => Object.assign(CRM, { live: true, needsKey: false, clients: [{ id: CID, name: 'Probe', tier: 'Principal', focus: 'M', wants: [], holdings: 1, open_followups: 1 }], detail: {}, detailBusy: {}, detailError: { [CID]: 'Could not reach the book from this device.' }, activity: [], followups: [], diary: { configured: true, today: [] } }),
    // THE BUG this file exists to keep out: S.client holding an id the book does
    // not carry. It shipped as the default ("nanda", from the old sample book),
    // so opening Clients on a wide screen sat on "Opening the card" for ever.
    'stale-selection': () => { Object.assign(CRM, { live: true, needsKey: false, clients: [{ id: CID, name: 'Probe', tier: 'Principal', focus: 'M', wants: [], holdings: 1, open_followups: 1 }], detail: { [CID]: full }, detailBusy: {}, detailError: {}, followups: [], diary: { configured: true, today: [] } }); STALE = true; },
    // Live, signed in, and nobody in the book to select.
    'live-none':    () => Object.assign(CRM, { live: true, needsKey: false, clients: [], detail: {}, detailBusy: {}, detailError: {}, activity: [], followups: [], diary: { configured: true, today: [] } }),
  };
  // set by the stale-selection state; makes the loop point S.client at a ghost
  let STALE = false;

  const SCREENS = ['today', 'news', 'article', 'search', 'artists', 'market', 'clients',
                   'calendar', 'knowledge', 'more', 'newclient', 'unlock', 'dossier',
                   'client', 'ask'];
  const MARKET_TABS_T = ['overview', 'results', 'artists', 'auctions', 'no-such-tab'];
  const NEWS_TABS_T = ['foryou', 'all', 'saved'];
  const CLIENT_TABS_T = ['overview', 'interests', 'activity', 'templates', 'no-such-tab'];
  // A story key that exists, one that never did, and nothing at all.
  const someStory = () => (allStories()[0] || {}).key || null;
  const PANELS = [null, 'client', 'holding', 'log', 'followup', 'holding:h1', 'log:l1', 'followup:f1'];
  const ASK_STATES = {
    'ask-off':      () => { CRM.ai = false; S.askLog = []; S.askBusy = false; S.askError = null; },
    'ask-empty':    () => { CRM.ai = true; S.askLog = []; S.askBusy = false; S.askError = null; },
    'ask-busy':     () => { CRM.ai = true; S.askLog = [{ role: 'user', content: 'q' }]; S.askBusy = true; S.askError = null; },
    'ask-answered': () => { CRM.ai = true; S.askBusy = false; S.askError = null;
      S.askLog = [{ role: 'user', content: 'q' },
                  { role: 'assistant', content: 'a', model: 'm', unverified: [], sources: ['Client book'] }]; },
    'ask-warned':   () => { CRM.ai = true; S.askBusy = false; S.askError = null;
      S.askLog = [{ role: 'user', content: 'q' },
                  { role: 'assistant', content: 'a', model: 'm', unverified: ['34', '2019'], sources: [] }]; },
    'ask-broken':   () => { CRM.ai = true; S.askLog = []; S.askBusy = false; S.askError = 'rate limited'; },
    'ask-ragged':   () => { CRM.ai = true; S.askBusy = false; S.askError = null;
      S.askLog = [{ role: 'assistant', content: 'a' }]; },   // no model, no sources, no unverified
  };
  let runs = 0;

  // --- axis 1+2: viewport x auth state ------------------------------------
  for (const [vp, pad, split] of [['phone', false, false], ['tablet', true, false], ['desktop', true, true]]) {
    isPad = pad; isSplit = split;
    for (const [auth, setup] of Object.entries(AUTH)) {
      STALE = false; setup();
      for (const sc of SCREENS) {
        S.screen = sc; S.marketTab = 'overview';
        S.client = STALE ? 'no-such-collector' : CID;
        S.clientTab = 'overview'; S.newsTab = 'foryou';
        S.article = someStory(); S.back = null; S.query = '';
        S.artist = Object.keys(ARTISTS)[0]; S.panel = null; S.draft = {}; S.confirmDelete = null;
        if (sc === 'ask') {
          for (const [name, set] of Object.entries(ASK_STATES)) {
            set(); runs++;
            attempt(`${vp}/${auth}/ask/${name}`, () => render(false));
          }
          CRM.ai = true;
          continue;
        }
        for (const p of (sc === 'client' ? PANELS : [null])) {
          S.panel = p;
          S.draft = p === 'client' ? { name: 'X', tier: 'Growth' } : {};
          S.confirmDelete = p && p.includes(':') ? p : null;
          runs++; attempt(`${vp}/${auth}/${sc}${p ? '/' + p : ''}`, () => render(false));
        }
        if (sc === 'market') {
          for (const mt of MARKET_TABS_T) {
            S.marketTab = mt; runs++;
            attempt(`${vp}/${auth}/market:${mt}`, () => render(false));
          }
          S.marketTab = 'overview';
        }
        if (sc === 'news') {
          for (const nt of NEWS_TABS_T) {
            S.newsTab = nt; runs++;
            attempt(`${vp}/${auth}/news:${nt}`, () => render(false));
          }
          S.newsTab = 'foryou';
        }
        // An article the desk holds, one it never had, and none named at all.
        if (sc === 'article') {
          for (const key of [someStory(), 'no-such-story', null, '']) {
            for (const back of [null, 'today', 'article', 'search']) {
              S.article = key; S.back = back; runs++;
              attempt(`${vp}/${auth}/article:${key ? 'real' : String(key)}/back:${back}`, () => render(false));
            }
          }
          S.article = someStory(); S.back = null;
        }
        if (sc === 'search') {
          for (const q of ['', 'raza', 'zzzzz', 'a', '<img src=x onerror=alert(1)>', 'september']) {
            S.query = q; runs++;
            attempt(`${vp}/${auth}/search("${q.slice(0, 12)}")`, () => render(false));
          }
          S.query = '';
        }
        if (sc === 'client' || sc === 'clients') {
          for (const ct of CLIENT_TABS_T) {
            S.clientTab = ct; runs++;
            attempt(`${vp}/${auth}/${sc}/tab:${ct}`, () => render(false));
          }
          S.clientTab = 'overview';
        }
        // the month grid carries its own state: which month, which day open
        if (sc === 'calendar') {
          for (const m of [-14, 0, 1, 18]) {
            for (const day of [null, '2026-09-10', 'not-a-date', '']) {
              for (const ev of [null, 'no-such-key', (REAL.events && REAL.events[0]
                    ? `${REAL.events[0].starts}|${REAL.events[0].house}|${(REAL.events[0].title||'').slice(0,40)}` : 'x')]) {
              S.calMonth = m; S.calDay = day; S.calEvent = ev;
              runs++; attempt(`${vp}/${auth}/calendar/m${m}/d:${day}/e:${ev}`, () => render(false));
              }
            }
          }
          S.calMonth = 0; S.calDay = null;
        }
      }
    }
  }
  // A pass here is not "it did not throw" \u2014 the bug never threw. On a wide
  // screen a live book must end up pointed at a collector it holds, and the
  // detail column must show that card rather than an eternal "Opening the card".
  isPad = true; isSplit = true;
  AUTH['stale-selection'](); STALE = false;
  S.screen = 'clients'; S.client = 'no-such-collector';
  S.panel = null; S.draft = {}; S.confirmDelete = null;
  render(false);
  if (S.client !== CID) failures.push(`STALE selection not repaired :: S.client=${S.client}`);
  const detailText = (document.querySelector('.split .detail') || { textContent: '' }).textContent;
  if (/Opening the card/.test(detailText)) failures.push('STALE selection still shows "Opening the card"');
  if (!/Probe/.test(detailText)) failures.push('STALE selection did not paint the card');
  runs++;

  isPad = savedPad; isSplit = savedSplit; Object.assign(CRM, savedCRM);

  // --- axis 3: adverse data shapes ----------------------------------------
  const CASES = {
    'news missing': r => { delete r.news; },
    'news empty': r => { r.news = { on_market: [], wider: [] }; },
    'news row missing url': r => { r.news = { on_market: [{ date: '2026-08-01', source: 'X', headline: 'H' }], wider: [] }; },
    'events empty': r => { r.events = []; },
    'events missing': r => { delete r.events; },
    'event all-null': r => { r.events = [{ starts: '2026-09-01', ends: null, title: null, house: null, city: null, kind: null, url: null, lot_count: null }]; },
    'feed empty': r => { r.feed = []; },
    'feed row all nulls': r => { r.feed = [{ date: '2026-08-01', house: null, artist: 'A', artist_key: null, title: null, price: null, est: null, above: null }]; },
    'trending empty': r => { r.trending = []; },
    'trending unknown key': r => { r.trending = ['no-such-artist']; },
    'recent_sales empty': r => { r.recent_sales = []; },
    'fx missing': r => { delete r.fx; },
    'artists empty': r => { r.artists = {}; },
    'artist index null': r => { const k = Object.keys(r.artists)[0]; r.artists[k].index = null; },
    'artist records empty': r => { const k = Object.keys(r.artists)[0]; r.artists[k].records = []; },
    // The auction record carries a picture now, and the URL comes from a house
    // feed. safeUrl() is what stands between that and a live javascript: link.
    'record image is a script': r => { for (const a of Object.values(r.artists))
      for (const x of a.records) x.image = 'javascript:alert(1)'; },
    'record image missing': r => { for (const a of Object.values(r.artists))
      for (const x of a.records) delete x.image; },
    'record image is junk': r => { for (const a of Object.values(r.artists))
      for (const x of a.records) x.image = { nope: true }; },
    'feed image is a script': r => { for (const f of (r.feed || [])) f.image = 'JaVaScRiPt:alert(1)'; },
    'artist stats empty': r => { const k = Object.keys(r.artists)[0]; r.artists[k].stats = {}; },
    'coverage stripped': r => { r.coverage = {}; },
    'coverage.houses missing': r => { delete r.coverage.houses; },
    'caveats stripped': r => { r.caveats = {}; },
    'generated_at missing': r => { delete r.generated_at; },
    'everything stripped': r => { for (const k of Object.keys(r)) delete r[k]; },
    // A hand-typed end date is the one input nobody validates. Before the cap,
    // "9999-12-31" spun the day-spanning loop ~2.9M times and hung the tab.
    'event ends in 9999': r => { r.events = [{ starts: '2026-09-01', ends: '9999-12-31', title: 'Runaway', house: 'AstaGuru', city: null, kind: null, url: null, lot_count: null }]; },
    'event ends before it starts': r => { r.events = [{ starts: '2026-09-10', ends: '2026-09-01', title: 'Backwards', house: 'AstaGuru', city: null, kind: null, url: null, lot_count: null }]; },
    'event with no start': r => { r.events = [{ starts: null, ends: null, title: 'No date', house: 'AstaGuru', city: null, kind: null, url: null, lot_count: null }]; },
    'event with junk dates': r => { r.events = [{ starts: 'not-a-date', ends: 'also-not', title: 'Junk', house: null, city: null, kind: null, url: null, lot_count: null }]; },
  };
  for (const [label, mutate] of Object.entries(CASES)) {
    const r = JSON.parse(JSON.stringify(savedREAL));
    mutate(r);
    REAL = r;
    caught = [];
    try { rebuildFromReal(); } catch (e) { failures.push(`THREW ${label}/rebuildFromReal :: ${e.message}`); }
    for (const sc of ['today', 'news', 'article', 'search', 'artists', 'market', 'clients',
                      'calendar', 'knowledge', 'more', 'dossier']) {
      S.screen = sc; S.marketTab = 'overview'; S.panel = null;
      S.newsTab = 'foryou'; S.clientTab = 'overview';
      S.article = (allStories()[0] || {}).key || null;
      S.query = sc === 'search' ? 'raza' : '';
      S.artist = Object.keys(ARTISTS)[0] || 'missing';
      S.calMonth = sc === 'calendar' ? 1 : 0; S.calDay = null;
      runs++; attempt(`data:${label}/${sc}`, () => render(false));
    }
    S.calMonth = 0;
    for (const mt of ['results', 'artists', 'auctions']) {
      S.screen = 'market'; S.marketTab = mt;
      runs++; attempt(`data:${label}/market:${mt}`, () => render(false));
    }
    for (const nt of ['foryou', 'all', 'saved']) {
      S.screen = 'news'; S.newsTab = nt;
      runs++; attempt(`data:${label}/news:${nt}`, () => render(false));
    }
    for (const q of ['', 'gaitonde', 'september']) {
      S.screen = 'search'; S.query = q;
      runs++; attempt(`data:${label}/search("${q}")`, () => render(false));
    }
    S.query = '';
  }

  REAL = savedREAL; rebuildFromReal();
  Object.assign(S, savedS);
  S.panel = null; S.draft = {}; S.confirmDelete = null;
  S.calMonth = 0; S.calDay = null;
  render(false);
  console.error = realErr;
  ensureDetail = realEnsure;
  return { combinationsRun: runs, failureCount: failures.length, failures: failures.slice(0, 40) };
})()

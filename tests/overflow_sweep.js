/* Horizontal-overflow sweep for index.html. Paste into the browser console.
 *
 *     Type size has broken this layout twice, and both times by a handful of
 *     pixels that nobody sees until it is on her iPad: a day number and a TODAY
 *     pill that no longer fit a 56px calendar cell, a rail subtitle that wrapped.
 *     Neither was visible in a screenshot. Both were obvious in a measurement.
 *
 * Run it at 375 / 768 / 1280 / 1920 after ANY change to type, spacing or a
 * column. It reports three things and deliberately not more:
 *
 *   PAGE / MAIN scrolls   the layout is actually broken — something is pushing
 *                         the whole surface sideways.
 *   an element outside     unless some ancestor scrolls horizontally on purpose.
 *     the viewport         A chip row, a filter bar and the lot rail all bleed
 *                         past the gutter by design; that is not a finding.
 *   a LEAF whose text      containers are excluded: a container is wider than
 *     will not fit         its box whenever a child bleeds on purpose, and
 *                         overflow:hidden means it was meant to clip, so an
 *                         ellipsis is not a finding either.
 *
 * IMPORTANT: isPad/isSplit are forced from matchMedia first. Viewport emulation
 * does not fire matchMedia change events, so the flags go stale and you measure
 * the wrong layout — the same trap the render matrix carries.
 *
 * A green run is only evidence if it can go red. Verified by injecting
 * `.sec-head h2{white-space:nowrap;font-size:60px}` — 21 findings — and removing
 * it again — 0.
 */
(async () => {
  isPad = matchMedia('(min-width: 768px)').matches;
  isSplit = matchMedia('(min-width: 1024px)').matches;
  const bad = [];
  const W = innerWidth;

  const check = label => {
    const doc = document.scrollingElement;
    if (doc.scrollWidth > doc.clientWidth + 1)
      bad.push(`${label} :: PAGE scrolls ${doc.scrollWidth} > ${doc.clientWidth}`);
    const m = document.getElementById('main');
    if (m.scrollWidth > m.clientWidth + 1)
      bad.push(`${label} :: MAIN scrolls ${m.scrollWidth} > ${m.clientWidth}`);
    // any element wider than the viewport, or sticking out past its right edge
    for (const el of document.querySelectorAll('#main *')) {
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed' || cs.display === 'none') continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.right > W + 1 || r.left < -1) {
        // Sitting outside the viewport is fine if SOME ancestor scrolls
        // horizontally on purpose — a chip row, a filter bar, the lot rail.
        let ok = false;
        for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
          const ax = getComputedStyle(a).overflowX;
          if (ax === 'auto' || ax === 'scroll') { ok = true; break; }
        }
        if (!ok) { bad.push(`${label} :: ${el.className || el.tagName} right=${Math.round(r.right)} > ${W}`); break; }
      }
      /* Text that cannot fit its own box. Leaves only: a CONTAINER is wider than
         its box whenever a child bleeds to the edge on purpose (a chip row, the
         lot rail, a filter bar), and that is not an overflow — PAGE and MAIN
         above are what catch a layout actually breaking. overflow:hidden means
         it was MEANT to clip, so an ellipsis is not a finding either. */
      if (!el.firstElementChild && el.scrollWidth > el.clientWidth + 2
          && cs.overflowX === 'visible' && el.clientWidth > 0) {
        bad.push(`${label} :: ${el.className || el.tagName} content ${el.scrollWidth} > box ${el.clientWidth}`);
        break;
      }
    }
  };

  // Word-shaped stand-ins: the anonymised export is unbroken runs of 'x', which
  // no wrapper can break, and every one of those reads as an overflow.
  const WORDS = ['Meera','Krishnan','Ramanathan','Trustee','Foundation','Collection',
                 'Bengaluru','Chatterjee','Nanda','Advisory'];
  const phrase = n => Array.from({length:n}, (_,i) => WORDS[(i*3+n) % WORDS.length]).join(' ');
  if (CRM.live && CRM.clients) {
    CRM.clients = CRM.clients.map(c => ({ ...c, name: phrase(3), focus: phrase(2), city: 'Mumbai' }));
    CRM.activity = (CRM.activity || []).map(l => ({ ...l, note: phrase(14), client_name: phrase(2) }));
    CRM.followups = (CRM.followups || []).map(f => ({ ...f, reason: phrase(9), client_name: phrase(2) }));
    for (const k of Object.keys(CRM.detail)) {
      const d = CRM.detail[k];
      CRM.detail[k] = { ...d, name: phrase(3), title: phrase(4), city: 'Mumbai',
        brief: phrase(30), focus: phrase(2), wants: [phrase(2), phrase(1)],
        next_what: phrase(10),
        log: (d.log || []).map(l => ({ ...l, note: phrase(16) })),
        followups: (d.followups || []).map(f => ({ ...f, reason: phrase(10) })) };
    }
  }

  /* The auction record carries a picture now, and app_data.json only gains the
     field on the next CI rebuild — so measure the layout that IS coming, not
     the one that happens to be in the file today. */
  const someImg = (REAL.feed || []).map(f => f.image).filter(Boolean);
  if (someImg.length) {
    let n = 0;
    for (const a of Object.values(REAL.artists)) for (const r of a.records) r.image = someImg[n++ % someImg.length];
    // and one without, because a record with no picture takes the full width
    const first = Object.values(REAL.artists)[0];
    if (first && first.records[0]) first.records[0].image = null;
    ARTISTS = buildArtists();
  }

  const SCREENS = ['today','news','market','artists','clients','calendar','knowledge','more','search','ask'];
  for (const sc of SCREENS) {
    if (sc === 'search') { go(sc, { query: 'raza' }); renderSearchResults(); }
    else go(sc, {});
    check(`${W}/${sc}`);
    if (sc === 'market') for (const t of ['overview','results','artists','auctions']) {
      S.marketTab = t; render(false); check(`${W}/market:${t}`);
    }
    if (sc === 'news') for (const t of ['foryou','all','saved']) {
      S.newsTab = t; render(false); check(`${W}/news:${t}`);
    }
    if (sc === 'clients') for (const t of ['overview','interests','activity','templates']) {
      S.clientTab = t; render(false); check(`${W}/client:${t}`);
    }
  }
  const st = allStories();
  for (const s of [st.find(x => x.image), st.find(x => !x.image), st.find(x => x.artists.length)]) {
    if (!s) continue;
    go('article', { article: s.key, back: 'news' });
    check(`${W}/article${s.image ? '+img' : ''}${s.artists.length ? '+artists' : ''}`);
  }
  go('dossier', { artist: Object.keys(ARTISTS)[0] }); check(`${W}/dossier`);
  go('client', {}); check(`${W}/client-card`);
  return { width: W, isPad, isSplit, problems: bad };
})()

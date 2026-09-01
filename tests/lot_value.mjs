/* lotValue() — the comparator behind Today's "Top result".
 *
 *     node tests/lot_value.mjs
 *
 * The feed carries a price as the house PRINTED it ("₹9.98 cr"); there is no
 * number behind it. So ranking lots by value means reading the string back,
 * which is the one thing this desk otherwise refuses to do with money — the two
 * formatters have disagreed twice. Two rules make it safe here:
 *
 *   it only ever ORDERS   the printed string is what reaches the page.
 *   it fails CLOSED       an unreadable price returns null and drops out of the
 *                         ranking, rather than parsing to 0 and passing for the
 *                         cheapest lot in the sale.
 *
 * The definitions are lifted out of index.html at run time rather than copied,
 * so the test cannot drift from the code it is testing.
 *
 * Written after "Top result" was found showing ₹3.00 cr while a ₹9.98 cr Souza
 * sat in the same forty lots: the tile had been sorting by percentage over
 * estimate, not by price.
 */
import fs from 'fs';

const src = fs.readFileSync(new URL('../index.html', import.meta.url),'utf8');
// lift the real definitions out of index.html so the test cannot drift from it
const m = src.match(/const LOT_UNIT = \{[^\n]*\};/);
const f = src.match(/const lotValue = p => \{[\s\S]*?\n\};/);
if (!m || !f) { console.log('could not find LOT_UNIT / lotValue in index.html'); process.exit(1); }
const lotValue = new Function(m[0] + '\n' + f[0] + '\nreturn lotValue;')();

const cases = [
  ['₹9.98 cr', 99800000], ['₹3.00 cr', 30000000], ['₹98 lakh', 9800000],
  ['₹1.4 lakh', 140000], ['₹45,600', 45600], ['₹66,000', 66000],
  ['₹1,00,00,000', 10000000],
  ['₹0.5 cr', 5000000], ['₹12 crore', 120000000],
  ['—', null], ['', null], [null, null], [undefined, null],
  ['Refer department', null], ['$120,000', null], ['₹1.2 million', null],
];
let bad = 0;
for (const [inp, want] of cases) {
  const got = lotValue(inp);
  const ok = got === want;
  if (!ok) bad++;
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${String(JSON.stringify(inp)).padEnd(22)} -> ${got}${ok ? '' : '   want ' + want}`);
}
// the ordering property that actually matters
const feed = JSON.parse(fs.readFileSync(new URL('../data/app_data.json', import.meta.url),'utf8')).feed;
const unparsed = feed.filter(f => lotValue(f.price) == null);
const ranked = feed.filter(f => lotValue(f.price) != null).sort((a,b)=>lotValue(b.price)-lotValue(a.price));
console.log(`\n  ${feed.length} feed prices, ${unparsed.length} unreadable (dropped, never sorted as zero)`);
console.log(`  top by value: ${ranked[0].artist} ${ranked[0].price}`);
console.log(`  a crore must outrank a lakh: ${lotValue('₹1 cr') > lotValue('₹99 lakh') ? 'ok' : 'FAIL'}`);
console.log(bad ? `\n${bad} FAILED` : '\nall lotValue cases correct');
process.exit(bad ? 1 : 0);


// mirrors safeUrl() in index.html, control range written as an escape
const CTRL = new RegExp("[\\u0000-\\u0020]", "g");
const safeUrl = u => {
  const raw = String(u == null ? "" : u).replace(CTRL, "");
  return /^https?:\/\//i.test(raw) ? raw : "";
};
const TAB = String.fromCharCode(9), LF = String.fromCharCode(10), CR = String.fromCharCode(13);
const cases = [
  ["https://a.com/x", true],
  ["http://a.com", true],
  ["HTTPS://A.com/y", true],
  ["https://www.saffronart.com/auctions/friday-five-5054", true],
  ["javascript:alert(1)", false],
  ["java" + LF + "script:alert(1)", false],
  ["java" + TAB + "script:alert(1)", false],
  ["java" + CR + "script:alert(1)", false],
  ["   javascript:alert(1)", false],
  [TAB + "javascript:alert(1)", false],
  ["JaVaScRiPt:alert(1)", false],
  ["data:text/html,<script>1</script>", false],
  ["//evil.com", false],
  ["vbscript:x", false],
  ["file:///etc/passwd", false],
  ["", false], [null, false], [undefined, false],
];
let bad = 0;
for (const [input, shouldPass] of cases) {
  const got = safeUrl(input);
  const ok = (got !== "") === shouldPass;
  if (!ok) bad++;
  console.log("  " + (ok ? "ok  " : "FAIL") + "  " + String(JSON.stringify(input)).padEnd(46) + " -> " + JSON.stringify(got));
}
console.log("");
console.log(bad ? bad + " FAILURES" : "all " + cases.length + " cases correct");

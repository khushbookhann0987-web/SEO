const $ = (id) => document.getElementById(id);
function el(tag, cls, txt) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
}
$("content").addEventListener("input", () => ($("chars").textContent = $("content").value.length));
const showError = (m) => { $("error").textContent = m; $("error").hidden = false; };

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("error").hidden = true;
  if ($("content").value.trim().length < 50) return showError("Please paste at least a few sentences of page content.");
  $("go").disabled = true; $("go").textContent = "Analyzing...";
  try {
    const res = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: $("content").value, keyword: $("kw").value, page_type: $("type").value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Something went wrong.");
    render(data);
  } catch (err) {
    showError(err.message.includes("fetch") ? "Cannot reach the server. Is app.py running?" : err.message);
  } finally { $("go").disabled = false; $("go").textContent = "Analyze page"; }
});

function copyBtn(label, text) {
  const b = el("button", "btn", label);
  b.type = "button";
  b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch { b.textContent = "Copy failed"; }
    setTimeout(() => (b.textContent = label), 1500);
  });
  return b;
}
function panel(title) { const p = el("div", "panel"); p.append(el("h2", "", title)); return p; }
function tags(items, fmt) {
  const box = el("div", "tags");
  items.forEach((i) => { const s = el("span"); s.append(fmt(i)); box.append(s); });
  return box;
}

function lenField(label, text, max, minOk) {
  const row = el("div", "field");
  const ok = text.length <= max && text.length >= minOk;
  row.append(el("strong", "", label), el("span", "len " + (ok ? "ok" : "bad"), `${text.length} characters (aim ${minOk}-${max})`), copyBtn("Copy", text));
  return row;
}

function render(d) {
  const out = $("result");
  out.replaceChildren();
  const color = d.score >= 75 ? "#15803d" : d.score >= 50 ? "#d97706" : "#b42318";

  // score + stats
  const top = el("div", "panel top");
  const sc = el("div");
  const ring = el("div", "score");
  ring.style.background = `conic-gradient(${color} ${d.score * 3.6}deg, #e5eaf0 0)`;
  ring.append(el("b", "", String(d.score)));
  sc.append(ring, el("small", "", "Content checks score"));
  const right = el("div");
  right.append(el("h2", "", "SEO review"), el("p", "", d.ai.summary));
  const stats = el("div", "stats");
  [["Words", d.stats.words], ["Sentences", d.stats.sentences], ["Avg sentence", d.stats.avg_sentence + " words"],
   ["Readability", `${d.stats.readability} (${d.stats.readability_level})`]].forEach(([k, v]) => {
    const s = el("div", "stat"); s.append(el("b", "", String(v)), el("span", "", k)); stats.append(s);
  });
  right.append(stats);
  top.append(sc, right);
  out.append(top);

  // meta
  const meta = panel("Suggested meta title and description");
  const serp = el("div", "serp");
  serp.append(el("div", "u", "https://www.yoursite.com › page"), el("div", "t", d.ai.meta_title), el("div", "d", d.ai.meta_description));
  meta.append(serp, lenField("Meta title", d.ai.meta_title, 60, 30), lenField("Meta description", d.ai.meta_description, 160, 120));
  out.append(meta);

  // recommendations
  const rec = panel("Recommendations");
  d.ai.recommendations.forEach((r) => {
    const row = el("div", "rec");
    const body = el("div");
    body.append(el("div", "", r.action), el("small", "", r.area));
    row.append(el("span", `pill ${r.priority}`, r.priority), body);
    rec.append(row);
  });
  out.append(rec);

  // checks + keywords
  const two = el("div", "two");
  const checks = panel("Content checks");
  d.checks.forEach((c) => {
    const row = el("div", "chk");
    const body = el("div"); body.append(el("strong", "", c.label), el("small", "", c.detail));
    row.append(el("span", `dot ${c.status}`, c.status === "pass" ? "✓" : c.status === "warn" ? "!" : "✕"), body);
    checks.append(row);
  });
  const kwp = panel("Keyword usage");
  if (d.keyword) kwp.append(el("p", "", `"${d.keyword.keyword}" appears ${d.keyword.count} time(s), ${d.keyword.density}% of the text.`));
  kwp.append(el("small", "", "Most used words"));
  kwp.append(tags(d.top_keywords, (k) => { const f = document.createDocumentFragment(); f.append(k.term + " ", el("b", "", k.count)); return f; }));
  if (d.top_phrases.length) {
    kwp.append(el("small", "", "Most used phrases"));
    kwp.append(tags(d.top_phrases, (k) => { const f = document.createDocumentFragment(); f.append(k.term + " ", el("b", "", k.count)); return f; }));
  }
  if (d.ai.keyword_ideas.length) {
    kwp.append(el("small", "", "Related keyword ideas"));
    kwp.append(tags(d.ai.keyword_ideas, (k) => k));
  }
  two.append(checks, kwp);
  out.append(two);

  // headings
  const hp = el("div", "two");
  const cur = panel("Your current headings");
  const co = el("div", "outline");
  if (d.headings.length) d.headings.forEach((h) => { const r = el("div"); r.style.paddingLeft = (h.level - 1) * 16 + "px"; r.append(el("em", "", "H" + h.level), h.text); co.append(r); });
  else co.append(el("div", "", "No headings found."));
  cur.append(co);
  const sug = panel("Suggested heading outline");
  const so = el("div", "outline");
  d.ai.suggested_headings.forEach((h) => {
    const r = el("div"); r.style.paddingLeft = ((parseInt(h.level.slice(1)) || 1) - 1) * 16 + "px";
    r.append(el("em", "", h.level), h.text); so.append(r);
  });
  sug.append(so, copyBtn("Copy outline", d.ai.suggested_headings.map((h) => `${h.level}: ${h.text}`).join("\n")));
  hp.append(cur, sug);
  out.append(hp);

  out.hidden = false;
  out.scrollIntoView({ behavior: "smooth" });
}

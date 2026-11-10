import html
import json
import logging
import os
import re
from collections import Counter

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_URL = os.getenv("RENDER_EXTERNAL_URL", "http://127.0.0.1:5000")
MAX_CONTENT = 8000
PAGE_TYPES = ["Service page", "Home page", "Blog post", "Product page", "Landing page"]
STOP = set("""a about above after again all also am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his how i if in into is it
its just me more most my no nor not of off on once only or other our ours out over own same she should so some such than that the
their theirs them then there these they this those through to too under until up very was we were what when where which while who whom
why will with would you your yours get got make made use used using""".split())

logging.basicConfig(level=logging.INFO)
app = Flask(__name__, static_folder="static", static_url_path="/static")


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify(error=e.message), e.status


# ---------- measurable analysis (done in Python, not by the AI) ----------
def parse_page(raw):
    """Return (plain text, headings) from pasted text, markdown or HTML."""
    found = []
    for m in re.finditer(r"<h([1-6])[^>]*>(.*?)</h\1>", raw, re.I | re.S):
        found.append((m.start(), int(m.group(1)), re.sub(r"<[^>]+>", "", m.group(2)).strip()))
    for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*#*\s*$", raw, re.M):
        found.append((m.start(), len(m.group(1)), m.group(2).strip()))
    text = re.sub(r"<(script|style).*?</\1>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = html.unescape(text).strip()
    headings = [{"level": l, "text": html.unescape(t)[:150]} for _, l, t in sorted(found) if t]
    return text, headings


def syllables(word):
    word = word.lower()
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def analyze(text, headings, keyword):
    words = re.findall(r"[\w']+", text)
    n = max(len(words), 1)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(re.findall(r"[\w']+", s)) >= 2]
    s_count = max(len(sentences), 1)
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    avg_sentence = round(n / s_count, 1)
    flesch = 206.835 - 1.015 * avg_sentence - 84.6 * (sum(syllables(w) for w in words) / n)
    flesch = max(0, min(100, round(flesch)))
    level = ("Very easy" if flesch >= 80 else "Easy" if flesch >= 70 else "Standard" if flesch >= 60
             else "Fairly difficult" if flesch >= 50 else "Difficult" if flesch >= 30 else "Very difficult")

    lower_words = [w.lower() for w in words]
    content_words = [w for w in lower_words if len(w) > 3 and w not in STOP and not w.isdigit()]
    top = [{"term": w, "count": c} for w, c in Counter(content_words).most_common(8)]
    pairs = Counter(f"{a} {b}" for a, b in zip(lower_words, lower_words[1:])
                    if a not in STOP and b not in STOP and len(a) > 2 and len(b) > 2)
    top_phrases = [{"term": p, "count": c} for p, c in pairs.most_common(5) if c >= 2]

    kw = None
    if keyword:
        k = keyword.lower()
        pattern = r"(?<!\w)" + re.escape(k) + r"(?!\w)"
        count = len(re.findall(pattern, text.lower()))
        kw = {
            "keyword": keyword, "count": count,
            "density": round(count * len(k.split()) / n * 100, 2),
            "in_h1": any(h["level"] == 1 and re.search(pattern, h["text"].lower()) for h in headings),
            "in_headings": any(re.search(pattern, h["text"].lower()) for h in headings),
            "in_first_100": bool(re.search(pattern, " ".join(lower_words[:100]))),
        }

    stats = {"words": len(words), "sentences": len(sentences), "paragraphs": len(paragraphs),
             "avg_sentence": avg_sentence, "readability": flesch, "readability_level": level}
    return stats, top, top_phrases, kw


def run_checks(stats, headings, kw):
    checks = []
    add = lambda label, status, detail: checks.append({"label": label, "status": status, "detail": detail})
    w = stats["words"]
    add("Content length", "pass" if w >= 600 else "warn" if w >= 300 else "fail",
        f"{w} words. Aim for 600+ on a service page.")
    h1 = sum(1 for h in headings if h["level"] == 1)
    if not headings:
        add("H1 heading", "fail", "No headings found. Use # / ## in text or <h1>/<h2> tags.")
    else:
        add("H1 heading", "pass" if h1 == 1 else "fail" if h1 == 0 else "warn",
            "Exactly one H1." if h1 == 1 else "No H1 found." if h1 == 0 else f"{h1} H1 headings. Use only one.")
    subs = sum(1 for h in headings if h["level"] in (2, 3))
    add("Subheadings (H2/H3)", "pass" if subs >= 2 else "warn" if subs == 1 else "fail",
        f"{subs} subheading(s). Break the page into clear sections.")
    a = stats["avg_sentence"]
    add("Sentence length", "pass" if a <= 20 else "warn" if a <= 25 else "fail", f"Average {a} words per sentence.")
    r = stats["readability"]
    add("Readability", "pass" if r >= 60 else "warn" if r >= 45 else "fail",
        f"Flesch score {r} ({stats['readability_level']}). Higher is easier to read.")
    if kw:
        add("Keyword in H1", "pass" if kw["in_h1"] else "fail", "Target keyword appears in the H1." if kw["in_h1"] else "Add the keyword to the H1.")
        add("Keyword in first 100 words", "pass" if kw["in_first_100"] else "warn",
            "Found early in the text." if kw["in_first_100"] else "Mention the keyword in the opening paragraph.")
        d, c = kw["density"], kw["count"]
        status = "fail" if c == 0 or d > 3.5 else "pass" if 0.5 <= d <= 2.5 else "warn"
        add("Keyword density", status, f"Used {c} time(s), {d}% of the text. Healthy range is about 0.5% to 2.5%.")
    return checks


def score(checks):
    pts = {"pass": 1, "warn": 0.5, "fail": 0}
    return round(sum(pts[c["status"]] for c in checks) / len(checks) * 100)


# ---------- AI part ----------
SYSTEM = """You are a senior SEO content strategist. Review the page copy and return ONLY valid JSON shaped like:
{"summary":"2 sentences on the page's main SEO strengths and weaknesses",
 "meta_title":"...", "meta_description":"...",
 "suggested_headings":[{"level":"H1","text":"..."},{"level":"H2","text":"..."}],
 "recommendations":[{"priority":"high|medium|low","area":"Headings|Readability|Keywords|Content|Meta|Links","action":"specific action"}],
 "keyword_ideas":["related keyword"]}
Rules:
- meta_title: at most 60 characters, brand-neutral, keyword near the start if a target keyword is given.
- meta_description: 140 to 155 characters, clear benefit and a call to action.
- suggested_headings: one H1 and 3 to 5 H2 headings that fit the real content.
- 5 to 8 recommendations that refer to specific problems in THIS text, most important first. Keep each under 40 words.
- 5 keyword_ideas related to the topic.
- Never invent facts, prices, awards or locations that are not in the copy.
- Write in the language of the copy."""


def ask(user):
    if not API_KEY:
        raise ApiError("Server is missing OPENROUTER_API_KEY. Check your environment variables.", 500)
    payload = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
               "max_tokens": 1800, "temperature": 0.5}
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json",
               "HTTP-Referer": SITE_URL, "X-Title": "AI SEO Content Analyzer"}
    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content[content.find("{"): content.rfind("}") + 1])
    except requests.Timeout:
        raise ApiError("The AI took too long. Please try again.", 504)
    except requests.HTTPError as e:
        logging.error("OpenRouter %s: %s", e.response.status_code, e.response.text[:300])
        raise ApiError("The AI service returned an error. Check your API key and model.", 502)
    except (ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        logging.error("Bad model output: %r", e)
        raise ApiError("The AI reply could not be read. Please try again.", 502)
    except requests.RequestException as e:
        logging.error("Network error: %r", e)
        raise ApiError("Could not reach the AI service.", 502)


def clean_ai(raw):
    s = lambda v, n: str(v or "").strip()[:n]
    title, desc = s(raw.get("meta_title"), 120), s(raw.get("meta_description"), 300)
    recs = []
    for r in raw.get("recommendations", [])[:10]:
        pr = str(r.get("priority", "medium")).lower()
        if s(r.get("action"), 300):
            recs.append({"priority": pr if pr in ("high", "medium", "low") else "medium",
                         "area": s(r.get("area"), 30) or "Content", "action": s(r.get("action"), 300)})
    heads = [{"level": s(h.get("level"), 3).upper(), "text": s(h.get("text"), 150)}
             for h in raw.get("suggested_headings", [])[:8] if s(h.get("text"), 150)]
    if not title or not desc or not recs:
        raise ValueError("incomplete")
    return {"summary": s(raw.get("summary"), 500), "meta_title": title, "meta_description": desc,
            "suggested_headings": heads, "recommendations": recs,
            "keyword_ideas": [s(k, 60) for k in raw.get("keyword_ideas", [])[:8] if s(k, 60)]}


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", model=MODEL, key_loaded=bool(API_KEY))


@app.post("/api/analyze")
def analyze_route():
    d = request.get_json(silent=True) or {}
    raw = str(d.get("content", ""))[:MAX_CONTENT]
    keyword = str(d.get("keyword", "")).strip()[:80]
    page_type = d.get("page_type") if d.get("page_type") in PAGE_TYPES else "Service page"
    text, headings = parse_page(raw)
    if len(text) < 50:
        raise ApiError("Please paste at least a few sentences of page content.")

    stats, top, phrases, kw = analyze(text, headings, keyword)
    checks = run_checks(stats, headings, kw)
    outline = "\n".join(f"H{h['level']}: {h['text']}" for h in headings) or "(no headings found)"
    user = (f"Page type: {page_type}\nTarget keyword: {keyword or '(none given)'}\n"
            f"Words: {stats['words']}, readability: {stats['readability']} ({stats['readability_level']}), "
            f"avg sentence: {stats['avg_sentence']} words\nCurrent headings:\n{outline}\n\nPage copy:\n{text[:6000]}")
    try:
        ai = clean_ai(ask(user))
    except ValueError:
        raise ApiError("The AI reply was incomplete. Please try again.", 502)

    return jsonify(score=score(checks), checks=checks, stats=stats, headings=headings,
                   top_keywords=top, top_phrases=phrases, keyword=kw, ai=ai)


if __name__ == "__main__":
    # Local development only. On Render, gunicorn starts the app.
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")

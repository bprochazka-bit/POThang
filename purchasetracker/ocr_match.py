"""
Matching OCR'd text against PO lines, and guessing item fields from OCR text.

Used by the mobile API (blueprints/api.py). The Android app does on-device
OCR and sends us the raw text; all of the fuzzy logic lives here so it can be
unit-tested and tuned without shipping a new app build.

Matching strategy, strongest signal first:

  1. Identifiers (item.model / item.vendor_sku) appearing in the text, either
     verbatim or after folding characters OCR commonly confuses (O/0, I/l/1,
     S/5, B/8, Z/2). Labels on boxes almost always carry a model or part
     number, so this is the main signal.
  2. Name words: the fraction of the item name's significant words found in
     the text (exact, or a close fuzzy match for longer words).
  3. Description words, at a lower weight (descriptions are long and generic).
  4. Vendor name present.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable, Optional
from urllib.parse import urlparse

_WORD_RE = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Characters OCR mixes up on labels. Both sides are folded before comparing.
_CONFUSABLE = str.maketrans({"o": "0", "i": "1", "l": "1", "s": "5",
                             "b": "8", "z": "2", "q": "0"})

_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "pack", "pcs", "piece",
    "pieces", "set", "kit", "new", "inch", "inches", "black", "white", "size",
    "item", "items", "qty", "made", "in", "of", "to", "a", "an", "or", "by",
    "per", "each", "box", "case", "count", "color",
}

# Scores. A score >= MIN_SCORE is reported as a candidate.
SCORE_IDENT_EXACT = 100.0
SCORE_IDENT_FOLDED = 80.0
SCORE_IDENT_TOKEN = 60.0
SCORE_NAME = 50.0
SCORE_DESC = 15.0
SCORE_VENDOR = 8.0
MIN_SCORE = 12.0


def _compact(s: Optional[str]) -> str:
    return _NON_ALNUM_RE.sub("", (s or "").lower())


def _words(s: Optional[str]) -> list[str]:
    return _WORD_RE.findall((s or "").lower())


def _significant(words: Iterable[str]) -> list[str]:
    out = []
    seen = set()
    for w in words:
        if len(w) < 3 or w in _STOPWORDS:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


class OcrText:
    """Pre-processed view of a blob of OCR text, reused across candidates."""

    def __init__(self, text: str):
        self.raw = text or ""
        self.compact = _compact(self.raw)
        self.folded = self.compact.translate(_CONFUSABLE)
        words = _words(self.raw)
        # Also index the pieces of hyphenated / dotted tokens.
        expanded = set(words)
        for w in words:
            expanded.update(p for p in re.split(r"[.\-/]", w) if p)
        self.words = expanded
        self.compact_words = {_compact(w) for w in expanded}
        self.long_words = [w for w in expanded if len(w) >= 5]

    def has_word(self, word: str) -> bool:
        if word in self.words or _compact(word) in self.compact_words:
            return True
        if len(word) >= 5:
            # Tolerate one or two OCR slips in longer words.
            return bool(difflib.get_close_matches(word, self.long_words,
                                                  n=1, cutoff=0.84))
        return False


def _ident_score(ident: Optional[str], ocr: OcrText) -> tuple[float, Optional[str]]:
    c = _compact(ident)
    if len(c) < 3:
        return 0.0, None
    # Short identifiers (3 chars) only count as whole words - "a12" would
    # otherwise hit inside any random string.
    if len(c) < 4:
        return (SCORE_IDENT_TOKEN, "exact") if c in ocr.compact_words else (0.0, None)
    if c in ocr.compact:
        return SCORE_IDENT_EXACT, "exact"
    if c.translate(_CONFUSABLE) in ocr.folded:
        return SCORE_IDENT_FOLDED, "fuzzy"
    # A hyphenated/spaced model like "WH-1000XM5" may have been read as
    # separate words; require every part of it to be present.
    parts = [p for p in _words(ident) for p in re.split(r"[.\-/]", p) if p]
    if len(parts) > 1 and all(p in ocr.words for p in parts):
        return SCORE_IDENT_TOKEN, "parts"
    return 0.0, None


def score_item(item, ocr: OcrText) -> tuple[float, list[str]]:
    """Score how well an Item matches the OCR text; returns (score, reasons)."""
    score = 0.0
    reasons: list[str] = []

    best_ident = 0.0
    for label, value in (("model", item.model), ("SKU", item.vendor_sku)):
        s, how = _ident_score(value, ocr)
        if s:
            reasons.append(f"{label} {value}" + ("" if how == "exact" else f" ({how})"))
            best_ident = max(best_ident, s)
    score += best_ident

    name_words = _significant(_words(item.name))
    if name_words:
        hits = [w for w in name_words if ocr.has_word(w)]
        if hits:
            frac = len(hits) / len(name_words)
            score += SCORE_NAME * frac
            reasons.append(f"name {len(hits)}/{len(name_words)} words")

    desc_words = [w for w in _significant(_words(item.description))
                  if w not in name_words][:40]
    if desc_words:
        hits = [w for w in desc_words if ocr.has_word(w)]
        if hits:
            # Saturates at 8 matched words: long descriptions shouldn't
            # dominate the name.
            score += SCORE_DESC * min(1.0, len(hits) / min(8, len(desc_words)))
            reasons.append(f"description {len(hits)} words")

    vendor_words = _significant(_words(item.vendor))
    if vendor_words and all(ocr.has_word(w) for w in vendor_words):
        score += SCORE_VENDOR
        reasons.append("vendor")

    return score, reasons


def match_lines(text: str, lines: Iterable, limit: int = 10) -> list[dict]:
    """Rank PO lines by how well their item matches `text`.

    Returns [{"line": POLine, "score": float, "reasons": [...]}, ...], best
    first, only for scores >= MIN_SCORE. Lines for the same item share a
    score; outstanding lines are preferred on ties so the receiver lands on
    the line that still needs receiving.
    """
    ocr = OcrText(text)
    if not ocr.compact:
        return []
    cache: dict[int, tuple[float, list[str]]] = {}
    scored = []
    for line in lines:
        item = line.item
        if item is None:
            continue
        if item.id not in cache:
            cache[item.id] = score_item(item, ocr)
        score, reasons = cache[item.id]
        if score < MIN_SCORE:
            continue
        outstanding = line.qty - line.qty_received
        scored.append({"line": line, "score": round(score, 1),
                       "reasons": reasons, "outstanding": outstanding})
    scored.sort(key=lambda m: (-m["score"], m["outstanding"] <= 0,
                               m["line"].po_id, m["line"].line_no or 0))
    return scored[:limit]


def match_items(text: str, items: Iterable, limit: int = 10) -> list[dict]:
    """Same ranking as match_lines, but over bare Items (no PO needed)."""
    ocr = OcrText(text)
    if not ocr.compact:
        return []
    out = []
    for item in items:
        score, reasons = score_item(item, ocr)
        if score >= MIN_SCORE:
            out.append({"item": item, "score": round(score, 1), "reasons": reasons})
    out.sort(key=lambda m: (-m["score"], m["item"].id))
    return out[:limit]


# ---------- Field suggestions for "add item from photo" ----------

_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']+|"
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|co|us|ca|uk|de)(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"(?:US\s*)?\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")
_IDENT = r"([A-Z0-9][A-Z0-9\-_./]{2,40})"
_MODEL_RE = re.compile(
    r"\b(?:model(?:\s*(?:no|number|name|#))?|mpn|mfr\.?\s*part(?:\s*(?:no|number|#))?|"
    r"part\s*(?:no|number|#)|p/n|pn)\b\.?\s*[:#]?\s*" + _IDENT,
    re.IGNORECASE,
)
_SKU_RE = re.compile(
    r"\b(?:sku|asin|item\s*(?:no|number|#)|cat(?:alog)?\.?\s*(?:no|#)|upc)\b\.?\s*[:#]?\s*" + _IDENT,
    re.IGNORECASE,
)
_QTY_RE = re.compile(r"\b(?:qty|quantity)\b\.?\s*[:x]?\s*(\d{1,4})\b", re.IGNORECASE)
_BRAND_RE = re.compile(
    r"\b(?:brand\s*[:\-]?\s*|visit the\s+|by\s+)([A-Z][\w&'.\- ]{1,40}?)(?:\s+store)?\s*$",
    re.IGNORECASE,
)
_LABEL_LINE_RE = re.compile(
    r"^(?:\$|us\s*\$|qty|quantity|price|sku|asin|model|mpn|upc|part|p/n|item|"
    r"add to cart|buy now|in stock|ships? from|sold by|free (?:delivery|shipping)|"
    r"delivery|returns?|visit the|brand|rating|reviews?|\d+(?:\.\d+)?\s*out of)",
    re.IGNORECASE,
)

# Well-known storefront domains -> how we'd write the vendor name.
_KNOWN_VENDORS = {
    "amazon": "Amazon", "mcmaster": "McMaster-Carr", "digikey": "Digi-Key",
    "mouser": "Mouser", "homedepot": "Home Depot", "lowes": "Lowe's",
    "walmart": "Walmart", "bhphotovideo": "B&H Photo", "newegg": "Newegg",
    "adafruit": "Adafruit", "sparkfun": "SparkFun", "uline": "Uline",
    "grainger": "Grainger", "monoprice": "Monoprice", "staples": "Staples",
    "target": "Target", "costco": "Costco", "ebay": "eBay",
}


def _vendor_from_url(url: str) -> Optional[str]:
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    parts = [p for p in host.split(".") if p not in ("www", "smile", "m")]
    if len(parts) < 2:
        return None
    # amazon.co.uk -> amazon; shop.example.com -> example
    sld = parts[-3] if len(parts) >= 3 and parts[-2] in ("co", "com") else parts[-2]
    return _KNOWN_VENDORS.get(sld, sld.capitalize())


def _looks_like_name(text: str) -> bool:
    t = text.strip()
    if len(t) < 4 or _LABEL_LINE_RE.match(t) or _URL_RE.fullmatch(t):
        return False
    letters = sum(c.isalpha() for c in t)
    return letters >= 3 and letters / max(1, len(t)) > 0.45


def suggest_item_fields(text: str, lines: Optional[list[dict]] = None) -> dict:
    """Guess item fields from OCR text (e.g. a product page screenshot).

    `lines` optionally carries [{"text": ..., "height": px}, ...] from the
    OCR engine; the tallest plausible line is taken as the item name, which
    on product pages and box labels is nearly always the headline. Without
    heights we fall back to the longest plausible line near the top.

    Returns only the fields we could find; everything is a suggestion for
    the user to confirm.
    """
    text = text or ""
    if lines:
        text_lines = [str(l.get("text") or "").strip() for l in lines]
    else:
        text_lines = [l.strip() for l in text.splitlines()]
    text_lines = [l for l in text_lines if l]
    full = "\n".join(text_lines) if lines else text
    out: dict = {}

    m = _URL_RE.search(full)
    if m:
        url = m.group(0).rstrip(".,;)")
        out["url"] = url if url.lower().startswith("http") else "https://" + url
        vendor = _vendor_from_url(url)
        if vendor:
            out["vendor"] = vendor

    m = _PRICE_RE.search(full)
    if m:
        try:
            out["unit_cost"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    m = _MODEL_RE.search(full)
    if m:
        out["model"] = m.group(1).rstrip(".,;")
    m = _SKU_RE.search(full)
    if m:
        out["vendor_sku"] = m.group(1).rstrip(".,;")
    m = _QTY_RE.search(full)
    if m:
        out["qty"] = max(1, int(m.group(1)))

    for l in text_lines:
        m = _BRAND_RE.search(l)
        if m and l.lower().startswith(("brand", "visit the", "by ")):
            out["brand"] = m.group(1).strip()
            break

    # Name: tallest plausible line, else the longest of the first few.
    candidates = [l for l in (lines or []) if _looks_like_name(str(l.get("text") or ""))]
    name = None
    if candidates and any(c.get("height") for c in candidates):
        best = max(candidates, key=lambda c: (float(c.get("height") or 0),
                                              len(str(c.get("text")))))
        name = str(best["text"]).strip()
    else:
        plausible = [l for l in text_lines[:12] if _looks_like_name(l)]
        if plausible:
            name = max(plausible, key=len)
    if name:
        out["name"] = name[:255]
        # Description: the next couple of plausible lines after the name.
        try:
            idx = text_lines.index(name)
        except ValueError:
            idx = -1
        follow = [l for l in text_lines[idx + 1: idx + 6] if _looks_like_name(l)][:2]
        desc = " ".join(follow)
        if out.get("brand"):
            desc = (f"Brand: {out['brand']}. " + desc).strip()
        if desc:
            out["description"] = desc[:1000]
    elif out.get("brand"):
        out["description"] = f"Brand: {out['brand']}"

    return out

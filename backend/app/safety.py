"""Privacy redaction and safety checks. Review text is treated as untrusted data."""
import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\s().\-]{6,}\d(?![\w])")
INJECTION_RE = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?)"
    r"|disregard .{0,40}instructions|system prompt|you are now|reveal .{0,30}(prompt|instructions)",
    re.I,
)
SENSITIVE = {
    "health": re.compile(r"food poisoning|\bsick\b|vomit|allerg|hospital|nausea|diarrh|\bill\b|ambulance", re.I),
    "legal": re.compile(r"lawyer|attorney|\bsue\b|lawsuit|legal action|health department|police", re.I),
    "discrimination/harassment": re.compile(r"racis|discriminat|harass|sexist|slur", re.I),
}
PROMISE_RE = re.compile(
    r"refund|compensat|reimburs|free meal|voucher|gift card|discount|on the house|complimentary", re.I
)


def _digits(s: str) -> int:
    return len(re.sub(r"\D", "", s))


def _phone_sub(match: "re.Match") -> str:
    return "[removed]" if 9 <= _digits(match.group(0)) <= 15 else match.group(0)


def redact(text: str):
    """Return (clean_text, kinds_found). Removes emails and phone-like numbers."""
    found = []
    if EMAIL_RE.search(text):
        found.append("email")
        text = EMAIL_RE.sub("[removed]", text)
    cleaned = PHONE_RE.sub(_phone_sub, text)
    if cleaned != text:
        found.append("phone")
    return cleaned, found


def analyze_review(text: str):
    """Reasons a human should look at this review before replying."""
    reasons = []
    if INJECTION_RE.search(text):
        reasons.append("Review contains text that looks like instructions to the AI (ignored)")
    for kind, rx in SENSITIVE.items():
        if rx.search(text):
            reasons.append(f"Review mentions a sensitive topic ({kind}); check the reply carefully")
    return reasons


def _has_phone(text: str) -> bool:
    return any(9 <= _digits(m.group(0)) <= 15 for m in PHONE_RE.finditer(text))


def check_reply(text: str):
    """Problems in a drafted reply: private data or promises of money/compensation."""
    return {
        "private": bool(EMAIL_RE.search(text) or _has_phone(text)),
        "promises": bool(PROMISE_RE.search(text)),
    }


def first_name(name: str) -> str:
    token = (name or "").strip().split(" ")[0] if name else ""
    token = re.sub(r"[^A-Za-z\u00C0-\u024F'\-]", "", token)[:30]
    return token or "there"

from dataclasses import dataclass

import anthropic
import structlog

from src.config import get_settings

log = structlog.get_logger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_HTML_CHARS = 30000  # trim large pages before sending to LLM


@dataclass
class PriceData:
    price: float | None
    currency: str
    available: bool
    confidence: str  # high | medium | low


@dataclass
class ListingData:
    title: str | None
    price: float | None
    currency: str | None
    condition: str | None
    seller: str | None
    url: str | None
    confidence: str


@dataclass
class ArbitraryResult:
    answer: str
    confidence: str


def _get_client() -> anthropic.Anthropic:
    settings = get_settings()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _trim_html(html: str) -> str:
    return html[:MAX_HTML_CHARS]


async def extract_price(html: str, context: str) -> PriceData:
    """Use Claude haiku to extract price data from HTML."""
    client = _get_client()
    prompt = (
        f"Context: {context}\n\n"
        f"HTML content:\n{_trim_html(html)}\n\n"
        "Extract the price and availability from this HTML. "
        "Respond with ONLY valid JSON in this exact format:\n"
        '{"price": <float or null>, "currency": "<USD/EUR/GBP/etc>", "available": <true/false>, "confidence": "<high/medium/low>"}\n'
        "If you cannot determine something, use null or false."
    )

    message = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    import json
    try:
        raw = message.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return PriceData(
            price=data.get("price"),
            currency=data.get("currency", "USD"),
            available=bool(data.get("available", False)),
            confidence=data.get("confidence", "low"),
        )
    except Exception as exc:
        log.warning("extract_price.parse_failed", error=str(exc))
        return PriceData(price=None, currency="USD", available=False, confidence="low")


async def extract_listing(html: str, context: str) -> ListingData:
    """Extract structured listing data (title, price, condition, seller, url)."""
    client = _get_client()
    prompt = (
        f"Context: {context}\n\n"
        f"HTML content:\n{_trim_html(html)}\n\n"
        "Extract listing data from this HTML. "
        "Respond with ONLY valid JSON in this exact format:\n"
        '{"title": "<string or null>", "price": <float or null>, "currency": "<string or null>", '
        '"condition": "<string or null>", "seller": "<string or null>", "url": "<string or null>", "confidence": "<high/medium/low>"}'
    )

    message = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    import json
    try:
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return ListingData(
            title=data.get("title"),
            price=data.get("price"),
            currency=data.get("currency"),
            condition=data.get("condition"),
            seller=data.get("seller"),
            url=data.get("url"),
            confidence=data.get("confidence", "low"),
        )
    except Exception as exc:
        log.warning("extract_listing.parse_failed", error=str(exc))
        return ListingData(
            title=None, price=None, currency=None,
            condition=None, seller=None, url=None, confidence="low",
        )


async def extract_arbitrary(html: str, question: str) -> ArbitraryResult:
    """Answer an arbitrary question about page content."""
    client = _get_client()
    prompt = (
        f"Question: {question}\n\n"
        f"HTML content:\n{_trim_html(html)}\n\n"
        "Answer the question based on the HTML content above. "
        "Respond with ONLY valid JSON:\n"
        '{"answer": "<your answer>", "confidence": "<high/medium/low>"}'
    )

    message = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    import json
    try:
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return ArbitraryResult(
            answer=data.get("answer", ""),
            confidence=data.get("confidence", "low"),
        )
    except Exception as exc:
        log.warning("extract_arbitrary.parse_failed", error=str(exc))
        return ArbitraryResult(answer="Could not extract answer", confidence="low")

import structlog

from src.config import get_settings

log = structlog.get_logger(__name__)

FROM_EMAIL = "Market Sniper <alerts@marketsniper.app>"


def _build_html(snipe: dict, result) -> str:
    name = snipe.get("name", "Your Snipe")
    summary = result.summary or "Condition met."
    price = result.data.get("price")
    currency = result.data.get("currency", "USD")
    available = result.data.get("available")
    item_url = result.data.get("url", "")
    platform = result.data.get("platform", "")
    snipe_id = snipe.get("id", "")

    price_str = f"{currency} {price:.2f}" if price is not None else "—"
    avail_str = "In Stock" if available else ("Out of Stock" if available is False else "—")

    pause_link = f"https://marketsniper.app/snipes/{snipe_id}/pause"
    view_link = f"https://marketsniper.app/snipes/{snipe_id}"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f4f4f5; margin: 0; padding: 0; }}
    .container {{ max-width: 560px; margin: 40px auto; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
    .header {{ background: #0f172a; padding: 28px 32px; }}
    .header h1 {{ color: #fff; margin: 0; font-size: 20px; font-weight: 700; }}
    .header span {{ color: #22d3ee; }}
    .body {{ padding: 32px; }}
    .alert-box {{ background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; }}
    .alert-box p {{ margin: 0; color: #166534; font-weight: 600; font-size: 15px; }}
    .details {{ margin-bottom: 24px; }}
    .detail-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f1f5f9; font-size: 14px; }}
    .detail-row:last-child {{ border-bottom: none; }}
    .detail-label {{ color: #64748b; }}
    .detail-value {{ color: #0f172a; font-weight: 600; }}
    .cta {{ text-align: center; margin: 28px 0 8px; }}
    .btn {{ display: inline-block; background: #0f172a; color: #fff; text-decoration: none; padding: 12px 28px; border-radius: 8px; font-weight: 600; font-size: 14px; }}
    .btn-secondary {{ display: inline-block; color: #64748b; text-decoration: none; font-size: 13px; margin-top: 12px; }}
    .footer {{ background: #f8fafc; padding: 20px 32px; text-align: center; font-size: 12px; color: #94a3b8; }}
    .footer a {{ color: #64748b; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Market <span>Sniper</span> &mdash; Target acquired</h1>
    </div>
    <div class="body">
      <div class="alert-box">
        <p>Your snipe "{name}" was triggered!</p>
      </div>
      <p style="color:#374151;font-size:15px;margin:0 0 24px;">{summary}</p>
      <div class="details">
        <div class="detail-row">
          <span class="detail-label">Price</span>
          <span class="detail-value">{price_str}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">Availability</span>
          <span class="detail-value">{avail_str}</span>
        </div>
        {"" if not platform else f'<div class="detail-row"><span class="detail-label">Platform</span><span class="detail-value">{platform}</span></div>'}
        {"" if not item_url else f'<div class="detail-row"><span class="detail-label">Link</span><span class="detail-value"><a href="{item_url}" style="color:#0ea5e9;">View item &rarr;</a></span></div>'}
      </div>
      <div class="cta">
        {"" if not item_url else f'<a href="{item_url}" class="btn">Go to Item</a><br>'}
        <a href="{view_link}" style="display:inline-block;margin-top:12px;color:#0ea5e9;font-size:13px;">View snipe dashboard</a><br>
        <a href="{pause_link}" class="btn-secondary">Pause this snipe</a>
      </div>
    </div>
    <div class="footer">
      Powered by <a href="https://openswarm.world">SWARM</a> &middot; Market Sniper
      <br>You received this because you set up a snipe alert.
    </div>
  </div>
</body>
</html>"""


async def send_trigger_email(to: str, snipe: dict, result) -> bool:
    """
    Send trigger notification via Resend.
    Subject: "Market Sniper: {snipe.name} triggered!"
    """
    import resend

    settings = get_settings()
    if not settings.resend_api_key:
        log.warning("email.no_resend_key")
        return False

    resend.api_key = settings.resend_api_key
    name = snipe.get("name", "Snipe")

    try:
        params: resend.Emails.SendParams = {
            "from": FROM_EMAIL,
            "to": [to],
            "subject": f"Market Sniper: {name} triggered!",
            "html": _build_html(snipe, result),
        }
        email = resend.Emails.send(params)
        log.info("email.sent", to=to, email_id=email.get("id"))
        return True
    except Exception as exc:
        log.error("email.send_failed", to=to, error=str(exc))
        return False

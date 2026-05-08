import os
import smtplib
import yaml
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
CONFIG = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
ALERT_EMAIL = CONFIG["alerts"]["email"]


def _send_email(subject: str, body_html: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")

    if not smtp_user or smtp_user == "your_sending_email_here":
        print(f"[ALERT - email not configured]\n{subject}\n{body_html}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ALERT_EMAIL
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, ALERT_EMAIL, msg.as_string())
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def _signal_row_html(s: dict) -> str:
    tier_colors = {
        "Strong Buy": "#16a34a", "Buy": "#22c55e",
        "Watch": "#f59e0b", "Hold": "#6b7280",
        "Avoid": "#f97316", "Sell": "#ef4444", "Strong Sell": "#b91c1c",
    }
    color = tier_colors.get(s["tier"], "#6b7280")
    reasons = "<br>".join(f"• {r}" for r in s.get("reasons", [])[:3])
    size = s.get("sizing", {})
    amount = f"${size.get('suggested_dollars', 0):,.0f} ({size.get('suggested_shares', 0)} shares)" if size else "—"
    return f"""
    <tr>
      <td style="padding:8px;font-weight:bold">{s['ticker']}</td>
      <td style="padding:8px;color:{color};font-weight:bold">{s['tier']}</td>
      <td style="padding:8px">${s.get('price', 0):,.2f}</td>
      <td style="padding:8px">{amount}</td>
      <td style="padding:8px;font-size:12px">{reasons}</td>
    </tr>"""


def send_daily_digest(signals: list[dict], portfolio_summary: dict, market_context: dict) -> bool:
    date_str = datetime.now().strftime("%B %d, %Y")
    vix = market_context.get("vix", {})
    fg = market_context.get("fear_greed", {})

    actionable = [s for s in signals if s["tier"] in ("Strong Buy", "Buy", "Sell", "Strong Sell")]
    watch = [s for s in signals if s["tier"] == "Watch"]

    signal_rows = "".join(_signal_row_html(s) for s in actionable) or "<tr><td colspan='5'>No actionable signals today.</td></tr>"
    watch_tickers = ", ".join(s["ticker"] for s in watch) or "None"

    pnl = portfolio_summary.get("total_pnl", 0)
    pnl_color = "#16a34a" if pnl >= 0 else "#ef4444"
    flags_html = "".join(f"<li>{f}</li>" for f in portfolio_summary.get("flags", [])) or "<li>None</li>"

    html = f"""
<html><body style="font-family:sans-serif;max-width:700px;margin:auto">
  <h2 style="color:#1e293b">Stock Model — Daily Digest</h2>
  <p style="color:#64748b">{date_str}</p>

  <h3>Market Context</h3>
  <p>{vix.get('note', '')} &nbsp;|&nbsp; Fear & Greed: <b>{fg.get('label', 'N/A')}</b> ({fg.get('value', 'N/A')})</p>

  <h3>Portfolio</h3>
  <p>Total value: <b>${portfolio_summary.get('total_value', 0):,.2f}</b> &nbsp;
     P&L: <b style="color:{pnl_color}">${pnl:+,.2f} ({portfolio_summary.get('total_pnl_pct', 0):+.2f}%)</b></p>
  <p>Concentration flags: <ul>{flags_html}</ul></p>

  <h3>Actionable Signals</h3>
  <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0">
    <tr style="background:#f8fafc">
      <th style="padding:8px;text-align:left">Ticker</th>
      <th style="padding:8px;text-align:left">Signal</th>
      <th style="padding:8px;text-align:left">Price</th>
      <th style="padding:8px;text-align:left">Suggested</th>
      <th style="padding:8px;text-align:left">Top Reasons</th>
    </tr>
    {signal_rows}
  </table>

  <h3>Watching</h3>
  <p>{watch_tickers}</p>
</body></html>"""

    return _send_email(f"Stock Model — {date_str}", html)


def send_strong_signal_alert(signal: dict) -> bool:
    tier = signal["tier"]
    ticker = signal["ticker"]
    color = "#16a34a" if "Buy" in tier else "#b91c1c"
    reasons = "<br>".join(f"• {r}" for r in signal.get("reasons", []))
    size = signal.get("sizing", {})
    amount = f"${size.get('suggested_dollars', 0):,.0f} ({size.get('suggested_shares', 0)} shares)" if size else "—"

    html = f"""
<html><body style="font-family:sans-serif;max-width:500px;margin:auto">
  <h2 style="color:{color}">{tier}: {ticker}</h2>
  <p>Price: <b>${signal.get('price', 0):,.2f}</b> &nbsp; Suggested: <b>{amount}</b></p>
  <h3>Why this signal fired:</h3>
  <p>{reasons}</p>
</body></html>"""

    return _send_email(f"{tier} Alert: {ticker}", html)

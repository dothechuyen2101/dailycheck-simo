"""
Alert Manager – Gửi cảnh báo qua Email và Webhook (Slack/Teams).
Tích hợp cơ chế chống spam: mỗi (system, host, check_name) chỉ alert 1 lần/ngày.
"""
import json
import logging
import smtplib
import urllib.request
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config_manager import get
from src.logger import CheckResult

logger = logging.getLogger("dailycheck.alert")

# Bộ nhớ chống spam: set các key đã alert hôm nay
_alerted_today: set[str] = set()
_alerted_date: date = date.today()


def _reset_if_new_day():
    global _alerted_today, _alerted_date
    today = date.today()
    if today != _alerted_date:
        _alerted_today.clear()
        _alerted_date = today


def _alert_key(r: CheckResult) -> str:
    return f"{r.system}|{r.host}|{r.check_name}"


STATUS_EMOJI = {
    "OK": "✅",
    "WARNING": "⚠️",
    "CRITICAL": "🔴",
    "ERROR": "❌",
}


class AlertManager:
    def __init__(self):
        self.email_cfg = get("notifications.email", {})
        self.webhook_cfg = get("notifications.webhook", {})

    def should_alert(self, r: CheckResult) -> bool:
        """Chỉ alert cho CRITICAL/ERROR và chưa alert hôm nay."""
        _reset_if_new_day()
        if r.status not in ("CRITICAL", "ERROR"):
            return False
        key = _alert_key(r)
        if key in _alerted_today:
            return False
        _alerted_today.add(key)
        return True

    def alert(self, r: CheckResult):
        """Gửi alert nếu thoả điều kiện."""
        if not self.should_alert(r):
            return
        emoji = STATUS_EMOJI.get(r.status, "❗")
        subject = f"[SIMO DailyCheck] {emoji} {r.status}: {r.system} – {r.check_name}"
        body = (
            f"**Hệ thống:** {r.system}\n"
            f"**Host:** {r.host}\n"
            f"**Kiểm tra:** {r.check_name}\n"
            f"**Trạng thái:** {r.status}\n"
            f"**Thông báo:** {r.message}\n"
            f"**Thời gian:** {r.checked_at}\n"
        )
        if self.email_cfg.get("enabled"):
            self._send_email(subject, body)
        if self.webhook_cfg.get("enabled"):
            self._send_webhook(subject, body)

    def send_report(self, subject: str, html_body: str):
        """Gửi báo cáo tổng hợp HTML (không chống spam)."""
        if self.email_cfg.get("enabled"):
            self._send_email(subject, html_body, is_html=True)

    def _send_email(self, subject: str, body: str, is_html: bool = False):
        cfg = self.email_cfg
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = cfg.get("sender", "dailycheck@simo.vn")
            recipients = cfg.get("recipients", [])
            msg["To"] = ", ".join(recipients)

            part = MIMEText(body, "html" if is_html else "plain", "utf-8")
            msg.attach(part)

            with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587)) as server:
                if cfg.get("smtp_use_tls", True):
                    server.starttls()
                if cfg.get("password"):
                    server.login(cfg["sender"], cfg["password"])
                server.sendmail(cfg["sender"], recipients, msg.as_string())
            logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.error(f"Email send failed: {e}")

    def _send_webhook(self, subject: str, body: str):
        cfg = self.webhook_cfg
        try:
            payload = json.dumps({"text": f"*{subject}*\n{body}"}).encode("utf-8")
            req = urllib.request.Request(
                cfg["url"],
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
            logger.info(f"Webhook sent: {subject}")
        except Exception as e:
            logger.error(f"Webhook send failed: {e}")

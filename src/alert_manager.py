"""
Alert Manager – Gửi một báo cáo tổng hợp cho mỗi lần chạy Daily Check.
"""
import json
import logging
import socket
import ssl
import smtplib
import time
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


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
        """Giữ tương thích ngược; không gửi alert riêng lẻ nữa."""
        logger.info(
            "Per-result alert suppressed for %s/%s/%s; consolidated report mode is enabled.",
            r.system,
            r.host,
            r.check_name,
        )

    def send_report(self, subject: str, html_body: str, recipients: list[str] | None = None):
        """Gửi báo cáo tổng hợp HTML (không chống spam)."""
        if self.email_cfg.get("enabled"):
            self._send_email(subject, html_body, is_html=True, recipients=recipients)

    def _send_email(self, subject: str, body: str, is_html: bool = False, recipients: list[str] | None = None):
        cfg = self.email_cfg
        smtp_host = (cfg.get("smtp_host") or "").strip()
        smtp_connect_host = (cfg.get("smtp_connect_host") or smtp_host).strip()
        recipients = recipients or cfg.get("recipients", [])
        smtp_port = int(cfg.get("smtp_port", 587))
        smtp_timeout = float(cfg.get("smtp_timeout_sec", 15))
        username = (cfg.get("username") or cfg.get("sender") or "").strip()
        password = cfg.get("password")
        use_tls = _as_bool(cfg.get("smtp_use_tls"), default=(smtp_port == 587))
        use_ssl = _as_bool(cfg.get("smtp_use_ssl"), default=(smtp_port == 465))
        require_auth = _as_bool(cfg.get("smtp_require_auth"), default=bool(password))

        if not smtp_host:
            logger.error("Email send skipped: notifications.email.smtp_host is empty")
            return
        if not smtp_connect_host:
            logger.error(
                "Email send skipped: notifications.email.smtp_connect_host is empty"
            )
            return
        if smtp_host == "smtp.company.com":
            logger.error(
                "Email send skipped: notifications.email.smtp_host is still the placeholder "
                "'smtp.company.com'. Set a real SMTP server in config/config.yaml."
            )
            return
        if not recipients:
            logger.error("Email send skipped: notifications.email.recipients is empty")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = cfg.get("sender", "chuyen.dothe@sbv.gov.vn")
            msg["To"] = ", ".join(recipients)

            part = MIMEText(body, "html" if is_html else "plain", "utf-8")
            msg.attach(part)

            retry_count = max(int(cfg.get("smtp_resolve_retries", 3)), 1)
            retry_delay = float(cfg.get("smtp_resolve_retry_delay_sec", 2))
            last_resolve_error = None

            # Resolve before connecting so DNS failures are explicit and retryable.
            for attempt in range(1, retry_count + 1):
                try:
                    socket.getaddrinfo(
                        smtp_connect_host,
                        smtp_port,
                        type=socket.SOCK_STREAM,
                    )
                    last_resolve_error = None
                    break
                except socket.gaierror as e:
                    last_resolve_error = e
                    if attempt < retry_count:
                        time.sleep(retry_delay)

            if last_resolve_error is not None:
                raise last_resolve_error

            smtp_client_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
            with smtp_client_cls(
                smtp_connect_host,
                smtp_port,
                timeout=smtp_timeout,
            ) as server:
                if not use_ssl:
                    server.ehlo()

                if use_tls and not use_ssl:
                    if server.has_extn("starttls"):
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    else:
                        logger.warning(
                            "SMTP server %s:%s does not advertise STARTTLS; "
                            "continuing without TLS.",
                            smtp_connect_host,
                            smtp_port,
                        )

                if password:
                    if server.has_extn("auth"):
                        server.login(username, password)
                    elif require_auth:
                        logger.warning(
                            "SMTP server %s:%s does not advertise AUTH; "
                            "skipping SMTP login.",
                            smtp_connect_host,
                            smtp_port,
                        )

                server.sendmail(cfg["sender"], recipients, msg.as_string())
            logger.info(f"Email sent: {subject}")
        except ssl.SSLError as e:
            logger.error(
                "Email send failed during TLS negotiation with %s:%s: %s. "
                "If this SMTP server expects plain SMTP, set "
                "notifications.email.smtp_use_tls to false. If it expects implicit SSL, "
                "set notifications.email.smtp_use_ssl to true and use port 465.",
                smtp_connect_host,
                smtp_port,
                e,
            )
        except socket.gaierror as e:
            logger.error(
                "Email send failed: cannot resolve SMTP target "
                f"'{smtp_connect_host}' for configured SMTP host '{smtp_host}': {e}. "
                "This usually indicates a DNS, VPN, or network routing issue. "
                "Set notifications.email.smtp_connect_host to a reachable IP/host "
                "if DNS for smtp_host is unavailable on this machine."
            )
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

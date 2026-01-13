# server/app/infrastructure/notifications/providers/email_provider.py

import smtplib
from email.mime.text import MIMEText
from typing import Optional
from app.core.config import settings

class EmailProvider:
    """
    Envoi d'e-mails via SMTP simple (texte).
    Pré-requis: settings.SMTP_HOST et settings.SMTP_FROM.
    """

    def __init__(self):
        if not settings.SMTP_HOST:
            raise ValueError("SMTP_HOST not configured")
        if not settings.SMTP_FROM:
            raise ValueError("SMTP_FROM not configured")

        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.username = settings.SMTP_USERNAME
        self.password = settings.SMTP_PASSWORD
        self.use_tls = settings.SMTP_USE_TLS
        self.sender = settings.SMTP_FROM

    def send(self, *, to: str, subject: str, body: str) -> bool:
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = to

        if self.use_tls:
            server = smtplib.SMTP(self.host, self.port, timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=10)

        try:
            if self.username and self.password:
                server.login(self.username, self.password)
            server.sendmail(self.sender, [to], msg.as_string())
            return True
        finally:
            server.quit()

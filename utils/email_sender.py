import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)


def send_verification_email(receiver_email: str, code: str, ttl_seconds: int) -> None:
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD are not configured")

    ttl_minutes = max(1, ttl_seconds // 60)

    subject = "Your verification code"
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #222;">
        <h2>Email verification</h2>
        <p>Your verification code is:</p>
        <div style="font-size: 28px; font-weight: bold; letter-spacing: 6px; margin: 16px 0;">
          {code}
        </div>
        <p>This code will expire in <b>{ttl_minutes} minutes</b>.</p>
        <p>If you didn’t request this, ignore this email.</p>
      </body>
    </html>
    """

    text = (
        f"Email verification\n\n"
        f"Your verification code is: {code}\n"
        f"This code will expire in {ttl_minutes} minutes.\n\n"
        f"If you didn’t request this, ignore this email."
    )

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = receiver_email

    message.attach(MIMEText(text, "plain"))
    message.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, receiver_email, message.as_string())
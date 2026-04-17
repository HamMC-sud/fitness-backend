import os
import time
import smtplib
import socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid


SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)


def _build_message(receiver_email: str, code: str, ttl_seconds: int) -> MIMEMultipart:
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
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()

    message.attach(MIMEText(text, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))

    return message


def _send_message(receiver_email: str, message: MIMEMultipart) -> None:
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message, from_addr=SMTP_FROM, to_addrs=[receiver_email])
        return

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.ehlo()

        if SMTP_PORT == 587:
            server.starttls()
            server.ehlo()

        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message, from_addr=SMTP_FROM, to_addrs=[receiver_email])


def send_verification_email(
    receiver_email: str,
    code: str,
    ttl_seconds: int,
) -> bool:
    """
    Returns:
        True  -> email sent
        False -> email was not sent
    """

    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD are not configured")

    message = _build_message(receiver_email, code, ttl_seconds)

    max_attempts = 5
    base_delay_seconds = 2

    for attempt in range(1, max_attempts + 1):
        try:
            _send_message(receiver_email, message)
            print(f"[EMAIL] sent to {receiver_email}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            print(f"[EMAIL AUTH ERROR] {e}")
            return False

        except smtplib.SMTPRecipientsRefused as e:
            print(f"[EMAIL RECIPIENT ERROR] {e}")
            return False

        except (
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPConnectError,
            smtplib.SMTPHeloError,
            smtplib.SMTPDataError,
            smtplib.SMTPResponseException,
            socket.timeout,
            TimeoutError,
        ) as e:
            print(f"[EMAIL RETRYABLE ERROR] attempt {attempt}/{max_attempts}: {e}")

            if attempt < max_attempts:
                delay = base_delay_seconds * (2 ** (attempt - 1))
                time.sleep(delay)
            else:
                print(f"[EMAIL FAILED] could not send to {receiver_email}")
                return False

        except Exception as e:
            print(f"[EMAIL UNKNOWN ERROR] {e}")
            return False

    return False
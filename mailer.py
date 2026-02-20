# mailer.py
import smtplib
import tomllib
from email.message import EmailMessage
from pathlib import Path

def load_config():
    with open("config.toml", "rb") as f:
        return tomllib.load(f)

def send_email(to_email: str, subject: str, body: str, attachments: list[Path]):
    cfg = load_config()
    host = cfg["mail"]["smtp_host"]
    port = int(cfg["mail"]["smtp_port"])
    user = cfg["mail"]["smtp_user"]
    pwd  = cfg["mail"]["smtp_password"]
    from_name = cfg["mail"]["from_name"]

    msg = EmailMessage()
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    for p in attachments:
        data = p.read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=p.name)

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)

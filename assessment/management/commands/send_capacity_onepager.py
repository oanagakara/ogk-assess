"""
Personalize docs/wc_cet_college_capacity_onepager.html with a per-recipient
"Prepared for" watermark and email it to one named recipient at a time.

A timestamped, named copy of exactly what was sent is kept locally in
perf/locust-style fashion under scripts/sent_copies/ (gitignored) — the point
is an accountable, per-recipient record: if a specific copy ever leaks, the
watermark in the document identifies which one, and this directory plus your
own sent-mail log identify who it went to and when.

Usage:
  python manage.py send_capacity_onepager --name "Jane Doe" --title "Principal" \
      --email jane@example.org --dry-run

  # Once the dry-run output looks right, drop --dry-run to actually send.

Required environment variables (set in your shell, never committed):
  SPONSOR_EMAIL_HOST      e.g. smtp.zoho.com
  SPONSOR_EMAIL_PORT      e.g. 465 (SSL) — defaults to 465 if unset
  SPONSOR_EMAIL_USER      your sending mailbox address
  SPONSOR_EMAIL_PASSWORD  app/account password
  SPONSOR_EMAIL_FROM      optional — defaults to SPONSOR_EMAIL_USER
"""
import os
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

TEMPLATE = Path(settings.BASE_DIR) / "docs" / "wc_cet_college_capacity_onepager.html"
SENT_DIR = Path(settings.BASE_DIR) / "scripts" / "sent_copies"
WATERMARK_ANCHOR = "<!-- PREPARED_FOR_WATERMARK -->"

REQUIRED_ENV = ["SPONSOR_EMAIL_HOST", "SPONSOR_EMAIL_USER", "SPONSOR_EMAIL_PASSWORD"]

DEFAULT_SUBJECT = "NQF Learner Placement Assessment Platform — Reliability & Capacity Readiness"
DEFAULT_BODY = (
    "Hi {first_name},\n\n"
    "Attached is a short reliability and capacity readiness summary ahead of our discussion.\n\n"
    "This copy is prepared specifically for you and marked accordingly — please treat it as "
    "confidential. Happy to share the full underlying test/security reports on request.\n\n"
    "Kind regards"
)


def slugify(name: str) -> str:
    return "-".join(name.strip().split()).replace("/", "-")


class Command(BaseCommand):
    help = "Personalize and email the WC CET College capacity one-pager to a named recipient"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Recipient's full name")
        parser.add_argument("--title", required=True, help="Recipient's title/role")
        parser.add_argument("--email", required=True, help="Recipient's email address")
        parser.add_argument("--subject", default=DEFAULT_SUBJECT)
        parser.add_argument("--body", default=DEFAULT_BODY, help="Use {first_name} as a placeholder")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Write the personalized copy locally without sending anything",
        )

    def handle(self, *args, **options):
        if not TEMPLATE.exists():
            raise CommandError(f"Template not found: {TEMPLATE}")

        name = options["name"]
        title = options["title"]
        email = options["email"]
        sent_date = date.today()

        source = TEMPLATE.read_text(encoding="utf-8")
        if WATERMARK_ANCHOR not in source:
            raise CommandError(f"Watermark anchor not found in {TEMPLATE} — has the template changed?")
        watermark = f"Prepared for {name}, {title} &mdash; {sent_date:%d %B %Y}<br>"
        personalized_html = source.replace(WATERMARK_ANCHOR, watermark)

        SENT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SENT_DIR / f"{sent_date.isoformat()}_{slugify(name)}.html"
        out_path.write_text(personalized_html, encoding="utf-8")
        self.stdout.write(f"Personalized copy written: {out_path}")

        if options["dry_run"]:
            self.stdout.write("--dry-run set: nothing sent. Review the file above, then re-run without --dry-run.")
            return

        missing = [key for key in REQUIRED_ENV if not os.environ.get(key)]
        if missing:
            raise CommandError(
                "Missing required environment variable(s): " + ", ".join(missing)
            )

        body = options["body"].format(first_name=name.split()[0])
        self._send_email(email, name, out_path, options["subject"], body)
        self.stdout.write(self.style.SUCCESS(f"Sent to {name} <{email}> ({sent_date.isoformat()})"))

    def _send_email(self, to_email, to_name, attachment_path, subject, body):
        host = os.environ["SPONSOR_EMAIL_HOST"]
        port = int(os.environ.get("SPONSOR_EMAIL_PORT", "465"))
        user = os.environ["SPONSOR_EMAIL_USER"]
        password = os.environ["SPONSOR_EMAIL_PASSWORD"]
        from_addr = os.environ.get("SPONSOR_EMAIL_FROM", user)

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = f"{to_name} <{to_email}>"
        msg.set_content(body)
        msg.add_attachment(
            attachment_path.read_bytes(),
            maintype="text",
            subtype="html",
            filename=attachment_path.name,
        )

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context) as server:
            server.login(user, password)
            server.send_message(msg)

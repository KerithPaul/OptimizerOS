import email

from app.core.config import get_settings
from app.models.report import SiteReportEmailStatus
from app.services.mailer import deliver_site_report, parse_recipients


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in = None
        self.sent = None
        self.started_tls = False
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return True

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def sendmail(self, from_addr, to_addrs, msg):
        self.sent = (from_addr, list(to_addrs), msg)


def test_parse_recipients_dedupes_and_skips_junk() -> None:
    assert parse_recipients("Ada@x.com, ada@x.com; bob@x.com, nope") == [
        "Ada@x.com",
        "bob@x.com",
    ]


def test_deliver_unavailable_without_smtp() -> None:
    settings = get_settings().model_copy(
        update={"smtp_host": "", "smtp_user": "", "smtp_password": ""}
    )
    result = deliver_site_report(
        html="<p>hi</p>",
        document={"project_name": "demo"},
        recipients=["a@x.com"],
        settings=settings,
    )
    assert result.status is SiteReportEmailStatus.UNAVAILABLE
    assert "SMTP is not configured" in result.detail


def test_deliver_unavailable_without_recipients() -> None:
    settings = get_settings().model_copy(
        update={
            "smtp_host": "smtp.gmail.com",
            "smtp_user": "user@example.com",
            "smtp_password": "secret",
            "smtp_from": "Architectos@lexfintech.io",
        }
    )
    result = deliver_site_report(
        html="<p>hi</p>",
        document={"project_name": "demo"},
        recipients=[],
        settings=settings,
    )
    assert result.status is SiteReportEmailStatus.UNAVAILABLE
    assert "no recipients" in result.detail


def test_deliver_sends_html_over_starttls(monkeypatch) -> None:
    _FakeSMTP.instances = []
    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", _FakeSMTP)
    settings = get_settings().model_copy(
        update={
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_password": "abcd efgh ijkl mnop",
            "smtp_from": "Architectos@lexfintech.io",
        }
    )
    result = deliver_site_report(
        html="<h1>12,000 impressions</h1>",
        document={
            "project_name": "Dr.Moksha",
            "website_url": "https://www.drmoksha.com/",
            "totals": {"impressions": {"status": "measured", "value": 12000}},
        },
        recipients=["manager@lexfintech.io"],
        settings=settings,
    )
    assert result.status is SiteReportEmailStatus.SENT
    assert "manager@lexfintech.io" in result.detail
    smtp = _FakeSMTP.instances[0]
    assert smtp.host == "smtp.gmail.com"
    assert smtp.started_tls is True
    assert smtp.logged_in == ("user@example.com", "abcdefghijklmnop")
    assert smtp.sent[0] == "Architectos@lexfintech.io"
    assert smtp.sent[1] == ["manager@lexfintech.io"]
    parsed = email.message_from_string(smtp.sent[2])
    decoded: list[str] = []
    for part in parsed.walk():
        payload = part.get_payload(decode=True)
        if payload:
            decoded.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    body = "\n".join(decoded)
    assert "Impressions: 12,000" in body
    assert "<h1>12,000 impressions</h1>" in body
    assert "secret" not in result.detail
    assert "abcdefghijklmnop" not in result.detail
    assert "secret" not in smtp.sent[2]

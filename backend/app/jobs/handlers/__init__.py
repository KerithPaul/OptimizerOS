"""Importing this package registers every job handler as a side effect."""

from app.jobs.handlers import agent_run  # noqa: F401
from app.jobs.handlers import audit  # noqa: F401
from app.jobs.handlers import cms_change  # noqa: F401
from app.jobs.handlers import code_change  # noqa: F401
from app.jobs.handlers import health_ping  # noqa: F401
from app.jobs.handlers import repository_clone  # noqa: F401
from app.jobs.handlers import site_report  # noqa: F401
from app.jobs.handlers import website_crawl  # noqa: F401

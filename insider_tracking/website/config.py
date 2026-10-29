"""
config.py - where the website lives, and where its files are.

Settings (environment variables, the same secrets the collectors use):
  SUPABASE_DB_URL, SUPABASE_DB_PASSWORD   the database
  SITE_BASE_PATH   where the site lives on its domain, e.g. "/insider-tracking"
                   (worked out from GITHUB_REPOSITORY when not set; "/" for a custom domain)
  SITE_URL         the full address, e.g. "https://mykedestroyer64.github.io/insider-tracking"
"""

from dataclasses import dataclass

from ..runs.platform import REPOSITORY

SITE_FOLDER = REPOSITORY / "site"
TEMPLATES = SITE_FOLDER / "templates"
STATIC = SITE_FOLDER / "static"
CACHE = SITE_FOLDER / "cache"                 # last week's copy of the legislators list
OVERRIDES = SITE_FOLDER / "overrides.json"    # filer key -> bioguide ID, for names that don't match


@dataclass(frozen=True)
class SiteConfig:
    repo: str        # "owner/name" on GitHub
    base: str        # "" or "/insider-tracking": what every link on the site starts with
    url: str         # the full address, without a trailing slash

    @classmethod
    def read(cls, environ, base=None, site_url=None):
        """From the environment; `base` and `site_url` (command-line options) win when given."""
        repo = environ.get("GITHUB_REPOSITORY", "mykedestroyer64/insider-tracking")
        owner, _, name = repo.partition("/")
        raw = (environ.get("SITE_BASE_PATH") or "").strip()
        if raw:                                   # "/" means the root of its own domain
            default_base = "" if raw == "/" else "/" + raw.strip("/")
        else:
            default_base = "" if name.lower() == f"{owner.lower()}.github.io" else f"/{name}"
        default_url = (environ.get("SITE_URL") or f"https://{owner.lower()}.github.io{default_base}").rstrip("/")
        return cls(repo=repo,
                   base=base.rstrip("/") if base is not None else default_base,
                   url=site_url.rstrip("/") if site_url else default_url)

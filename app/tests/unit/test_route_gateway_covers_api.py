"""The gateway's API allowlist must cover every router the app registers.

A router that is registered in FastAPI but missing from deploy/nginx/gagent.conf
works in development and answers with the SPA's index.html behind the gateway
(this is how /extensions shipped broken in #38). The reverse also matters: a
prefix that is also an SPA page route must keep serving the page at the bare path.
"""

from __future__ import annotations

import re
from pathlib import Path

CONF = (Path(__file__).resolve().parents[3] / "deploy" / "nginx" / "gagent.conf").read_text()
ANONYMOUS_LOCATIONS = {"auth", "sso"}  # have their own location blocks


def _regex_alternatives(marker: str) -> set[str]:
    match = re.search(r"location ~ \^/\(([^)]*)\)" + re.escape(marker), CONF)
    assert match, f"no location with {marker!r} in gagent.conf"
    return set(match.group(1).split("|"))


def test_every_registered_router_prefix_is_proxied_by_the_gateway():
    import app.routers  # noqa: F401  (populates the registry)
    from app.routers.registry import RouterRegistry

    proxied = _regex_alternatives(r"(/|$)") | ANONYMOUS_LOCATIONS
    missing = sorted(
        {entry.path.strip("/").split("/")[0] for entry in RouterRegistry.entries() if entry.path.strip("/")} - proxied
    )
    assert not missing, f"registered in FastAPI but not proxied by deploy/nginx/gagent.conf: {missing}"


def test_spa_page_routes_that_share_an_api_prefix_are_served_as_pages():
    spa_pages = _regex_alternatives(r"/?$")
    app_tsx = (Path(__file__).resolve().parents[3] / "web-ui" / "src" / "App.tsx").read_text()
    page_routes = set(re.findall(r'<Route path="/([a-z-]+)"', app_tsx))
    api = _regex_alternatives(r"(/|$)")
    collide = page_routes & api
    assert collide <= spa_pages, f"SPA page routes shadowed by the API allowlist: {sorted(collide - spa_pages)}"

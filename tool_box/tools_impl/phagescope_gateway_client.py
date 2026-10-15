"""HTTP client for the independent gateway; mutating requests are sent once."""
import math
from urllib.parse import quote, urlsplit

import httpx

from app.services.foundation.settings import get_settings
from ..phagescope_gateway_contract import PHAGE_FILTERS, ROUTES


class GatewayError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def channel_config(channel):
    settings = get_settings()
    common = (settings.phagescope_gateway_base_url or "").strip().rstrip("/")
    base = (getattr(settings, f"phagescope_gateway_{channel}_url") or common).strip().rstrip("/")
    key = getattr(settings, f"phagescope_gateway_{channel}_key") or ""
    suffix = f"/agent-api/v1/{channel}"
    # Accept either a service origin or its documented API root.
    if base and not base.endswith(suffix):
        base += suffix
    return base, key


def configured(channel):
    base, key = channel_config(channel)
    return {"url_configured": bool(base), "key_configured": bool(key), "ready": bool(base and key)}


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GatewayError("invalid_parameter", f"{name} must be a positive integer.")
    return value


def query_request(operation, params):
    channel, path, identifiers, fields = ROUTES[operation]
    allowed = set(identifiers) | fields | {"save"}
    unknown = set(params) - allowed
    if unknown:
        raise GatewayError("invalid_parameter", "Unsupported parameters: " + ", ".join(sorted(unknown)))
    ids = {}
    for name in identifiers:
        value = params.get(name)
        if name in {"phage_id", "job_id"}:
            value = str(positive(value, name))
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "/\\\r\n"):
            raise GatewayError("invalid_parameter", f"{name} is required and must be one identifier.")
        ids[name] = quote(value, safe="")
    query = {k: v for k, v in params.items() if k in fields}
    for name in ("page", "pagesize", "max_bases", "length_min", "length_max"):
        if name in query:
            if name.startswith("length_") and query[name] == 0 and not isinstance(query[name], bool):
                continue
            positive(query[name], name)
    if "pagesize" in query:
        query["pagesize"] = min(query["pagesize"], 50)
    if "max_bases" in query:
        query["max_bases"] = min(query["max_bases"], 200000)
    for name in ("gc_min", "gc_max"):
        if name in query and (isinstance(query[name], bool) or not isinstance(query[name], (int, float)) or not math.isfinite(query[name])):
            raise GatewayError("invalid_parameter", f"{name} must be a finite number.")
    if "include_sequence" in query:
        if not isinstance(query["include_sequence"], bool):
            raise GatewayError("invalid_parameter", "include_sequence must be boolean.")
        query["include_sequence"] = int(query["include_sequence"])
    if operation == "phages" and not any(query.get(k) is not None and query[k] != "" for k in PHAGE_FILTERS):
        raise GatewayError("missing_filter", "phages requires at least one catalog filter.")
    required = {"phage_features": "type", "hosts": "rank", "families": "protein_id", "results": "module"}.get(operation)
    if required and not query.get(required):
        raise GatewayError("invalid_parameter", f"{required} is required.")
    if operation == "structures":
        single, many = query.get("protein_id"), query.get("protein_ids")
        if bool(single) == bool(many):
            raise GatewayError("invalid_parameter", "Provide protein_id or protein_ids, not both.")
        if many:
            if not isinstance(many, list) or not 1 <= len(many) <= 20 or not all(isinstance(v, str) and v and "," not in v for v in many):
                raise GatewayError("invalid_parameter", "protein_ids must contain 1 to 20 protein identifiers.")
            query["protein_ids"] = ",".join(many)
    return channel, path.format(**ids), query


async def request(channel, path, *, timeout, method="GET", query=None, body=None):
    base, key = channel_config(channel)
    if not base or not key:
        raise GatewayError("configuration_required", f"Configure PHAGESCOPE_GATEWAY_BASE_URL (or PHAGESCOPE_GATEWAY_{channel.upper()}_URL) and PHAGESCOPE_GATEWAY_{channel.upper()}_KEY.", 503)
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GatewayError("configuration_error", "Gateway URL must be an HTTP(S) service origin or API root.", 503)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        response = await client.request(method, base + "/" + path, params=query, json=body,
                                        headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        payload = response.json()
    except ValueError:
        raise GatewayError("invalid_gateway_response", "Gateway returned a non-JSON response.", 502)
    return response.status_code, payload, response.headers.get("Retry-After")


def response(action, channel, status, payload, retry_after=None):
    ok = 200 <= status < 300 and not (isinstance(payload, dict) and payload.get("error"))
    out = {"success": ok, "action": "gateway", "gateway_action": action, "gateway_channel": channel,
           "status_code": status, "data": payload, "artifact_scope": "api_response_only"}
    if not ok:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        out.update(error_code=error.get("code", "gateway_http_error") if isinstance(error, dict) else "gateway_http_error",
                   error=error.get("message", "Gateway request failed.") if isinstance(error, dict) else str(error))
    if retry_after:
        out["retry_after"] = retry_after
    return out

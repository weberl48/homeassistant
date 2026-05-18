"""Shared HA REST client. Reads $SUPERVISOR_TOKEN from env (auto-provided inside SSH addon).
Fall back to a long-lived token at /config/.ha_token if SUPERVISOR_TOKEN is unset."""
import json, os, time, urllib.parse, urllib.request


def _token():
    """Prefer /config/.ha_token + 192.168.1.160:8123 — works from BOTH HA Core's
    shell_command env (where `http://supervisor` doesn't resolve) AND the SSH addon
    (where `localhost` would be the addon itself, not HA Core). Fall back to
    SUPERVISOR_TOKEN + http://supervisor for SSH-addon-only standalone runs."""
    fpath = "/config/.ha_token"
    if os.path.exists(fpath):
        return open(fpath).read().strip(), "http://192.168.1.160:8123/api"
    t = os.environ.get("SUPERVISOR_TOKEN")
    if t:
        return t, "http://supervisor/core/api"
    raise RuntimeError("No HA token available (/config/.ha_token or SUPERVISOR_TOKEN env)")


def _request(method, path, body=None, timeout=30):
    tok, base = _token()
    url = base + path
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if not raw or r.status == 204:
            return None
        return json.loads(raw)


def get_state(entity_id):
    """Read entity state. Returns dict with {entity_id, state, attributes, ...} or None."""
    try:
        return _request("GET", f"/states/{entity_id}")
    except Exception:
        return None


def state_value(entity_id, default=None):
    """Shortcut: read just the state value."""
    s = get_state(entity_id)
    return s.get("state") if s else default


def state_attr(entity_id, attr, default=None):
    """Shortcut: read a single attribute."""
    s = get_state(entity_id)
    if s:
        return s.get("attributes", {}).get(attr, default)
    return default


def set_state(entity_id, state, attributes=None):
    """Force-set entity state (works for input_text, input_number indirectly via service)."""
    body = {"state": state}
    if attributes:
        body["attributes"] = attributes
    return _request("POST", f"/states/{entity_id}", body=body)


def call_service(domain, service, data=None, target=None):
    """Call a HA service. Returns list of affected entity states, or [] on 204."""
    body = dict(data or {})
    if target:
        body.update(target)
    return _request("POST", f"/services/{domain}/{service}", body=body)


def wait_for_state(entity_id, target_state, timeout_s=10, poll_s=0.5):
    """Poll until entity reaches target_state or timeout. Returns True if reached."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if state_value(entity_id) == target_state:
            return True
        time.sleep(poll_s)
    return False


def wait_until(predicate, timeout_s=10, poll_s=0.5):
    """Poll until predicate() returns truthy. Returns True if reached."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(poll_s)
    return False


def spotify_token():
    """Pull live Spotify access token from HA's config_entries (kept fresh by the integration)."""
    cfg = json.load(open("/config/.storage/core.config_entries"))
    for e in cfg["data"]["entries"]:
        if e.get("domain") == "spotify":
            return e["data"]["token"]["access_token"]
    raise RuntimeError("Spotify integration not found in config_entries")


def spotify_request(method, path, body=None, params=None):
    """Spotify Web API call. Handles 204 No Content (used by play/pause/next/prev endpoints)."""
    tok = spotify_token()
    url = "https://api.spotify.com/v1" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        if not raw or r.status == 204:
            return None
        return json.loads(raw)

"""
Vinyl-to-Spotify trigger server + admin tool.

Receives a tag UID (POST /scan), looks it up in mappings.json,
and plays the mapped Spotify link on the Sonos "Office" zone.

Also serves a web-based admin tool at /admin for linking new tags to
records: scan a barcode (looked up against discogs_spotify_matches.csv),
or search manually if there's no barcode match, then tap the tag and save.

Run: python3 server.py
Admin tool: http://localhost:5050/admin
Test scan: curl -X POST http://localhost:5050/scan -H "Content-Type: application/json" -d '{"uid": "TEST123"}'
"""

import json
import os
import csv
import time
import base64
import requests
import soco
from soco.plugins.sharelink import ShareLinkPlugin, SpotifyShare
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

MAPPINGS_FILE = os.path.join(os.path.dirname(__file__), "mappings.json")
MATCHES_CSV = os.path.join(os.path.dirname(__file__), "discogs_spotify_matches.csv")
ZONE_NAME = "Office"

# Reader room name (sent by each StickC as "room") -> Sonos zone name.
# A scan with no room, or an unknown room, falls back to ZONE_NAME.
ROOMS = {
    "living": "Office",
    "nia": "Nia Room",
}

# Per-room Spotify account for Sonos. "sn" is the account serial number in the
# queue address, "token" is the account part of the metadata token (read from a
# Sonos Favorite saved under that account). Rooms not listed use whatever
# default account Sonos picks.
ROOM_SPOTIFY = {
    "nia": {"sn": "7", "token": "d6c9b478"},
}

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

# Tracks the most recent UID that was scanned but had no mapping yet, so the
# admin page can show "tag just scanned: ..." without you retyping it.
last_unmapped_scan = {"uid": None, "at": 0}

# Cached Spotify token for the admin tool's manual search fallback.
_spotify_token_cache = {"token": None, "obtained_at": 0}


def load_mappings():
    if not os.path.exists(MAPPINGS_FILE):
        return {}
    with open(MAPPINGS_FILE, "r") as f:
        return json.load(f)


def save_mappings(mappings):
    with open(MAPPINGS_FILE, "w") as f:
        json.dump(mappings, f, indent=2)


def load_matches_csv():
    """Returns list of dicts from discogs_spotify_matches.csv, or [] if the
    file doesn't exist yet (script hasn't been run / output not copied over)."""
    if not os.path.exists(MATCHES_CSV):
        return []
    with open(MATCHES_CSV, newline="") as f:
        return list(csv.DictReader(f))


def get_zone(name=ZONE_NAME, timeout=10, retries=2):
    """Longer timeout + retry because Sonos UPnP discovery is occasionally
    slow to respond, especially right after network changes or Sonos idle
    periods — the default short timeout can fail even when the system is
    healthy, as confirmed by a longer manual call succeeding immediately
    after a failed quick one."""
    for attempt in range(retries):
        zones = soco.discover(timeout=timeout)
        if zones:
            matches = []
            available = []
            for z in zones:
                try:
                    zname = z.player_name
                except Exception:
                    continue  # unreachable or sleeping speaker, skip it
                available.append(zname)
                if zname == name:
                    matches.append(z)
            if matches:
                return matches[0]
            raise RuntimeError(f"Zone '{name}' not found. Available: {available}")
        time.sleep(1)
    raise RuntimeError("No Sonos zones found on network after retries")


def get_spotify_token():
    """Cached, auto-refreshing Spotify token for the admin tool's manual
    artist search. Separate from the standalone discogs_to_spotify.py script
    — this one lives for as long as the server process runs."""
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set. "
            "Manual artist search won't work until these are exported."
        )
    if time.time() - _spotify_token_cache["obtained_at"] < 50 * 60:
        return _spotify_token_cache["token"]

    auth = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"},
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _spotify_token_cache["token"] = token
    _spotify_token_cache["obtained_at"] = time.time()
    return token


# Tracks the last successful play time, to enforce a minimum gap between
# scans. Partly a safeguard against accidental double-taps, partly a test
# of the theory that rapid back-to-back Spotify resolution calls through
# Sonos might be what's triggering the occasional auth/link breakage.
last_play_time = {}  # zone name -> timestamp of last successful play
MIN_SECONDS_BETWEEN_PLAYS = 5


def enqueue_spotify(zone, spotify_url, account=None):
    """Adds a Spotify link to the zone's queue. With no sn this is the stock
    SoCo ShareLinkPlugin call. With an sn, it builds the same request by
    hand and adds sid/flags/sn to the URI so Sonos uses that account."""
    if not account:
        ShareLinkPlugin(zone).add_share_link_to_queue(spotify_url)
        return
    sn = account["sn"]
    token = account["token"]

    service = SpotifyShare()
    if not service.canonical_uri(spotify_url):
        raise RuntimeError(f"Unsupported URI: {spotify_url}")
    share_type, encoded_uri = service.extract(spotify_url)
    magic = service.magic()[share_type]

    flags = {"track": 8232, "episode": 8232, "album": 8268}.get(share_type, 8300)
    enqueue_uri = f"{magic['prefix']}{encoded_uri}?sid=9&flags={flags}&sn={sn}"
    metadata = (
        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
        f'<item id="{magic["key"]}{encoded_uri}" parentID="-1" restricted="true">'
        f'<dc:title></dc:title><upnp:class>{magic["class"]}</upnp:class>'
        '<desc id="cdudn" nameSpace="urn:schemas-rinconnetworks-com:metadata-1-0/">'
        f'SA_RINCON2311_X_#Svc2311-{token}-Token</desc></item></DIDL-Lite>'
    )
    zone.avTransport.AddURIToQueue([
        ("InstanceID", 0),
        ("EnqueuedURI", enqueue_uri),
        ("EnqueuedURIMetaData", metadata),
        ("DesiredFirstTrackNumberEnqueued", 0),
        ("EnqueueAsNext", 0),
    ])


@app.route("/scan", methods=["POST"])
def scan():
    data = request.get_json(force=True)
    uid = data.get("uid")
    if not uid:
        return jsonify({"error": "missing 'uid' field"}), 400

    room = (data.get("room") or "").strip().lower()
    zone_name = ROOMS.get(room, ZONE_NAME)

    time_since_last = time.time() - last_play_time.get(zone_name, 0)
    if time_since_last < MIN_SECONDS_BETWEEN_PLAYS:
        wait = round(MIN_SECONDS_BETWEEN_PLAYS - time_since_last, 1)
        return jsonify({"error": f"Too soon since last play, wait {wait}s"}), 429

    mappings = load_mappings()
    entry = mappings.get(uid)

    if not entry:
        # Remember this so the admin tool can surface it automatically.
        last_unmapped_scan["uid"] = uid
        last_unmapped_scan["at"] = time.time()
        return jsonify({"error": f"no mapping found for uid '{uid}'"}), 404

    spotify_url = entry["spotify_url"] if isinstance(entry, dict) else entry

    try:
        zone = get_zone(zone_name)
        zone.clear_queue()
        enqueue_spotify(zone, spotify_url, ROOM_SPOTIFY.get(room))
        zone.play_from_queue(0)
        last_play_time[zone_name] = time.time()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "playing", "uid": uid, "zone": zone_name, "spotify_url": spotify_url}), 200


@app.route("/map", methods=["POST"])
def add_mapping():
    """Add or update a uid -> spotify_url mapping. 'label' is optional,
    purely for display in the admin tool and mappings list.
    curl -X POST http://localhost:5050/map -H "Content-Type: application/json" \
      -d '{"uid": "04A1B2C3", "spotify_url": "https://open.spotify.com/artist/...", "label": "Bob Dylan"}'
    """
    data = request.get_json(force=True)
    uid = data.get("uid")
    spotify_url = data.get("spotify_url")
    label = data.get("label", "")
    if not uid or not spotify_url:
        return jsonify({"error": "need 'uid' and 'spotify_url'"}), 400

    mappings = load_mappings()
    mappings[uid] = {"spotify_url": spotify_url, "label": label}
    save_mappings(mappings)

    # Clear it so the admin page stops showing it as "just scanned".
    if last_unmapped_scan["uid"] == uid:
        last_unmapped_scan["uid"] = None

    return jsonify({"status": "saved", "uid": uid, "spotify_url": spotify_url, "label": label}), 200


@app.route("/map", methods=["GET"])
def list_mappings():
    return jsonify(load_mappings()), 200


@app.route("/admin/last-scan", methods=["GET"])
def admin_last_scan():
    """Polled by the admin page. Only reports scans from the last 2 minutes,
    so a stale tap from earlier doesn't linger on screen forever."""
    if last_unmapped_scan["uid"] and time.time() - last_unmapped_scan["at"] < 120:
        return jsonify({"uid": last_unmapped_scan["uid"]}), 200
    return jsonify({"uid": None}), 200


@app.route("/admin/barcode-lookup", methods=["GET"])
def admin_barcode_lookup():
    barcode = request.args.get("barcode", "").strip()
    if not barcode:
        return jsonify({"error": "missing barcode"}), 400

    rows = load_matches_csv()
    for row in rows:
        if row.get("barcode", "").strip() == barcode:
            if not row.get("spotify_url"):
                return jsonify({"found": False, "reason": "matched but no Spotify URL on record"}), 200
            return jsonify({
                "found": True,
                "artist": row.get("discogs_artist", ""),
                "title": row.get("discogs_title", ""),
                "spotify_artist": row.get("spotify_matched_artist", ""),
                "spotify_url": row.get("spotify_url", ""),
            }), 200

    return jsonify({"found": False, "reason": "barcode not in collection CSV"}), 200


@app.route("/admin/search-artist", methods=["GET"])
def admin_search_artist():
    """Searches for 'This Is [Artist]' playlists rather than artist pages.
    Raw Spotify artist URLs (open.spotify.com/artist/...) cannot be played
    via SoCo/Sonos — only album, episode, playlist, show, and track URIs
    are supported by SoCo's sharelink plugin. See discogs_to_spotify.py's
    search_spotify_playlist() for the full explanation."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "missing query"}), 400

    try:
        token = get_spotify_token()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    resp = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": f"This Is {query}", "type": "playlist", "limit": 6},
    )
    if resp.status_code != 200:
        return jsonify({"error": f"Spotify search failed: {resp.status_code}"}), 500

    items = resp.json().get("playlists", {}).get("items", [])
    items = [i for i in items if i]  # Spotify sometimes returns None entries

    results = [{
        "name": p["name"],
        "spotify_url": p["external_urls"]["spotify"],
        "image": p["images"][0]["url"] if p.get("images") else None,
        "followers": 0,  # playlist search doesn't return follower counts
    } for p in items]

    return jsonify({"results": results}), 200


ADMIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Vinyl NFC — Admin</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 20px; color: #222; }
  h1 { font-size: 20px; }
  .card { border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin: 16px 0; }
  .uid-banner { background: #eaf6ea; border: 1px solid #b6e0b6; padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
  .uid-banner.empty { background: #f5f5f5; border-color: #ddd; color: #888; }
  input[type=text] { width: 100%; padding: 8px; font-size: 15px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 6px; }
  button { padding: 8px 14px; font-size: 14px; border-radius: 6px; border: 1px solid #ccc; background: #fafafa; cursor: pointer; }
  button:hover { background: #f0f0f0; }
  .result { display: flex; align-items: center; gap: 10px; padding: 8px; border: 1px solid #eee; border-radius: 8px; margin: 6px 0; }
  .result img { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; background: #eee; }
  .result button { margin-left: auto; }
  .muted { color: #888; font-size: 13px; }
  .match { background: #f4fbf4; }
</style>
</head>
<body>

<h1>Tag a record</h1>

<div id="uid-banner" class="uid-banner empty">Waiting for a tag scan…</div>

<div class="card">
  <label><strong>1. Paste a Spotify URL directly</strong></label>
  <input type="text" id="url-input" placeholder="https://open.spotify.com/playlist/... then press Enter" autofocus>
  <input type="text" id="url-label-input" placeholder="Label (e.g. artist name) — optional, for your reference" style="margin-top:6px;">
  <div id="url-result"></div>
</div>

<div class="card">
  <label><strong>2. Or look up a barcode</strong></label>
  <input type="text" id="barcode-input" placeholder="Scan or type barcode, then press Enter">
  <div id="barcode-result"></div>
</div>

<div class="card">
  <label><strong>3. Or search manually</strong></label>
  <input type="text" id="search-input" placeholder="Artist name, then press Enter">
  <div id="search-results"></div>
</div>

<div class="card" id="confirm-card" style="display:none;">
  <strong>Ready to save</strong>
  <div id="confirm-details" class="muted"></div>
  <button id="save-btn" style="margin-top:8px;">Save mapping</button>
  <div id="save-status" class="muted"></div>
</div>

<script>
let currentUid = null;
let pendingLabel = null;
let pendingUrl = null;

function pollLastScan() {
  fetch('/admin/last-scan').then(r => r.json()).then(data => {
    const banner = document.getElementById('uid-banner');
    if (data.uid) {
      currentUid = data.uid;
      banner.textContent = 'Tag scanned: ' + data.uid;
      banner.classList.remove('empty');
    } else {
      banner.textContent = 'Waiting for a tag scan…';
      banner.classList.add('empty');
    }
  });
}
setInterval(pollLastScan, 2000);
pollLastScan();

function showConfirm(label, url) {
  pendingLabel = label;
  pendingUrl = url;
  document.getElementById('confirm-card').style.display = 'block';
  document.getElementById('confirm-details').textContent = label + ' — ' + url;
  document.getElementById('save-status').textContent = '';
}

document.getElementById('url-input').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const url = e.target.value.trim();
  if (!url) return;
  if (!url.includes('open.spotify.com')) {
    document.getElementById('url-result').innerHTML =
      '<p class="muted">Doesn\\'t look like a Spotify URL — check and try again.</p>';
    return;
  }
  const label = document.getElementById('url-label-input').value.trim() || url;
  showConfirm(label, url);
  document.getElementById('url-result').innerHTML =
    '<p class="muted">Ready — scroll down to save.</p>';
});

document.getElementById('barcode-input').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const barcode = e.target.value.trim();
  if (!barcode) return;
  fetch('/admin/barcode-lookup?barcode=' + encodeURIComponent(barcode))
    .then(r => r.json()).then(data => {
      const el = document.getElementById('barcode-result');
      if (data.found) {
        el.innerHTML = '<div class="result match"><div>' + data.artist + ' — ' + data.title +
          '<div class="muted">matched: ' + data.spotify_artist + '</div></div>' +
          '<button onclick="showConfirm(\\'' + data.artist.replace(/'/g, "") + '\\', \\'' + data.spotify_url + '\\')">Use this</button></div>';
      } else {
        el.innerHTML = '<p class="muted">No barcode match (' + (data.reason || 'unknown') + '). Try manual search below.</p>';
      }
    });
});

document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const q = e.target.value.trim();
  if (!q) return;
  const el = document.getElementById('search-results');
  el.innerHTML = '<p class="muted">Searching…</p>';
  fetch('/admin/search-artist?q=' + encodeURIComponent(q))
    .then(r => r.json()).then(data => {
      if (data.error) {
        el.innerHTML = '<p class="muted">' + data.error + '</p>';
        return;
      }
      el.innerHTML = data.results.map(r =>
        '<div class="result">' +
        (r.image ? '<img src="' + r.image + '">' : '<div style="width:40px;height:40px;border-radius:50%;background:#eee;"></div>') +
        '<div>' + r.name + '<div class="muted">' + r.followers.toLocaleString() + ' followers</div></div>' +
        '<button onclick="showConfirm(\\'' + r.name.replace(/'/g, "") + '\\', \\'' + r.spotify_url + '\\')">Use this</button>' +
        '</div>'
      ).join('');
    });
});

document.getElementById('save-btn').addEventListener('click', () => {
  if (!currentUid) {
    document.getElementById('save-status').textContent = 'No tag scanned yet — tap the tag against the reader first.';
    return;
  }
  fetch('/map', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({uid: currentUid, spotify_url: pendingUrl, label: pendingLabel})
  }).then(r => r.json()).then(data => {
    document.getElementById('save-status').textContent = 'Saved: ' + data.label + ' → tag ' + data.uid;
    document.getElementById('confirm-card').style.display = 'none';
    document.getElementById('barcode-input').value = '';
    document.getElementById('search-input').value = '';
    document.getElementById('barcode-result').innerHTML = '';
    document.getElementById('search-results').innerHTML = '';
    currentUid = null;
  });
});
</script>
</body>
</html>
"""


@app.route("/admin", methods=["GET"])
def admin_page():
    return render_template_string(ADMIN_PAGE)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)

"""
Vinyl-to-Spotify trigger server.

Receives a tag UID (POST /scan), looks it up in mappings.json,
and plays the mapped Spotify link on the Sonos "Office" zone.

Run: python3 server.py
Test: curl -X POST http://localhost:5050/scan -H "Content-Type: application/json" -d '{"uid": "TEST123"}'
"""

import json
import os
import soco
from soco.plugins.sharelink import ShareLinkPlugin
from flask import Flask, request, jsonify

app = Flask(__name__)

MAPPINGS_FILE = os.path.join(os.path.dirname(__file__), "mappings.json")
ZONE_NAME = "Office"


def load_mappings():
    if not os.path.exists(MAPPINGS_FILE):
        return {}
    with open(MAPPINGS_FILE, "r") as f:
        return json.load(f)


def save_mappings(mappings):
    with open(MAPPINGS_FILE, "w") as f:
        json.dump(mappings, f, indent=2)


def get_zone(name=ZONE_NAME):
    zones = soco.discover()
    if not zones:
        raise RuntimeError("No Sonos zones found on network")
    matches = [z for z in zones if z.player_name == name]
    if not matches:
        available = [z.player_name for z in zones]
        raise RuntimeError(f"Zone '{name}' not found. Available: {available}")
    return matches[0]


@app.route("/scan", methods=["POST"])
def scan():
    data = request.get_json(force=True)
    uid = data.get("uid")
    if not uid:
        return jsonify({"error": "missing 'uid' field"}), 400

    mappings = load_mappings()
    spotify_url = mappings.get(uid)

    if not spotify_url:
        return jsonify({"error": f"no mapping found for uid '{uid}'"}), 404

    try:
        zone = get_zone()
        share = ShareLinkPlugin(zone)
        zone.clear_queue()
        share.add_share_link_to_queue(spotify_url)
        zone.play_from_queue(0)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "playing", "uid": uid, "spotify_url": spotify_url}), 200


@app.route("/map", methods=["POST"])
def add_mapping():
    """Add or update a uid -> spotify_url mapping.
    curl -X POST http://localhost:5050/map -H "Content-Type: application/json" \
      -d '{"uid": "04A1B2C3", "spotify_url": "https://open.spotify.com/playlist/..."}'
    """
    data = request.get_json(force=True)
    uid = data.get("uid")
    spotify_url = data.get("spotify_url")
    if not uid or not spotify_url:
        return jsonify({"error": "need 'uid' and 'spotify_url'"}), 400

    mappings = load_mappings()
    mappings[uid] = spotify_url
    save_mappings(mappings)
    return jsonify({"status": "saved", "uid": uid, "spotify_url": spotify_url}), 200


@app.route("/map", methods=["GET"])
def list_mappings():
    return jsonify(load_mappings()), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)

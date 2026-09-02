"""
Pull your Discogs collection and auto-match each release to a Spotify artist,
plus fetch the barcode for each release where one exists (used later by the
admin tool to auto-fill a record's data when you scan its barcode).

Outputs a CSV for you to review — don't trust it blindly, spot-check matches
before copying URLs into mappings.json. Fuzzy text matching will occasionally
grab the wrong artist, especially for common-word band names.

Note: this run is slower than before — barcodes require a second Discogs API
call per release, with a 1-second pause between each to respect their rate
limit. For a few hundred records, expect this to take several minutes.

Setup:
    pip3 install requests

Spotify credentials: reuse the client_id / client_secret from your app at
developer.spotify.com/dashboard (the one you already created for the
earlier Web API test). This script only needs Client Credentials flow —
no user login, no refresh token, since it's just doing public search.

Credentials are read from environment variables, NOT hardcoded, so they
never end up committed to git / pushed to GitHub. Set them before running:

    export SPOTIFY_CLIENT_ID="your_id_here"
    export SPOTIFY_CLIENT_SECRET="your_secret_here"
    export DISCOGS_TOKEN="your_discogs_token_here"
    python3 discogs_to_spotify.py YOUR_DISCOGS_USERNAME

DISCOGS_TOKEN is optional but strongly recommended — it raises your rate
limit from 25 requests/minute (unauthenticated) to 60/minute, and without
it the barcode lookups will silently come back empty for many releases
purely from being throttled, not because no barcode exists.

Get a token at: https://www.discogs.com/settings/developers
("Generate new token" — no app registration needed)

Those export lines only last for your current Terminal session. If you
don't want to retype them every time, add them to ~/.zshrc (Mac's default
shell config file) and they'll be set automatically in every new Terminal
window from then on.
"""

import sys
import os
import time
import csv
import requests
import base64

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")  # optional but recommended

DISCOGS_USER_AGENT = "VinylNFCMapper/1.0"


def discogs_headers():
    headers = {"User-Agent": DISCOGS_USER_AGENT}
    if DISCOGS_TOKEN:
        headers["Authorization"] = f"Discogs token={DISCOGS_TOKEN}"
    return headers


def discogs_get(url, params=None, max_retries=3):
    """Wraps requests.get with retry-on-429, so a rate limit hit gets
    retried instead of silently being treated as 'no data found'."""
    for attempt in range(max_retries):
        resp = requests.get(url, headers=discogs_headers(), params=params)
        if resp.status_code == 429:
            wait = 5 * (attempt + 1)
            print(f"    (rate limited, waiting {wait}s...)")
            time.sleep(wait)
            continue
        return resp
    return resp  # give up after max_retries, return last response


def get_spotify_token():
    auth = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_discogs_barcode(release_id):
    """Barcode lives in the full release record, not the collection summary,
    so this is a second API call per release. Some older pressings have no
    barcode at all — that's expected, not an error."""
    url = f"https://api.discogs.com/releases/{release_id}"
    resp = discogs_get(url)
    if resp.status_code != 200:
        print(f"    (barcode lookup failed: {resp.status_code})")
        return None
    identifiers = resp.json().get("identifiers", [])
    for ident in identifiers:
        if ident.get("type", "").lower() == "barcode":
            # strip spaces/dashes so it matches cleanly against a scanner's raw read
            return ident.get("value", "").replace(" ", "").replace("-", "")
    return None


def get_discogs_collection(username):
    releases = []
    url = f"https://api.discogs.com/users/{username}/collection/folders/0/releases"
    params = {"per_page": 100, "page": 1}

    while url:
        resp = discogs_get(url, params=params)
        if resp.status_code != 200:
            print(f"Discogs error {resp.status_code}: {resp.text}")
            break
        data = resp.json()
        for item in data.get("releases", []):
            info = item.get("basic_information", {})
            artists = ", ".join(a["name"] for a in info.get("artists", []))
            title = info.get("title", "")
            year = info.get("year", "")
            release_id = info.get("id")
            releases.append({
                "artist": artists,
                "title": title,
                "year": year,
                "release_id": release_id,
            })

        pagination = data.get("pagination", {})
        next_url = pagination.get("urls", {}).get("next")
        url = next_url
        params = {}  # next_url already has query params baked in
        time.sleep(1)  # be polite to Discogs' rate limit

    return releases


def search_spotify_artist(token, artist_name):
    resp = requests.get(
        "https://api.spotify.com/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": f"artist:{artist_name}", "type": "artist", "limit": 1},
    )
    if resp.status_code != 200:
        return None, None
    items = resp.json().get("artists", {}).get("items", [])
    if not items:
        return None, None
    artist = items[0]
    return artist["name"], artist["external_urls"]["spotify"]


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 discogs_to_spotify.py YOUR_DISCOGS_USERNAME")
        sys.exit(1)

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        print("Missing credentials. Set them first:")
        print('  export SPOTIFY_CLIENT_ID="your_id_here"')
        print('  export SPOTIFY_CLIENT_SECRET="your_secret_here"')
        sys.exit(1)

    username = sys.argv[1]

    print(f"Fetching Discogs collection for '{username}'...")
    releases = get_discogs_collection(username)
    print(f"Found {len(releases)} releases.")

    print("Getting Spotify access token...")
    token = get_spotify_token()

    # Dedupe by artist — no point calling Spotify twice for two Dylan records.
    seen_artists = {}
    rows = []
    for i, r in enumerate(releases, 1):
        artist = r["artist"]
        print(f"[{i}/{len(releases)}] {artist} - {r['title']}", end=" ")

        if artist in seen_artists:
            matched_name, url = seen_artists[artist]
        else:
            matched_name, url = search_spotify_artist(token, artist)
            seen_artists[artist] = (matched_name, url)
            time.sleep(0.2)  # stay well under Spotify's rate limit

        barcode = get_discogs_barcode(r["release_id"]) if r["release_id"] else None
        time.sleep(1)  # separate Discogs call, same rate-limit courtesy as above
        print(f"barcode: {barcode or 'none'}")

        rows.append({
            "discogs_artist": artist,
            "discogs_title": r["title"],
            "discogs_year": r["year"],
            "barcode": barcode or "",
            "spotify_matched_artist": matched_name or "NO MATCH",
            "spotify_url": url or "",
        })

    out_file = "discogs_spotify_matches.csv"
    with open(out_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    no_match = sum(1 for r in rows if r["spotify_url"] == "")
    no_barcode = sum(1 for r in rows if r["barcode"] == "")
    print(f"\nDone. Wrote {len(rows)} rows to {out_file}")
    print(f"{no_match} releases had no Spotify match — check those manually.")
    print(f"{no_barcode} releases had no barcode — expected for older pressings.")
    print("Review every row before using — fuzzy matching makes mistakes,")
    print("especially with common-word band names.")


if __name__ == "__main__":
    main()

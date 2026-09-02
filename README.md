
# vinyl-nfc

Sometimes you browse your records and want to listen to the artist rather than the particular album you own. This project bridges makes it easy to do that — NFC tags stuck inside record sleeves get scanned by an ESP32 reader next to the turntable, which triggers the Spotify playlist for that artist to play on a Sonos zone. Built as a way to play music you don't own on vinyl but do have on Spotify, without touching a phone.


## How it works

1. Tap a tagged record against the reader
2. ESP32 reads the tag's UID, posts it to a local Flask server
3. Server looks up the UID in `mappings.json`
4. Server calls Sonos via SoCo, plays the mapped Spotify link on the "Office" zone

## Setup

pip3 install -r requirements.txt
python3 server.py

## Testing without hardware

curl -X POST http://localhost:5050/scan -H "Content-Type: application/json" -d '{"uid": "TEST123"}'
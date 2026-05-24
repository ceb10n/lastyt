import base64
import json
import os
import time
from pathlib import Path

import pylast
from ytmusicapi import YTMusic

STATE_FILE = Path("state.json")
MAX_STATE_SIZE = 5000


def load_state() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_state(ids: set[str]) -> None:
    entries = list(ids)
    if len(entries) > MAX_STATE_SIZE:
        entries = entries[-MAX_STATE_SIZE:]
    STATE_FILE.write_text(json.dumps(entries))


def build_ytmusic() -> YTMusic:
    if "YTM_HEADERS" in os.environ:
        headers_path = Path("/tmp/ytm_headers.json")
        headers_path.write_text(base64.b64decode(os.environ["YTM_HEADERS"]).decode())
        return YTMusic(str(headers_path))
    if Path("browser.json").exists():
        return YTMusic("browser.json")
    raise RuntimeError("Set YTM_HEADERS env var or place browser.json in the project root.")


def build_lastfm() -> pylast.LastFMNetwork:
    return pylast.LastFMNetwork(
        api_key=os.environ["LASTFM_API_KEY"],
        api_secret=os.environ["LASTFM_API_SECRET"],
        username=os.environ["LASTFM_USERNAME"],
        password_hash=pylast.md5(os.environ["LASTFM_PASSWORD"]),
    )


def sync() -> None:
    scrobbled = load_state()
    ytmusic = build_ytmusic()
    network = build_lastfm()

    history = ytmusic.get_history()
    new_tracks = [
        t for t in history
        if t.get("videoId") and t["videoId"] not in scrobbled
    ]

    if not new_tracks:
        print("No new tracks to scrobble.")
        return

    now = int(time.time())
    # Reverse so oldest track gets the earliest timestamp
    for i, track in enumerate(reversed(new_tracks)):
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        title = track["title"]
        timestamp = now - (len(new_tracks) - 1 - i) * 30
        network.scrobble(artist=artist, title=title, timestamp=timestamp)
        scrobbled.add(track["videoId"])
        print(f"  {artist} — {title}")

    save_state(scrobbled)
    print(f"\nScrobbled {len(new_tracks)} track(s).")


if __name__ == "__main__":
    sync()

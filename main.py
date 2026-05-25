import base64
import datetime
import json
import os
import time
from pathlib import Path

import pylast
from ytmusicapi import YTMusic

STATE_FILE = Path("state.json")
MAX_STATE_SIZE = 5000
WIDTH = 56


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
        headers_path.write_text(base64.b64decode(os.environ["YTM_HEADERS"].strip()).decode())
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


def fmt_ts(timestamp: int) -> str:
    return datetime.datetime.fromtimestamp(timestamp).strftime("%d %b %Y %H:%M")


def write_summary(lines: list[str]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write("\n".join(lines) + "\n")


def sync() -> None:
    started_at = time.time()

    print("─" * WIDTH)
    print(f"  🎵 lastyt  |  YouTube Music → Last.fm")
    print(f"  📅 {datetime.datetime.now().strftime('%d %b %Y %H:%M')}")
    print("─" * WIDTH)

    print("\n  ⏳ Fetching history...")
    scrobbled = load_state()
    ytmusic = build_ytmusic()
    network = build_lastfm()

    history = ytmusic.get_history()
    new_tracks = [
        t for t in history
        if t.get("videoId") and t["videoId"] not in scrobbled
    ]

    if not new_tracks:
        print("  ✅ No new tracks to scrobble.\n")
        print("─" * WIDTH)
        write_summary(["## 🎵 lastyt — YouTube Music → Last.fm", "", "✅ Nothing new to scrobble."])
        return

    count = len(new_tracks)
    print(f"  🎶 Found {count} new track(s):\n")

    now = int(time.time())
    oldest_ts = now - (count - 1) * 30
    rows: list[str] = []

    for i, track in enumerate(reversed(new_tracks)):
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        title = track["title"]
        album = track.get("album", {}).get("name") if track.get("album") else None
        duration = track.get("duration_seconds")
        timestamp = oldest_ts + i * 30
        label = f"{i + 1:>{len(str(count))}}. {artist} — {title}"
        time_label = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M")
        print(f"  🎧 {label[:WIDTH - 10]}  {time_label}")
        network.scrobble(artist=artist, title=title, timestamp=timestamp, album=album, duration=duration)
        scrobbled.add(track["videoId"])
        rows.append(f"| {i + 1} | {artist} | {title} | {album or '—'} | {time_label} |")

    save_state(scrobbled)
    elapsed = time.time() - started_at

    print()
    print("─" * WIDTH)
    print(f"  ✅ Scrobbled  {count} track(s)")
    print(f"  📆 From       {fmt_ts(oldest_ts)}")
    print(f"  📆 To         {fmt_ts(now)}")
    print(f"  ⚡ Completed  {elapsed:.1f}s")
    print("─" * WIDTH)

    write_summary([
        "## 🎵 lastyt — YouTube Music → Last.fm",
        "",
        f"✅ **{fmt_ts(now)}** &nbsp;·&nbsp; 🎶 {count} track(s) scrobbled &nbsp;·&nbsp; ⚡ {elapsed:.1f}s",
        "",
        f"| # | 🎤 Artist | 🎵 Track | 💿 Album | 🕐 ~Time |",
        f"|---|--------|-------|-------|-------|",
        *rows,
        "",
        "> ⚠️ Timestamps are approximate.",
    ])


if __name__ == "__main__":
    sync()

import base64
import datetime
import json
import os
import time
from pathlib import Path

import pylast
from ytmusicapi import YTMusic
from ytmusicapi.continuations import get_continuations
from ytmusicapi.navigation import MUSIC_SHELF, SECTION_LIST, SINGLE_COLUMN_TAB, TITLE_TEXT, nav
from ytmusicapi.parsers.playlists import parse_playlist_items

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


def get_full_history(ytmusic: YTMusic, limit: int = 500) -> list:
    """Fetch history including paginated continuations, up to `limit` tracks."""
    endpoint = "browse"
    body = {"browseId": "FEmusic_history"}
    response = ytmusic._send_request(endpoint, body)
    section_list = nav(response, [*SINGLE_COLUMN_TAB, "sectionListRenderer"])
    sections = section_list.get("contents", [])
    songs: list = []

    def parse_section(content: dict) -> list:
        data = nav(content, [*MUSIC_SHELF, "contents"], True)
        if not data:
            return []
        played_label = nav(content["musicShelfRenderer"], TITLE_TEXT)
        items = parse_playlist_items(data)
        for s in items:
            s["played"] = played_label
        # follow per-section continuation (more tracks in the same time period)
        music_shelf = content.get("musicShelfRenderer", {})
        if "continuations" in music_shelf:
            request_func = lambda params: ytmusic._send_request(endpoint, body, params)
            extras = get_continuations(music_shelf, "musicShelfContinuation", None, request_func, parse_playlist_items)
            for s in extras:
                s["played"] = played_label
            items.extend(extras)
        return items

    for content in sections:
        songs.extend(parse_section(content))
        if len(songs) >= limit:
            return songs[:limit]

    # follow section-list continuation (older time periods: "This week", "Last month", etc.)
    if "continuations" in section_list:
        def parse_section_list(items: list) -> list:
            result = []
            for content in items:
                result.extend(parse_section(content))
            return result

        request_func = lambda params: ytmusic._send_request(endpoint, body, params)
        more = get_continuations(
            section_list, "sectionListContinuation", limit - len(songs), request_func, parse_section_list
        )
        songs.extend(more)

    return songs[:limit]


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

    history = get_full_history(ytmusic)
    print(f"  📋 API returned {len(history)} track(s) total")
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
    try:
        sync()
    except Exception as exc:
        print(f"\n  ❌ Sync failed: {exc}")
        raise

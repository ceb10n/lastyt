import base64
import datetime
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
import pylast
from ytmusicapi import YTMusic
from ytmusicapi.continuations import get_continuations
from ytmusicapi.navigation import MUSIC_SHELF, SECTION_LIST, SINGLE_COLUMN_TAB, TITLE_TEXT, nav
from ytmusicapi.parsers.playlists import parse_playlist_items

STATE_FILE = Path("state.json")
MAX_STATE_SIZE = 5000
WIDTH = 56
DEDUP_DAYS = 7  # a song can be re-scrobbled after this many days


def _played_date(played_label: str) -> str:
    """Convert a YTM 'played' section label to a stable date key."""
    today = datetime.date.today()
    lower = played_label.lower().strip()
    if lower in ("hoje", "today"):
        return today.isoformat()
    if lower in ("ontem", "yesterday"):
        return (today - datetime.timedelta(days=1)).isoformat()
    iso = today.isocalendar()
    return f"W{iso.year}-{iso.week:02d}"


def _track_base_key(t: dict) -> str | None:
    """Return base dedup key (videoId or artist+title hash, plus played date), without occurrence count."""
    date_part = _played_date(t.get("played", "today"))
    if vid := t.get("videoId"):
        return f"{vid}|{date_part}"
    artist = t["artists"][0]["name"] if t.get("artists") else None
    title = t.get("title")
    if artist and title:
        h = hashlib.md5(f"{artist}\x00{title}".encode()).hexdigest()[:12]
        return f"title:{h}|{date_part}"
    return None


def load_state() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    data = json.loads(STATE_FILE.read_text())
    if not data:
        return set()
    pipes = data[0].count("|")
    today = datetime.date.today()
    if pipes == 0:
        # Migrate from very old format (plain videoIds) → vid|date|0
        migrated: set[str] = set()
        for vid in data:
            for days_ago in range(DEDUP_DAYS + 1):
                d = (today - datetime.timedelta(days=days_ago)).isoformat()
                migrated.add(f"{vid}|{d}|0")
        return migrated
    if pipes == 1:
        # Migrate from previous format (vid|date) → vid|date|0
        return {k + "|0" for k in data}
    return set(data)


def save_state(keys: set[str]) -> None:
    cutoff = (datetime.date.today() - datetime.timedelta(days=DEDUP_DAYS)).isoformat()

    def is_recent(k: str) -> bool:
        # key format: "{id}|{date}|{count}"
        parts = k.split("|")
        if len(parts) < 3:
            return True
        date_part = parts[-2]  # second-to-last part is the date
        return "-W" in date_part or date_part >= cutoff

    pruned = {k for k in keys if is_recent(k)}
    if len(pruned) > MAX_STATE_SIZE:
        pruned = set(list(pruned)[-MAX_STATE_SIZE:])
    STATE_FILE.write_text(json.dumps(list(pruned)))


def get_full_history(ytmusic: YTMusic, limit: int = 500) -> list:
    """Fetch history including paginated continuations, up to `limit` tracks."""
    endpoint = "browse"
    body = {"browseId": "FEmusic_history"}
    response = ytmusic._send_request(endpoint, body)
    section_list = nav(response, [*SINGLE_COLUMN_TAB, "sectionListRenderer"])
    sections = section_list.get("contents", [])
    if not sections:
        available_keys = list(section_list.keys())
        raise RuntimeError(
            f"YTMusic history returned no sections (sectionListRenderer keys: {available_keys}). "
            "Check that your YTM credentials (YTM_HEADERS or browser.json) are still valid."
        )
    songs: list = []

    def parse_section(content: dict) -> list:
        data = nav(content, [*MUSIC_SHELF, "contents"], True)
        if not data:
            error = nav(content, ["musicNotifierShelfRenderer", "title", "runs", 0, "text"], True)
            if error:
                raise RuntimeError(f"YTMusic history error: {error}")
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


def scrobble_loudgy(
    token: str,
    artist: str,
    title: str,
    timestamp: int,
    album: str | None = None,
    album_artist: str | None = None,
    duration: int | None = None,
) -> None:
    data: dict[str, str] = {
        "method": "track.scrobble",
        "sk": token,
        "artist[0]": artist,
        "track[0]": title,
        "timestamp[0]": str(timestamp),
    }
    if album:
        data["album[0]"] = album
    if album_artist:
        data["albumArtist[0]"] = album_artist
    if duration is not None:
        data["duration[0]"] = str(duration)
    httpx.post("https://loudgy.com/2.0/", data=data, timeout=15)


def fmt_ts(timestamp: int) -> str:
    return datetime.datetime.fromtimestamp(timestamp).strftime("%d %b %Y %H:%M")


def write_summary(lines: list[str]) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write("\n".join(lines) + "\n")


def sync() -> None:
    started_at = time.time()

    loudgy_token = os.environ.get("LOUDGY_TOKEN")
    destinations = "Last.fm" + (" + Loudgy" if loudgy_token else "")

    print("─" * WIDTH)
    print(f"  🎵 lastyt  |  YouTube Music → {destinations}")
    print(f"  📅 {datetime.datetime.now().strftime('%d %b %Y %H:%M')}")
    print("─" * WIDTH)

    print("\n  ⏳ Fetching history...")
    scrobbled = load_state()
    ytmusic = build_ytmusic()
    network = build_lastfm()

    history = get_full_history(ytmusic)
    print(f"  📋 API returned {len(history)} track(s) total")

    # Count total occurrences of each base key (history is newest-first).
    # Assign nth-play count oldest-first so new plays always get the highest
    # count and are the only ones absent from state.
    base_totals: dict[str, int] = {}
    for t in history:
        b = _track_base_key(t)
        if b:
            base_totals[b] = base_totals.get(b, 0) + 1

    base_seen: dict[str, int] = {}
    new_tracks: list[tuple[str, dict]] = []
    print()
    for t in history:
        artist = t["artists"][0]["name"] if t.get("artists") else "?"
        title = t.get("title", "?")
        played = t.get("played", "?")
        b = _track_base_key(t)
        if b is None:
            print(f"  ⚠️  [{played}] {artist} — {title}  (no ID)")
            continue
        n = base_seen.get(b, 0)
        base_seen[b] = n + 1
        # newest appearance → highest count; oldest appearance → 0
        key = f"{b}|{base_totals[b] - 1 - n}"
        if key in scrobbled:
            print(f"  ⏭   [{played}] {artist} — {title}  (key: {key})")
        else:
            print(f"  🆕  [{played}] {artist} — {title}  (key: {key})")
            new_tracks.append((key, t))
    print()

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

    for i, (key, track) in enumerate(reversed(new_tracks)):
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        title = track["title"]
        album = track.get("album", {}).get("name") if track.get("album") else None
        duration = track.get("duration_seconds")
        timestamp = oldest_ts + i * 30
        label = f"{i + 1:>{len(str(count))}}. {artist} — {title}"
        time_label = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M")
        print(f"  🎧 {label[:WIDTH - 10]}  {time_label}")
        network.scrobble(artist=artist, title=title, timestamp=timestamp, album=album, duration=duration)
        if loudgy_token:
            scrobble_loudgy(loudgy_token, artist, title, timestamp, album=album, album_artist=artist, duration=duration)
        scrobbled.add(key)
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

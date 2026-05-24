# lastyt

Scrobble your YouTube Music listening history to [Last.fm](https://www.last.fm) automatically.

YouTube Music has a great catalog and no native Last.fm scrobbling on iPhone. This project bridges that gap: a Python script runs hourly via GitHub Actions, fetches your recent YT Music history, and scrobbles new tracks to Last.fm.

## How it works

1. A GitHub Actions cron job runs every hour.
2. It fetches your YouTube Music history using [ytmusicapi](https://github.com/sigma67/ytmusicapi).
3. New tracks (not yet scrobbled) are sent to Last.fm using [pylast](https://github.com/pylast/pylast).
4. A state file persisted in GitHub Actions cache tracks what was already scrobbled.

> **Timestamps:** YouTube Music's unofficial API does not expose exact play times. Tracks are scrobbled with approximate timestamps (spaced 30 seconds apart, ending at the time the sync ran). Last.fm accepts this just fine.

---

## Setup

### 1. Fork this repository

Click **Fork** on GitHub. All configuration is done through GitHub Secrets, so you never need to edit any code.

---

### 2. Get your YouTube Music headers

`ytmusicapi` authenticates using your browser's request headers. You only need to do this once (or when your session expires).

**Install ytmusicapi locally:**

```bash
pip install ytmusicapi
```

**Run the setup wizard:**

```bash
ytmusicapi browser
```

This will ask you to open YouTube Music in your browser, open DevTools, copy a request header, and paste it in the terminal. It saves your credentials to a file called `browser.json`.

> **Never commit `browser.json` to git.** It contains your session credentials.

**Encode it as base64** (you'll paste this into a GitHub Secret):

```bash
# Linux
base64 -w 0 browser.json

# macOS
base64 -i browser.json
```

Copy the entire output string.

---

### 3. Get your Last.fm API credentials

1. Go to [https://www.last.fm/api/account/create](https://www.last.fm/api/account/create)
2. Fill in the form (application name can be anything, e.g. `lastyt`)
3. Copy the **API key** and **Shared secret**

---

### 4. Add GitHub Secrets

In your forked repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add the following:

| Secret name | Value |
|---|---|
| `YTM_HEADERS` | The base64 string from step 2 |
| `LASTFM_API_KEY` | Your Last.fm API key |
| `LASTFM_API_SECRET` | Your Last.fm shared secret |
| `LASTFM_USERNAME` | Your Last.fm username |
| `LASTFM_PASSWORD` | Your Last.fm password |

---

### 5. Enable GitHub Actions

GitHub disables Actions on forks by default.

Go to the **Actions** tab in your forked repository and click **"I understand my workflows, go ahead and enable them"**.

The workflow runs every hour automatically. You can also trigger it manually from the **Actions** tab by clicking **"Run workflow"**.

---

## Running locally

```bash
pip install .

export YTM_HEADERS=$(base64 -w 0 browser.json)
export LASTFM_API_KEY=your_api_key
export LASTFM_API_SECRET=your_api_secret
export LASTFM_USERNAME=your_username
export LASTFM_PASSWORD=your_password

python main.py
```

---

## Troubleshooting

**The workflow runs but scrobbles nothing.**
Your YT Music history might already be fully scrobbled. Delete the Actions cache (`Actions → Caches → lastyt-state-*`) to force a full re-sync, then trigger the workflow manually.

**`ytmusicapi` raises an authentication error.**
Your browser session has expired. Re-run `ytmusicapi browser`, re-encode the file, and update the `YTM_HEADERS` secret.

**Last.fm shows wrong timestamps.**
This is expected. See the note at the top — exact play times are not available from the YouTube Music API.

# AWTRIX Python Integrations

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-6f42c1)](https://github.com/astral-sh/uv)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)](#run-modes)

Lightweight, plugin-based AWTRIX integration app for **weather** and **YouTube subscriber count**.  
Optimized for **macOS tray/menu-bar background usage**.

## Why this project

- Simple plugin architecture (`plugins/`)
- Clean JSON configuration (`config.json`)
- Multi-profile auto-detection (home/work/...)
- `.env` secret support
- Works as a tray app or headless runner
- macOS features: **Start at Login** support and dynamic tray icon
- **Network resilience**: automatically reconnects when AWTRIX comes back online after a Wi-Fi change or system startup delay

## Installation (macOS App)

1. Download the `AWTRIX-macOS-arm64.app.zip` from the [Releases](https://github.com/jonashuberts/awtrix-python-integrations/releases) page.
2. Unzip and move `AWTRIX.app` to your `/Applications` folder.

At first launch, runtime files are created in:

- `~/Library/Application Support/AWTRIX/config.json`
- `~/Library/Application Support/AWTRIX/.env`

Sensitive values are expected there and are not bundled as live `.env` secrets.

### macOS Security & Privacy

Since this app is not signed by a registered Apple Developer, macOS Gatekeeper will block it upon first launch.

**To fix this, run the following command in your terminal:**

```bash
xattr -d com.apple.quarantine /Applications/AWTRIX.app
```

*Alternatively: Right-click (or Control-click) the app and select **Open** from the menu, then click **Open** again in the dialog box.*

## Quick start (Development)

1. Install dependencies:

```bash
uv sync
```

2. Create your env file:

```bash
cp .env.example .env
```

3. Run:

```bash
uv run python main.py
```

## Run modes

- **macOS default**: tray app (menu bar, no window)
- **Headless mode**: set `AWTRIX_TRAY=0`

```bash
AWTRIX_TRAY=0 uv run python main.py
```

## Menu bar status indicator

When running as a tray app the menu bar icon and menu reflect the live connection state:

| Icon colour | Meaning |
|-------------|---------|
| ⚪ White | Connected — everything is working |
| 🟠 Orange | Unreachable for ~1 minute — background retry is active |
| ⚫ Grey | Updates paused by the user |
| 🔴 Red | Config or plugin load error |

The menu shows:
- **AWTRIX · `profile`** — current profile name
- **🟢 Connected — Connected to `ip`** — one-line live status with detail
- **Reconnect now** — force an immediate re-probe and profile re-detection (useful after switching Wi-Fi)
- **Reload config** — re-read `config.json` and `.env` without restarting
- **Pause updates** — suspend all plugin updates while keeping the app running

The icon colour only changes after **4 consecutive failed probes** (~1 minute), so brief network hiccups during normal operation do not cause any visible flashing.

## Network resilience

A background connectivity monitor runs every 15 seconds (configurable via `AWTRIX_RECONNECT_INTERVAL`):

- While connected it probes silently — no icon changes, no log spam.
- When AWTRIX becomes unreachable (e.g. you switched Wi-Fi or the device is booting) the icon turns orange after ~1 minute.
- The monitor re-runs **profile auto-detection** on every reconnect attempt, so switching networks automatically picks the right profile.
- The moment AWTRIX responds again the icon returns to white and updates resume immediately.

## Logs

Logs are written to a rotating file so they never fill up your disk:

- **macOS**: `~/Library/Logs/AWTRIX/awtrix.log`
- **Other**: `~/.awtrix/logs/awtrix.log`

Maximum size: **2 MB** per file, **3 backups** kept (6 MB total).

## Configuration

Core config keys:

- `interval`: update interval in seconds
- `profiles`: named AWTRIX targets with optional plugin overrides
- `default_profile`: fallback if no profile is reachable
- `plugins`: enabled integrations and their config

Environment placeholders in config are supported:

```json
"api_key": "${OPENWEATHER_API_KEY}"
```

### Multi-location profile behavior

- On startup, the app probes each profile AWTRIX endpoint.
- First reachable profile is selected automatically.
- You can override selection via the **Profile** submenu in the tray or via environment variable:

```bash
export AWTRIX_PROFILE=home
uv run python main.py
```

## Build macOS app bundle

```bash
bash scripts/build_macos_app.sh
```

Build output:

- `dist/AWTRIX.app`
- `dist/AWTRIX/AWTRIX` (standalone CLI binary)

## Environment variables

```bash
YOUTUBE_API_KEY=your-api-key
OPENWEATHER_API_KEY=your-api-key
AWTRIX_PROFILE=work
AWTRIX_CONFIG=config.json
AWTRIX_TRAY=1
AWTRIX_RECONNECT_INTERVAL=15   # seconds between connectivity probes (default: 15)
```

## AWTRIX API documentation

- Project quick reference (official-doc based): `docs/awtrix-api-relevant.md`
- Official source on device: `http://<awtrix-ip>/` → **API** → **MQTT / HTTP API**

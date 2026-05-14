# AWTRIX Python Integrations

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/package%20manager-uv-6f42c1)](https://github.com/astral-sh/uv)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)](#run-modes)

Lightweight, plugin-based AWTRIX integration app for **weather**, **YouTube subscriber count**, and profile-aware multi-location setups.  
Optimized for **macOS tray/menu-bar background usage**.

## Why this project

- Simple plugin architecture (`plugins/`)
- Clean JSON configuration (`config.json`)
- Multi-profile auto-detection (home/work/...)
- `.env` secret support
- Works as a tray app or headless runner

## Quick start

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
- You can override selection:

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
```

## AWTRIX API documentation

- Project quick reference (official-doc based): `docs/awtrix-api-relevant.md`
- Official source on device: `http://<awtrix-ip>/` → **API** → **MQTT / HTTP API**

## Known limitation (AWTRIX2)

Real-time Pomodoro countdown support was intentionally removed after integration tests.  
Initial send/stop worked, but live timer text updates were not reliable enough for a stable user experience.

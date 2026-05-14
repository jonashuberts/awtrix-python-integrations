# AWTRIX Python Integration

Lightweight AWTRIX app runner with plugin-based data integrations.

## Features

- Plugin discovery from `config.json`
- YouTube subscriber and weather integrations
- Shared global settings with per-plugin overrides
- Environment-variable secrets support (`${ENV_VAR_NAME}`)
- Multi-location profile auto-detection (work/home/etc.)
- Tray-controlled Pomodoro sessions (25/50 aligned mode)

## Requirements

- Python 3.12+
- AWTRIX with Custom API enabled
- `uv` package manager

## Setup

1. Clone the repo and install dependencies:

```bash
uv sync
```

2. Copy the environment example and add your API keys:

```bash
cp .env.example .env
# Edit .env and add your API keys
```

3. Edit `config.json` for your profiles and plugin settings.

4. Run with `uv`:

```bash
uv run python main.py
```

On macOS this now starts as a **menu-bar (tray) app** by default (no window).

Or, if you prefer direct execution:

```bash
source .env
python main.py
```

## Configuration

Global values:

- `interval`: refresh interval in seconds
- `profiles`: named places (work/home/etc.) with `awtrix_ip` and `plugin_overrides`
- `default_profile`: fallback when auto-detection cannot reach any AWTRIX

Each plugin entry defines:

- `module`: module file in `plugins/` (without package prefix)
- `class`: class to instantiate from that module
- `enabled`: whether to load it
- `config`: keyword arguments passed to plugin constructor

Strings in config that match `${VAR_NAME}` are resolved from environment variables at startup.

### Multi-location behavior

- On startup, the app probes each `profiles[].awtrix_ip` and picks the first reachable profile.
- The selected profile's `awtrix_ip` is used by all plugins unless plugin config overrides it.
- `plugin_overrides` can inject profile-specific config per plugin module (for example weather `location`).
- You can force a profile manually with:

```bash
export AWTRIX_PROFILE=home
uv run python main.py
```

## Build a macOS app (no Python required on target machine)

Build command:

```bash
bash scripts/build_macos_app.sh
```

Output:

- `dist/AWTRIX.app` (double-clickable macOS app)
- `dist/AWTRIX/AWTRIX` (standalone CLI binary)

The build copies `config.json` and `.env.example` into the app bundle. If you have a local `.env`, it is copied too.

### Tray app behavior

- Runs continuously in the background from the menu bar
- No terminal window when launched as `.app`
- Tray menu includes:
  - Start Pomodoro 25
  - Start Pomodoro 50
  - Stop Pomodoro
  - Run now
  - Reload config
  - Pause/resume updates
  - Profile selection (auto detect or force profile)
  - Open `config.json`
  - Open `.env`

## Environment Variables

Create a `.env` file (or use `.env.example` as a template):

```
YOUTUBE_API_KEY=your-api-key
OPENWEATHER_API_KEY=your-api-key
AWTRIX_PROFILE=work          # Optional: force a profile
AWTRIX_CONFIG=config.json    # Optional: custom config file
AWTRIX_TRAY=1                # Optional: set 0 to force headless mode
```

## Pomodoro behavior

- Start from tray:
  - `Start Pomodoro 25`: focus runs until next `XX:25` or `XX:50`, then 5-minute break
  - `Start Pomodoro 50`: focus runs until next `XX:50`, then 10-minute break
- During focus and break, only Pomodoro is shown on AWTRIX (icon + countdown + progress bar).
- After break ends (or `Stop Pomodoro`), normal apps resume automatically.

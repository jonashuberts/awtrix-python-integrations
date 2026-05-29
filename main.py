import importlib
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from plugin_base import ClockApp

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

LOGGER = logging.getLogger("awtrix")
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

# Default interval (seconds) for background connectivity probe
_DEFAULT_RECONNECT_INTERVAL = 15


class ConnectionStatus(str, Enum):
    """Live connection state of the AWTRIX device."""

    CONNECTED = "connected"       # Device is reachable, last update succeeded
    UNREACHABLE = "unreachable"   # Device not responding
    RECONNECTING = "reconnecting" # Background probe actively trying to reconnect
    PAUSED = "paused"             # User paused updates
    ERROR = "error"               # Config / plugin load error


def configure_logging() -> None:
    log_format = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Console handler
    logging.basicConfig(level=logging.INFO, format=log_format, datefmt=date_fmt)

    # Rotating file handler — max 2 MB, keep 3 backups
    if sys.platform == "darwin":
        log_dir = Path.home() / "Library" / "Logs" / "AWTRIX"
    else:
        log_dir = Path.home() / ".awtrix" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "awtrix.log",
            maxBytes=2 * 1024 * 1024,  # 2 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_fmt))
        logging.getLogger().addHandler(file_handler)
    except OSError as exc:
        logging.warning("Could not create log file: %s", exc)


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _app_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AWTRIX"
    return Path.home() / ".awtrix"


def _default_env_path() -> Path:
    return _app_support_dir() / ".env"


def _load_env_file() -> None:
    candidates: list[Path] = []
    env_override = os.getenv("AWTRIX_ENV")
    if env_override:
        candidates.append(Path(env_override).expanduser())
    candidates.extend(
        [
            _default_env_path(),
            Path.cwd() / ".env",
            _resource_dir() / ".env",
        ]
    )
    for env_file in candidates:
        if not env_file.exists():
            continue

        with open(env_file, "r", encoding="utf-8") as env_handle:
            for line in env_handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key not in os.environ:
                        os.environ[key] = value
        LOGGER.info("Loaded environment variables from %s", env_file)
        return


def _resolve_env_values(value: Any, path: str = "config") -> Any:
    if isinstance(value, str):
        def replace_match(match: re.Match) -> str:
            env_var_name = match.group(1)
            # Try original, then uppercase, then lowercase
            resolved = os.getenv(env_var_name)
            if resolved is None:
                resolved = os.getenv(env_var_name.upper())
            if resolved is None:
                resolved = os.getenv(env_var_name.lower())
                
            if resolved is None:
                raise ValueError(f"Missing environment variable {env_var_name} referenced at {path}")
                
            # Strip quotes if present (common in .env files)
            if len(resolved) >= 2 and resolved.startswith(('"', "'")) and resolved.endswith(resolved[0]):
                resolved = resolved[1:-1]
            return resolved

        return ENV_VAR_PATTERN.sub(replace_match, value)

    if isinstance(value, list):
        return [_resolve_env_values(item, f"{path}[{idx}]") for idx, item in enumerate(value)]

    if isinstance(value, Mapping):
        return {k: _resolve_env_values(v, f"{path}.{k}") for k, v in value.items()}

    return value


def load_config(config_file: str) -> dict[str, Any]:
    with open(config_file, "r", encoding="utf-8") as config_handle:
        config = json.load(config_handle)
    return _resolve_env_values(config)


def _default_config_path() -> str:
    user_config = _app_support_dir() / "config.json"
    if user_config.exists():
        return str(user_config)
    bundled_path = _resource_dir() / "config.json"
    if bundled_path.exists():
        return str(bundled_path)
    return "config.json"


def _ensure_user_runtime_files() -> None:
    if not getattr(sys, "frozen", False):
        return

    support_dir = _app_support_dir()
    support_dir.mkdir(parents=True, exist_ok=True)

    user_config = support_dir / "config.json"
    bundled_config = _resource_dir() / "config.json"
    if not user_config.exists() and bundled_config.exists():
        user_config.write_text(bundled_config.read_text(encoding="utf-8"), encoding="utf-8")
        LOGGER.info("Created user config at %s", user_config)

    user_env = _default_env_path()
    bundled_env_example = _resource_dir() / ".env.example"
    if not user_env.exists() and bundled_env_example.exists():
        user_env.write_text(bundled_env_example.read_text(encoding="utf-8"), encoding="utf-8")
        LOGGER.info("Created user env template at %s", user_env)


def _base_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _is_awtrix_reachable(awtrix_ip: str, timeout_seconds: float = 1.5) -> bool:
    probe_url = _base_url(awtrix_ip) + "/api/custom"
    try:
        response = requests.get(probe_url, timeout=timeout_seconds)
        return response.status_code < 500
    except requests.RequestException:
        return False


def _extract_profile_names(config: dict[str, Any]) -> list[str]:
    profiles = config.get("profiles", [])
    if not isinstance(profiles, list):
        return []

    names: list[str] = []
    for profile in profiles:
        if isinstance(profile, Mapping):
            name = profile.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def _choose_profile(config: dict[str, Any], forced_profile: str | None = None) -> dict[str, Any] | None:
    profiles = config.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return None

    profiles_by_name = {
        profile.get("name"): profile
        for profile in profiles
        if isinstance(profile, Mapping) and profile.get("name")
    }

    effective_forced_profile = forced_profile or os.getenv("AWTRIX_PROFILE")
    if effective_forced_profile:
        selected = profiles_by_name.get(effective_forced_profile)
        if selected is None:
            raise ValueError(
                f"AWTRIX_PROFILE '{effective_forced_profile}' does not exist in config profiles"
            )
        LOGGER.info("Using forced profile: %s", effective_forced_profile)
        return selected

    for profile in profiles:
        if not isinstance(profile, Mapping):
            continue
        name = profile.get("name")
        awtrix_ip = profile.get("awtrix_ip")
        if not isinstance(name, str) or not isinstance(awtrix_ip, str):
            continue
        
        if _is_awtrix_reachable(awtrix_ip):
            LOGGER.info("Auto-detected profile '%s' via AWTRIX reachability", name)
            return profile

    default_profile_name = config.get("default_profile")
    if default_profile_name:
        selected = profiles_by_name.get(default_profile_name)
        if selected is None:
            raise ValueError(f"default_profile '{default_profile_name}' does not exist in config profiles")
        LOGGER.warning(
            "No profile auto-detected. Falling back to default_profile '%s'",
            default_profile_name,
        )
        return selected

    first_profile = profiles[0]
    if isinstance(first_profile, Mapping):
        LOGGER.warning("No profile auto-detected. Falling back to first profile entry.")
        return first_profile
    return None


def _apply_profile(config: dict[str, Any], forced_profile: str | None = None) -> tuple[dict[str, Any], str | None]:
    runtime_config = deepcopy(config)
    selected_profile = _choose_profile(runtime_config, forced_profile=forced_profile)
    if selected_profile is None:
        return runtime_config, None

    profile_name = selected_profile.get("name", "unnamed")
    profile_awtrix_ip = selected_profile.get("awtrix_ip")
    if not isinstance(profile_awtrix_ip, str):
        raise ValueError(f"Profile '{profile_name}' must define a string awtrix_ip")

    runtime_config["awtrix_ip"] = profile_awtrix_ip

    plugin_overrides = selected_profile.get("plugin_overrides", {})
    if not isinstance(plugin_overrides, Mapping):
        raise ValueError(f"Profile '{profile_name}' plugin_overrides must be an object")

    for plugin_cfg in runtime_config.get("plugins", []):
        module_name = plugin_cfg.get("module")
        if not isinstance(module_name, str):
            continue
        override_values = plugin_overrides.get(module_name)
        if not isinstance(override_values, Mapping):
            continue

        plugin_config = dict(plugin_cfg.get("config", {}))
        plugin_config.update(override_values)
        plugin_cfg["config"] = plugin_config

    LOGGER.info("Selected profile: %s", profile_name)
    return runtime_config, str(profile_name)


def load_plugins(config: dict[str, Any]) -> list[ClockApp]:
    plugins: list[ClockApp] = []
    global_awtrix_ip = config.get("awtrix_ip")

    for plugin_cfg in config.get("plugins", []):
        if not plugin_cfg.get("enabled", True):
            continue

        module_name = plugin_cfg.get("module")
        class_name = plugin_cfg.get("class")
        if not module_name or not class_name:
            LOGGER.error("Skipping plugin with missing module/class: %s", plugin_cfg)
            continue

        plugin_config = dict(plugin_cfg.get("config", {}))
        if global_awtrix_ip and "awtrix_ip" not in plugin_config:
            plugin_config["awtrix_ip"] = global_awtrix_ip

        try:
            module = importlib.import_module(f"plugins.{module_name}")
            plugin_class = getattr(module, class_name)

            if not issubclass(plugin_class, ClockApp):
                LOGGER.error(
                    "Plugin %s in %s is not a ClockApp subclass. Skipping.",
                    class_name,
                    module_name,
                )
                continue

            plugin_instance = plugin_class(**plugin_config)
            plugins.append(plugin_instance)
            LOGGER.info("Loaded plugin: %s.%s", module_name, class_name)
        except (AttributeError, ImportError, TypeError, ValueError) as exc:
            LOGGER.error("Failed to load plugin %s.%s: %s", module_name, class_name, exc)

    return plugins


class AppRuntime:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._plugins: list[ClockApp] = []
        self._current_profile: str | None = None
        self._forced_profile: str | None = None
        self._paused = False
        self._profile_names: list[str] = []
        self._interval = 300
        self._awtrix_ip: str | None = None

        # Connection state
        self._status = ConnectionStatus.CONNECTED
        self._status_detail: str = "Starting up…"
        self._unreachable_logged = False  # suppress log spam
        self._consecutive_failures: int = 0
        # How many consecutive probe failures before the icon turns orange.
        # At 15s per probe that's ~1 minute of being unreachable.
        self._failure_threshold: int = 4

        # Reconnect probe interval (seconds)
        reconnect_env = os.getenv("AWTRIX_RECONNECT_INTERVAL")
        try:
            self._reconnect_interval = int(reconnect_env) if reconnect_env else _DEFAULT_RECONNECT_INTERVAL
        except ValueError:
            self._reconnect_interval = _DEFAULT_RECONNECT_INTERVAL

        # Initial load
        self.reload()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def profile_names(self) -> list[str]:
        return self._profile_names

    @property
    def current_profile(self) -> str | None:
        return self._current_profile

    @property
    def forced_profile(self) -> str | None:
        return self._forced_profile

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def status_detail(self) -> str:
        return self._status_detail

    # ------------------------------------------------------------------
    # Profile / config management
    # ------------------------------------------------------------------

    def set_forced_profile(self, name: str | None) -> None:
        with self._lock:
            self._forced_profile = name
        self.reload()

    def reload(self) -> None:
        try:
            with self._lock:
                config = load_config(self.config_path)
                self._profile_names = _extract_profile_names(config)
                runtime_config, profile_name = _apply_profile(config, forced_profile=self._forced_profile)
                interval = runtime_config.get("interval", 300)
                if not isinstance(interval, int) or interval <= 0:
                    raise ValueError("interval must be a positive integer")

                self._plugins = load_plugins(runtime_config)
                self._current_profile = profile_name
                self._interval = interval
                self._awtrix_ip = runtime_config.get("awtrix_ip")

            LOGGER.info("Runtime reloaded (profile=%s, plugins=%d)", profile_name, len(self._plugins))

            # After a successful reload, probe immediately to set initial status
            self._probe_and_update_status()
        except Exception as exc:
            LOGGER.error("Reload failed: %s", exc)
            with self._lock:
                self._status = ConnectionStatus.ERROR
                self._status_detail = str(exc)

    # ------------------------------------------------------------------
    # Connectivity probing
    # ------------------------------------------------------------------

    def _probe_and_update_status(self) -> bool:
        """Silently probe AWTRIX reachability and update status.

        The icon only turns orange after _failure_threshold consecutive failures
        so normal healthy operation never causes a colour flash.
        Returns True if reachable.
        """
        awtrix_ip = self._awtrix_ip
        if not awtrix_ip:
            with self._lock:
                self._status = ConnectionStatus.ERROR
                self._status_detail = "No awtrix_ip configured"
            return False

        reachable = _is_awtrix_reachable(awtrix_ip)
        with self._lock:
            if reachable:
                previously_unreachable = self._status == ConnectionStatus.UNREACHABLE
                self._consecutive_failures = 0
                self._status = ConnectionStatus.CONNECTED
                self._status_detail = f"Connected to {awtrix_ip}"
                self._unreachable_logged = False
                if previously_unreachable:
                    LOGGER.info("AWTRIX is reachable again at %s", awtrix_ip)
            else:
                self._consecutive_failures += 1
                # Only flip the icon after sustained failures
                if self._consecutive_failures >= self._failure_threshold:
                    if not self._unreachable_logged:
                        LOGGER.warning(
                            "AWTRIX unreachable at %s for %d consecutive probes — "
                            "retrying every %ds",
                            awtrix_ip,
                            self._consecutive_failures,
                            self._reconnect_interval,
                        )
                        self._unreachable_logged = True
                    self._status = ConnectionStatus.UNREACHABLE
                    self._status_detail = f"Cannot reach {awtrix_ip} — retrying…"
                # else: keep current status (white) while within grace period
        return reachable

    def reconnect_now(self) -> None:
        """Force an immediate connectivity probe. If reachable, run update cycle."""
        LOGGER.info("Reconnect requested by user")
        with self._lock:
            self._consecutive_failures = 0
            self._unreachable_logged = False
        # Re-run profile auto-detection to pick up network changes
        self.reload()
        if self._status == ConnectionStatus.CONNECTED:
            self.run_once()

    # ------------------------------------------------------------------
    # Update loop
    # ------------------------------------------------------------------

    def run_once(self) -> None:
        if self.is_paused:
            with self._lock:
                self._status = ConnectionStatus.PAUSED
                self._status_detail = "Updates paused by user"
            return

        with self._lock:
            plugins = list(self._plugins)

        # Fast path: if we know device is unreachable, skip and return
        if self._status == ConnectionStatus.UNREACHABLE:
            return

        any_success = False
        for plugin in plugins:
            if self._stop_event.is_set():
                break
            try:
                data = plugin.update()
                if data is not None:
                    plugin.send(data)
                any_success = True
            except requests.exceptions.ConnectionError:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    if not self._unreachable_logged:
                        LOGGER.warning("AWTRIX connection lost during update — will retry")
                        self._unreachable_logged = True
                    with self._lock:
                        self._status = ConnectionStatus.UNREACHABLE
                        self._status_detail = f"Connection lost — retrying every {self._reconnect_interval}s…"
                break  # no point continuing with other plugins
            except requests.exceptions.Timeout:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    if not self._unreachable_logged:
                        LOGGER.warning("AWTRIX timed out during update — will retry")
                        self._unreachable_logged = True
                    with self._lock:
                        self._status = ConnectionStatus.UNREACHABLE
                        self._status_detail = f"Timed out — retrying every {self._reconnect_interval}s…"
                break
            except (RuntimeError, ValueError, TypeError) as exc:
                LOGGER.error("Plugin %s failed: %s", plugin.__class__.__name__, exc)

        if any_success:
            with self._lock:
                self._status = ConnectionStatus.CONNECTED
                self._status_detail = f"Connected to {self._awtrix_ip or 'device'}"
                self._unreachable_logged = False

    def start(self) -> None:
        def _update_loop():
            while not self._stop_event.is_set():
                self.run_once()
                # Sleep in small increments to be responsive to stop_event
                interval = self._interval
                for _ in range(interval):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

        def _connectivity_monitor():
            """Periodically probe AWTRIX; when unreachable it tries to reconnect."""
            while not self._stop_event.is_set():
                # Wait for the reconnect interval in small steps
                for _ in range(self._reconnect_interval):
                    if self._stop_event.is_set():
                        return
                    time.sleep(1)

                current_status = self._status
                if current_status == ConnectionStatus.UNREACHABLE:
                    LOGGER.debug("Connectivity monitor probing AWTRIX…")
                    # Re-run profile selection in case we switched networks
                    self.reload()
                    if self._status == ConnectionStatus.CONNECTED:
                        LOGGER.info("AWTRIX came back online — resuming updates")
                        self.run_once()
                else:
                    # Silently probe to catch drop-outs; no colour change unless
                    # failures cross the threshold inside _probe_and_update_status
                    self._probe_and_update_status()

        self._stop_thread = threading.Thread(target=_update_loop, daemon=True, name="awtrix-update")
        self._monitor_thread = threading.Thread(target=_connectivity_monitor, daemon=True, name="awtrix-monitor")
        self._stop_thread.start()
        self._monitor_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        for attr in ("_stop_thread", "_monitor_thread"):
            thread = getattr(self, attr, None)
            if thread is not None:
                thread.join(timeout=2.0)

    def toggle_pause(self) -> None:
        with self._lock:
            self._paused = not self._paused
            paused = self._paused
            if paused:
                self._status = ConnectionStatus.PAUSED
                self._status_detail = "Updates paused by user"
        if not paused:
            # Resume: re-probe to get correct status
            self._probe_and_update_status()
        LOGGER.info("Runtime %s", "paused" if paused else "resumed")

# Icon colors for each connection state
_STATUS_COLORS: dict[ConnectionStatus, tuple[int, int, int, int]] = {
    ConnectionStatus.CONNECTED:   (255, 255, 255, 255),  # white
    ConnectionStatus.UNREACHABLE: (255, 140,   0, 255),  # orange
    ConnectionStatus.PAUSED:      (140, 140, 140, 255),  # grey
    ConnectionStatus.ERROR:       (220,  50,  50, 255),  # red
}

# Status labels shown in the tray menu
_STATUS_LABELS: dict[ConnectionStatus, str] = {
    ConnectionStatus.CONNECTED:   "🟢 Connected",
    ConnectionStatus.UNREACHABLE: "🔴 Unreachable — retrying…",
    ConnectionStatus.PAUSED:      "⏸ Paused",
    ConnectionStatus.ERROR:       "⚠️ Error",
}


def _create_tray_image(size: int = 64, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> Any:
    # Canvas with transparent background
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Pixel size scales with the image size.
    # The matrix is 4x6.
    pixel_size = max(1, size // 10)

    matrix_w = 4
    matrix_h = 6

    grid_w = matrix_w * pixel_size
    grid_h = matrix_h * pixel_size

    offset_x = (size - grid_w) // 2
    offset_y = (size - grid_h) // 2

    matrix = [
        "1110",
        "1001",
        "1001",
        "1111",
        "1001",
        "1001",
    ]

    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell == "1":
                left = offset_x + (x * pixel_size)
                top = offset_y + (y * pixel_size)
                draw.rectangle(
                    (left, top, left + pixel_size - 1, top + pixel_size - 1),
                    fill=color,
                )

    return image


def _is_login_item() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        # Check if an app with our name is in login items
        script = 'tell application "System Events" to get count of (every login item whose name is "AWTRIX")'
        output = subprocess.check_output(["osascript", "-e", script], text=True).strip()
        return output == "1"
    except Exception:
        return False


def _open_login_items_settings() -> None:
    if sys.platform == "darwin":
        # Opens the Login Items settings on macOS Ventura and later
        subprocess.run(["open", "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"], check=False)


def _toggle_login_item(icon: Any, _: Any) -> None:
    if sys.platform != "darwin":
        return
    
    is_set = _is_login_item()
    if not is_set:
        app_path = None
        if getattr(sys, "frozen", False):
            curr = Path(sys.executable).resolve()
            for parent in curr.parents:
                if parent.suffix == ".app":
                    app_path = parent
                    break
        
        if app_path:
            # Use 'POSIX file' for more robust path handling in AppleScript
            script = f'tell application "System Events" to make login item at end with properties {{path:POSIX file "{app_path}", name:"AWTRIX", hidden:false}}'
            try:
                subprocess.run(["osascript", "-e", script], check=True)
                LOGGER.info("Added AWTRIX to Login Items")
            except subprocess.CalledProcessError:
                LOGGER.error("Failed to add Login Item via script. Opening System Settings instead.")
                _open_login_items_settings()
        else:
            LOGGER.error("Could not determine .app path. Opening System Settings.")
            _open_login_items_settings()
    else:
        script = 'tell application "System Events" to delete (every login item whose name is "AWTRIX")'
        try:
            subprocess.run(["osascript", "-e", script], check=True)
            LOGGER.info("Removed AWTRIX from Login Items")
        except subprocess.CalledProcessError:
            _open_login_items_settings()
    
    icon.update_menu()


def _open_path(path: Path) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Tray file opening is currently supported on macOS only")
    subprocess.run(["open", str(path)], check=False)


def run_tray_app(runtime: AppRuntime) -> None:
    runtime.start()
    tray_image = _create_tray_image()

    # Track the last rendered status so we only redraw the icon on changes
    _last_rendered_status: list[ConnectionStatus] = [ConnectionStatus.RECONNECTING]

    def _title(_: Any) -> str:
        profile = runtime.current_profile or "auto"
        return f"AWTRIX · {profile}"

    def _status_item(_: Any) -> str:
        """One-line status: emoji + detail (e.g. 🟢 Connected to 192.168.1.5)."""
        emoji = _STATUS_LABELS.get(runtime.status, str(runtime.status))
        detail = runtime.status_detail
        if detail:
            return f"{emoji} — {detail}"
        return emoji

    def _refresh_icon(icon: Any) -> None:
        """Redraw the tray icon if connection status changed."""
        current = runtime.status
        if current != _last_rendered_status[0]:
            color = _STATUS_COLORS.get(current, (255, 255, 255, 255))
            icon.icon = _create_tray_image(color=color)
            _last_rendered_status[0] = current
            icon.update_menu()

    def _reload(_: Any, __: Any) -> None:
        runtime.reload()

    def _run_once(_: Any, __: Any) -> None:
        runtime.run_once()

    def _reconnect_now(icon: Any, __: Any) -> None:
        threading.Thread(target=lambda: _do_reconnect(icon), daemon=True).start()

    def _do_reconnect(icon: Any) -> None:
        runtime.reconnect_now()
        _refresh_icon(icon)

    def _toggle_pause(icon: Any, __: Any) -> None:
        runtime.toggle_pause()
        _refresh_icon(icon)

    def _is_paused(_: Any) -> bool:
        return runtime.is_paused

    def _open_config(_: Any, __: Any) -> None:
        _open_path(Path(runtime.config_path).resolve())

    def _open_env(_: Any, __: Any) -> None:
        env_path = _default_env_path()
        env_path.parent.mkdir(parents=True, exist_ok=True)
        if not env_path.exists():
            example = _resource_dir() / ".env.example"
            if example.exists():
                env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                env_path.write_text("", encoding="utf-8")
        _open_path(env_path.resolve())

    def _set_auto(icon: Any, __: Any) -> None:
        runtime.set_forced_profile(None)
        icon.update_menu()

    def _is_auto_selected(_: Any) -> bool:
        return runtime.forced_profile is None

    def _make_select(name: str) -> Any:
        def _select(icon: Any, __: Any) -> None:
            runtime.set_forced_profile(name)
            icon.update_menu()
        return _select

    def _make_is_selected(name: str) -> Any:
        def _is_selected(_: Any) -> bool:
            return runtime.forced_profile == name
        return _is_selected

    profile_items: list[Any] = [pystray.MenuItem("Auto detect", _set_auto, checked=_is_auto_selected, radio=True)]
    for profile_name in runtime.profile_names:
        profile_items.append(
            pystray.MenuItem(
                profile_name,
                _make_select(profile_name),
                checked=_make_is_selected(profile_name),
                radio=True,
            )
        )
    profile_menu = pystray.Menu(*profile_items)

    def _quit(icon: Any, __: Any) -> None:
        runtime.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(_title, None, enabled=False),
        pystray.MenuItem(_status_item, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Run now", _run_once),
        pystray.MenuItem("Reconnect now", _reconnect_now),
        pystray.MenuItem("Reload config", _reload),
        pystray.MenuItem("Pause updates", _toggle_pause, checked=_is_paused),
        pystray.MenuItem("Profile", profile_menu),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start at Login", _toggle_login_item, checked=lambda _: _is_login_item()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open config.json", _open_config),
        pystray.MenuItem("Open .env", _open_env),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon("awtrix", tray_image, "AWTRIX", menu)

    # Background thread that updates the icon color as status changes
    def _icon_watcher():
        while not runtime._stop_event.is_set():
            _refresh_icon(icon)
            time.sleep(3)  # check every 3 seconds

    threading.Thread(target=_icon_watcher, daemon=True, name="awtrix-icon-watcher").start()

    icon.run()


def run_headless(runtime: AppRuntime) -> None:
    runtime.start()
    while True:
        time.sleep(60)


def main() -> None:
    configure_logging()
    _ensure_user_runtime_files()
    _load_env_file()
    config_path = os.getenv("AWTRIX_CONFIG", _default_config_path())
    runtime = AppRuntime(config_path=config_path)

    wants_tray = os.getenv("AWTRIX_TRAY", "1") != "0"
    if wants_tray and sys.platform == "darwin" and pystray is not None and Image is not None:
        run_tray_app(runtime)
        return

    if wants_tray and sys.platform == "darwin" and pystray is None:
        LOGGER.warning("AWTRIX_TRAY enabled but pystray/Pillow is unavailable; falling back to headless mode")
    run_headless(runtime)


if __name__ == "__main__":
    main()

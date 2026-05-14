import importlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from plugin_base import ClockApp
from plugins.pomodoro_plugin import PomodoroApp

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

LOGGER = logging.getLogger("awtrix")
ENV_VAR_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _load_env_file() -> None:
    candidates = [_resource_dir() / ".env", Path.cwd() / ".env"]
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
        match = ENV_VAR_PATTERN.match(value)
        if not match:
            return value

        env_var = match.group(1)
        resolved = os.getenv(env_var)
        if resolved is None:
            raise ValueError(f"Missing environment variable {env_var} referenced at {path}")
        return resolved

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
    bundled_path = _resource_dir() / "config.json"
    if bundled_path.exists():
        return str(bundled_path)
    return "config.json"


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
            LOGGER.error("Error loading plugin %s.%s: %s", module_name, class_name, exc)
    return plugins


def run_plugins_once(plugins: list[ClockApp]) -> None:
    for plugin in plugins:
        if not plugin.enabled:
            continue
        try:
            data = plugin.update()
            if data is not None:
                plugin.send(data)
        except (RuntimeError, ValueError, TypeError) as exc:
            LOGGER.error("Plugin %s failed: %s", plugin.__class__.__name__, exc)


class AppRuntime:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._plugins: list[ClockApp] = []
        self._interval = 300
        self._paused = False
        self._profile_names: list[str] = []
        self._current_profile: str | None = None
        self._forced_profile: str | None = None
        self._pomodoro: PomodoroApp | None = None
        self._pomodoro_mode: int | None = None

    def start(self) -> None:
        self.reload()
        self._worker_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._worker_thread.join(timeout=5)

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            if not self.is_paused:
                if self.tick_pomodoro():
                    self._stop_event.wait(1)
                else:
                    self.run_once()
                    self._stop_event.wait(self.interval)
            else:
                self._stop_event.wait(1)

    def reload(self) -> None:
        config = load_config(self.config_path)
        profile_names = _extract_profile_names(config)
        runtime_config, profile_name = _apply_profile(config, forced_profile=self.forced_profile)
        interval = runtime_config.get("interval", 300)
        if not isinstance(interval, int) or interval <= 0:
            raise ValueError("interval must be a positive integer")
        plugins = load_plugins(runtime_config)
        pomodoro_cfg: dict[str, Any] = {}
        for plugin_cfg in runtime_config.get("plugins", []):
            if plugin_cfg.get("module") == "pomodoro_plugin":
                pomodoro_cfg = dict(plugin_cfg.get("config", {}))
                break

        awtrix_ip = runtime_config.get("awtrix_ip")
        if not isinstance(awtrix_ip, str):
            raise ValueError("awtrix_ip must be configured")
        pomodoro = PomodoroApp(awtrix_ip=awtrix_ip, **pomodoro_cfg)
        filtered_plugins = [p for p in plugins if not isinstance(p, PomodoroApp)]

        with self._lock:
            self._profile_names = profile_names
            self._current_profile = profile_name
            self._interval = interval
            self._plugins = filtered_plugins
            self._pomodoro = pomodoro

        LOGGER.info("Runtime reloaded (profile=%s, plugins=%d)", profile_name, len(filtered_plugins))

    def run_once(self) -> None:
        if self.is_pomodoro_active:
            return
        with self._lock:
            plugins = list(self._plugins)
        run_plugins_once(plugins)

    @property
    def interval(self) -> int:
        with self._lock:
            return self._interval

    @property
    def profile_names(self) -> list[str]:
        with self._lock:
            return list(self._profile_names)

    @property
    def current_profile(self) -> str | None:
        with self._lock:
            return self._current_profile

    @property
    def forced_profile(self) -> str | None:
        with self._lock:
            return self._forced_profile

    def set_forced_profile(self, profile_name: str | None) -> None:
        with self._lock:
            self._forced_profile = profile_name
        self.reload()

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def toggle_pause(self) -> None:
        with self._lock:
            self._paused = not self._paused
            paused = self._paused
        LOGGER.info("Runtime %s", "paused" if paused else "resumed")

    @property
    def is_pomodoro_active(self) -> bool:
        with self._lock:
            return self._pomodoro is not None and self._pomodoro.is_active

    @property
    def pomodoro_status(self) -> str:
        with self._lock:
            if self._pomodoro is None or not self._pomodoro.is_active:
                return "inactive"
            phase = self._pomodoro.phase or "unknown"
            mode = self._pomodoro_mode or 0
        return f"{phase}:{mode}"

    def start_pomodoro(self, mode_minutes: int) -> None:
        if mode_minutes not in {25, 50}:
            raise ValueError("Pomodoro mode must be 25 or 50")
        with self._lock:
            pomodoro = self._pomodoro
        if pomodoro is None:
            raise RuntimeError("Pomodoro is not initialized")
        pomodoro.start_aligned_session(mode_minutes)
        with self._lock:
            self._pomodoro_mode = mode_minutes
        LOGGER.info("Started Pomodoro %s mode", mode_minutes)

    def stop_pomodoro(self) -> None:
        with self._lock:
            pomodoro = self._pomodoro
        if pomodoro is None:
            return
        pomodoro.stop_session()
        pomodoro.clear_display()
        with self._lock:
            self._pomodoro_mode = None
        LOGGER.info("Stopped Pomodoro")

    def tick_pomodoro(self) -> bool:
        with self._lock:
            pomodoro = self._pomodoro
        if pomodoro is None or not pomodoro.is_active:
            return False
        still_active = pomodoro.tick()
        if not still_active:
            with self._lock:
                self._pomodoro_mode = None
            LOGGER.info("Pomodoro completed; normal apps resumed")
        return still_active


def _create_tray_image() -> Any:
    # 64x64 Canvas with transparent background
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Pixel size of 6 gives it a nice, crisp look without being too massive.
    pixel_size = 6
    
    # The matrix from your image is exactly 4 columns wide and 6 rows high.
    matrix_w = 4
    matrix_h = 6
    
    # Calculate dimensions
    grid_w = matrix_w * pixel_size
    grid_h = matrix_h * pixel_size
    
    # Center it perfectly in the 64x64 space
    offset_x = (64 - grid_w) // 2
    offset_y = (64 - grid_h) // 2
    
    ink = (255, 255, 255, 255) # Standard macOS menu bar white

    # This is the exact 1:1 pixel mapping from your provided image
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
                # Calculate coordinates for each block
                left = offset_x + (x * pixel_size)
                top = offset_y + (y * pixel_size)
                # Draw sharp squares
                draw.rectangle(
                    (left, top, left + pixel_size - 1, top + pixel_size - 1),
                    fill=ink,
                )
                
    return image


def _open_path(path: Path) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Tray file opening is currently supported on macOS only")
    subprocess.run(["open", str(path)], check=False)


def run_tray_app(runtime: AppRuntime) -> None:
    runtime.start()
    tray_image = _create_tray_image()

    def _title(_: Any) -> str:
        profile = runtime.current_profile or "auto"
        mode = "paused" if runtime.is_paused else "running"
        pomodoro = runtime.pomodoro_status
        return f"AWTRIX ({profile}, {mode}, {pomodoro})"

    def _reload(_: Any, __: Any) -> None:
        runtime.reload()

    def _run_once(_: Any, __: Any) -> None:
        runtime.run_once()

    def _toggle_pause(_: Any, __: Any) -> None:
        runtime.toggle_pause()

    def _is_paused(_: Any) -> bool:
        return runtime.is_paused

    def _open_config(_: Any, __: Any) -> None:
        _open_path(Path(runtime.config_path).resolve())

    def _open_env(_: Any, __: Any) -> None:
        env_path = _resource_dir() / ".env"
        if not env_path.exists():
            example = _resource_dir() / ".env.example"
            if example.exists():
                env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                env_path.write_text("", encoding="utf-8")
        _open_path(env_path.resolve())

    def _start_pomodoro_25(icon: Any, __: Any) -> None:
        runtime.start_pomodoro(25)
        icon.update_menu()

    def _start_pomodoro_50(icon: Any, __: Any) -> None:
        runtime.start_pomodoro(50)
        icon.update_menu()

    def _stop_pomodoro(icon: Any, __: Any) -> None:
        runtime.stop_pomodoro()
        icon.update_menu()

    def _pomodoro_active(_: Any) -> bool:
        return runtime.is_pomodoro_active

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
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start Pomodoro 25", _start_pomodoro_25),
        pystray.MenuItem("Start Pomodoro 50", _start_pomodoro_50),
        pystray.MenuItem("Stop Pomodoro", _stop_pomodoro, enabled=_pomodoro_active),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Run now", _run_once),
        pystray.MenuItem("Reload config", _reload),
        pystray.MenuItem("Pause updates", _toggle_pause, checked=_is_paused),
        pystray.MenuItem("Profile", profile_menu),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open config.json", _open_config),
        pystray.MenuItem("Open .env", _open_env),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon("awtrix", tray_image, "AWTRIX", menu)
    icon.run()


def run_headless(runtime: AppRuntime) -> None:
    runtime.start()
    while True:
        time.sleep(60)


def main() -> None:
    configure_logging()
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

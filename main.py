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

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

LOGGER = logging.getLogger("awtrix")
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


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

        # Initial load
        self.reload()

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

    def set_forced_profile(self, name: str | None) -> None:
        with self._lock:
            self._forced_profile = name
        self.reload()

    def reload(self) -> None:
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
        LOGGER.info("Runtime reloaded (profile=%s, plugins=%d)", profile_name, len(self._plugins))

    def run_once(self) -> None:
        if self.is_paused:
            return

        with self._lock:
            plugins = list(self._plugins)

        for plugin in plugins:
            if self._stop_event.is_set():
                break
            try:
                data = plugin.update()
                if data is not None:
                    plugin.send(data)
            except (RuntimeError, ValueError, TypeError) as exc:
                LOGGER.error("Plugin %s failed: %s", plugin.__class__.__name__, exc)

    def start(self) -> None:
        def _loop():
            while not self._stop_event.is_set():
                self.run_once()
                # Sleep in small increments to be responsive to stop_event
                interval = self._interval
                for _ in range(interval):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

        self._stop_thread = threading.Thread(target=_loop, daemon=True)
        self._stop_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if hasattr(self, "_stop_thread"):
            self._stop_thread.join(timeout=2.0)

    def toggle_pause(self) -> None:
        with self._lock:
            self._paused = not self._paused
            paused = self._paused
        LOGGER.info("Runtime %s", "paused" if paused else "resumed")

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


def _toggle_login_item(icon: Any, _: Any) -> None:
    if sys.platform != "darwin":
        return
    
    is_set = _is_login_item()
    if not is_set:
        # Get the path to the current app bundle
        app_path = None
        if getattr(sys, "frozen", False):
            # When running as .app bundle, sys.executable is inside Contents/MacOS/
            # We want the path to the .app itself
            curr = Path(sys.executable).resolve()
            for parent in curr.parents:
                if parent.suffix == ".app":
                    app_path = parent
                    break
        
        if app_path:
            script = f'tell application "System Events" to make login item at end with properties {{path:"{app_path}", name:"AWTRIX", hidden:true}}'
            subprocess.run(["osascript", "-e", script], check=False)
        else:
            LOGGER.error("Could not determine .app path for login item")
    else:
        script = 'tell application "System Events" to delete (every login item whose name is "AWTRIX")'
        subprocess.run(["osascript", "-e", script], check=False)
    
    icon.update_menu()


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
        return f"AWTRIX ({profile}, {mode})"

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
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Run now", _run_once),
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

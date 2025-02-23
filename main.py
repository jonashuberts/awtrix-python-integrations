# main.py
import json
import time
import importlib
from plugin_base import ClockApp
from datetime import datetime

def load_config(config_file="config.json"):
    """Load configuration from a JSON file."""
    with open(config_file, "r") as f:
        config = json.load(f)
    return config

def load_plugins(config):
    """
    Dynamically import and initialize plugin instances based on the configuration.
    """
    plugins = []
    global_awtrix_ip = config.get("awtrix_ip")
    for plugin_cfg in config.get("plugins", []):
        if not plugin_cfg.get("enabled", True):
            continue  # Skip disabled plugins

        module_name = plugin_cfg["module"]
        class_name = plugin_cfg["class"]
        try:
            # Import the module from the 'plugins' package.
            module = importlib.import_module("plugins." + module_name)
            plugin_class = getattr(module, class_name)
            
            # Ensure the loaded class is a subclass of ClockApp.
            if not issubclass(plugin_class, ClockApp):
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Plugin {class_name} in {module_name} is not a subclass of ClockApp. Skipping.")
                continue

            # Get the plugin's configuration and inject global awtrix_ip if needed.
            plugin_config = plugin_cfg.get("config", {})
            if global_awtrix_ip and "awtrix_ip" not in plugin_config:
                plugin_config["awtrix_ip"] = global_awtrix_ip

            plugin_instance = plugin_class(**plugin_config)
            plugins.append(plugin_instance)
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Loaded plugin: {module_name}.{class_name}")
        except Exception as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error loading plugin {module_name}.{class_name}: {e}")
    return plugins

def main():
    config = load_config()
    interval = config.get("interval", 300)
    plugins = load_plugins(config)
    
    if not plugins:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] No plugins loaded. Exiting.")
        return

    # Main loop: call update() and send() on each enabled plugin.
    while True:
        for plugin in plugins:
            if plugin.enabled:
                data = plugin.update()
                if data is not None:
                    plugin.send(data)
        time.sleep(interval)

if __name__ == "__main__":
    main()
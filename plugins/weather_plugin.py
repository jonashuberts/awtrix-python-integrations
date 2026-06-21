import requests
import logging
import socket
from urllib.parse import urlsplit, urlunsplit
from plugin_base import ClockApp

LOGGER = logging.getLogger("awtrix.weather")


def _resolve_mdns_url(url: str) -> str:
    """Resolve .local mDNS hostnames to IP before connecting."""
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if not hostname.endswith(".local"):
        return url
    try:
        results = socket.getaddrinfo(hostname, parsed.port or 80, socket.AF_INET, socket.SOCK_STREAM)
        ip = results[0][4][0]
        netloc = ip if not parsed.port else f"{ip}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except OSError:
        return url


class WeatherApp(ClockApp):
    def __init__(self, location: str, awtrix_ip: str, api_key: str = ""):
        """
        Initialize the WeatherApp.
        :param location: e.g., "Alt Moosach, DE"
        :param awtrix_ip: URL for the AWTRIX Custom API Endpoint
        :param api_key: Kept for compatibility but unused as Open-Meteo is keyless
        """
        super().__init__()
        self.location = location
        self.awtrix_ip = awtrix_ip
        self.api_key = api_key
        self.lat = None
        self.lon = None

    def _resolve_coordinates(self) -> bool:
        if self.lat is not None and self.lon is not None:
            return True
        try:
            # Open-Meteo geocoding works best with clean names (e.g. "Alt Moosach" instead of "Alt Moosach, DE")
            query = self.location
            if "," in query:
                query = query.split(",", 1)[0].strip()

            url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {
                "name": query,
                "count": 1,
                "format": "json"
            }
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            results = data.get("results")
            if results:
                self.lat = results[0]["latitude"]
                self.lon = results[0]["longitude"]
                LOGGER.info("Resolved location '%s' (query: '%s') to lat=%s, lon=%s", self.location, query, self.lat, self.lon)
                return True
            else:
                LOGGER.error("No geocoding results found for location '%s' (query: '%s')", self.location, query)
        except Exception as exc:
            LOGGER.error("Failed to geocode location '%s': %s", self.location, exc)
        return False

    def update(self) -> tuple[str, int] | None:
        """
        Fetch the current weather data from Open-Meteo.
        Resolves coordinates first, then queries the forecast API,
        rounds temperature, and maps the WMO code to an AWTRIX icon.
        """
        if not self._resolve_coordinates():
            return None

        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": self.lat,
                "longitude": self.lon,
                "current": "temperature_2m,weather_code",
                "timezone": "auto"
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            current = data["current"]
            temp = round(current["temperature_2m"])
            weather_code = current["weather_code"]
            icon = self.get_icon(weather_code)
            text = f"{temp}°C"
            return text, icon
        except requests.RequestException as exc:
            LOGGER.error("Error retrieving weather data from Open-Meteo: %s", exc)
            return None
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.error("Invalid weather payload structure from Open-Meteo: %s", exc)
            return None

    def get_icon(self, weather_code: int) -> int:
        """
        Determine an AWTRIX icon based on WMO weather code (WW).
        """
        # WMO Weather interpretation codes (WW): https://open-meteo.com/en/docs
        if weather_code == 0:
            return 8953  # sun (clear sky)
        elif weather_code in (1, 2, 3, 45, 48):
            return 91    # cloud (mainly clear, partly cloudy, overcast, fog)
        elif weather_code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
            return 24095 # rain cloud
        elif weather_code in (56, 57, 66, 67, 71, 73, 75, 77, 85, 86):
            return 2289  # snow
        elif weather_code in (95, 96, 99):
            return 11428 # thunderstorm
        return 91  # fallback to cloud

    def send(self, weather_info: tuple[str, int] | None) -> None:
        """
        Send the weather info (text and icon) to the AWTRIX clock.
        """
        if weather_info is None:
            return
        text, icon = weather_info
        payload = {
            "name": "Weather",
            "icon": icon,
            "text": text,
            "repeat": 60
        }
        try:
            url = _resolve_mdns_url(f"{self.awtrix_ip}?name=Weather")
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
            LOGGER.info("Weather updated successfully.")
        except requests.RequestException as exc:
            LOGGER.error("Error sending weather data to AWTRIX: %s", exc)

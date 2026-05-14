import requests
import logging
from plugin_base import ClockApp

LOGGER = logging.getLogger("awtrix.weather")


class WeatherApp(ClockApp):
    def __init__(self, location: str, awtrix_ip: str, api_key: str):
        """
        Initialize the WeatherApp.
        :param location: e.g., "Landsberg am Lech,DE"
        :param awtrix_ip: URL for the AWTRIX Custom API Endpoint
        :param api_key: Your OpenWeatherMap API key
        """
        super().__init__()
        self.location = location
        self.awtrix_ip = awtrix_ip
        self.api_key = api_key

    def update(self) -> tuple[str, int] | None:
        """
        Fetch the current weather data from OpenWeatherMap in JSON.
        This method extracts temperature and weather description,
        determines an icon, and returns (text, icon).
        """
        try:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": self.location,
                "units": "metric",
                "appid": self.api_key,
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            main = data["main"]
            weather = data["weather"][0]
            temp = round(main["temp"])
            description = weather["description"]
            icon = self.get_icon(weather["icon"], description)
            text = f"{temp}°C"
            return text, icon
        except requests.RequestException as exc:
            LOGGER.error("Error retrieving weather data from OWM: %s", exc)
            return None
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.error("Invalid weather payload structure: %s", exc)
            return None

    def get_icon(self, owm_icon_code: str, description: str) -> int:
        """
        Determine an AWTRIX icon based on the OpenWeatherMap icon code
        (e.g., "01d", "09n") or textual description.
        Returns an integer icon that AWTRIX understands.
        """
        # OWM icon codes: https://openweathermap.org/weather-conditions
        code = owm_icon_code.lower()
        desc = description.lower()

        if code.startswith("01"):  # clear sky
            return 8953  # sun
        elif code.startswith("02") or code.startswith("03") or code.startswith("04"):
            return 91    # cloud
        elif code.startswith("09") or code.startswith("10"):
            return 24095 # rain cloud
        elif code.startswith("11"):
            return 11428 # thunderstorm
        elif code.startswith("13"):
            return 2289  # snow
        elif code.startswith("50"):
            return 91    # mist/fog (fallback to cloud)
        else:
            # fallback by description
            if "clear" in desc:
                return 8953
            if "rain" in desc:
                return 24095
            if "snow" in desc:
                return 2289
            if "thunder" in desc:
                return 11428
            return 91

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
            url = f"{self.awtrix_ip}?name=Weather"
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
            LOGGER.info("Weather updated successfully.")
        except requests.RequestException as exc:
            LOGGER.error("Error sending weather data to AWTRIX: %s", exc)

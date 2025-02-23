# plugins/weather_plugin.py
import requests
from datetime import datetime
from plugin_base import ClockApp

class WeatherApp(ClockApp):
    def __init__(self, location, awtrix_ip):
        """
        Initialize the WeatherApp.
        :param location: A string representing the location (e.g., "London", "New York", "Tokyo").
        :param awtrix_ip: The URL for the AWTRIX custom API endpoint.
        """
        super().__init__()
        self.location = location
        self.awtrix_ip = awtrix_ip

    def update(self):
        """
        Fetch the current weather data from wttr.in in JSON format.
        This method extracts the temperature and weather description, determines an icon,
        and returns a tuple of (text, icon) without displaying the location.
        """
        try:
            # Use the JSON format endpoint.
            url = f"http://wttr.in/{self.location}?format=j1"
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                current = data["current_condition"][0]
                description = current["weatherDesc"][0]["value"]
                temp = current["temp_C"]
                icon = self.get_icon(description)
                # Format the text without showing the location.
                text = f"{temp}°C"
                return text, icon
            else:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error: Failed to fetch weather data (HTTP {response.status_code}).")
                return None
        except Exception as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error fetching weather data: {e}")
            return None

    def get_icon(self, description):
        """
        Determine an appropriate icon based on the weather description.
        Returns a string that AWTRIX recognizes (e.g., "sun", "cloud", "rain", etc.).
        """
        desc = description.lower()
        if "sunny" in desc or ("clear" in desc and "cloud" not in desc):
            return 8953 # sun
        elif "cloud" in desc:
            if "rain" in desc or "drizzle" in desc:
                return 24095 # rain-cloud
            return 91 # cloud
        elif "rain" in desc or "shower" in desc:
            return 3361 # rain
        elif "snow" in desc:
            return 2289 # snow
        elif "thunder" in desc:
            return 11428 # thunder
        else:
            return 91 # cloud

    def send(self, weather_info):
        """
        Send the fetched weather information (text and icon) to the AWTRIX clock.
        """
        if weather_info is None:
            return
        text, icon = weather_info
        payload = {
            "name": "Weather",  # This is a label for clarity
            "icon": icon,       # The icon now reflects the actual weather
            "text": text,
            "repeat": 60        # Display for 60 seconds
        }
        try:
            # Append a unique query parameter to identify the Weather app.
            url = f"{self.awtrix_ip}?name=Weather"
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Weather updated successfully!")
            else:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error updating AWTRIX with weather: {response.text}")
        except Exception as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error sending weather data to AWTRIX: {e}")

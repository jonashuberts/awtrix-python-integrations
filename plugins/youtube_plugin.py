import requests
import logging
from plugin_base import ClockApp

LOGGER = logging.getLogger("awtrix.youtube")


class YouTubeApp(ClockApp):
    def __init__(self, api_key: str, channel_id: str, awtrix_ip: str):
        super().__init__()
        self.api_key = api_key
        self.channel_id = channel_id
        self.awtrix_ip = awtrix_ip

    def update(self) -> int | None:
        url = (
            f"https://www.googleapis.com/youtube/v3/channels?"
            f"part=statistics&id={self.channel_id}&key={self.api_key}"
        )
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()

            items = data.get("items", [])
            if not items:
                LOGGER.error("YouTube API returned no channel stats. Check channel_id and api_key.")
                return None

            return int(items[0]["statistics"]["subscriberCount"])
        except requests.RequestException as exc:
            LOGGER.error("Error fetching YouTube data: %s", exc)
            return None
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.error("Invalid YouTube API response payload: %s", exc)
            return None

    def send(self, subs: int | None) -> None:
        if subs is None:
            return

        payload = {
            "name": "YouTube Subs",
            "icon": "youtube",
            "text": str(subs),
        }
        try:
            url = f"{self.awtrix_ip}?name=YouTubeSubs"
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
            LOGGER.info("YouTube subscriber count updated.")
        except requests.RequestException as exc:
            LOGGER.error("Error sending YouTube data to AWTRIX: %s", exc)

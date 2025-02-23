# plugins/youtube_plugin.py
import requests
from datetime import datetime
from plugin_base import ClockApp

class YouTubeApp(ClockApp):
    def __init__(self, api_key, channel_id, awtrix_ip):
        super().__init__()
        self.api_key = api_key
        self.channel_id = channel_id
        self.awtrix_ip = awtrix_ip

    def update(self):
        """
        Fetch the YouTube subscriber count.
        """
        url = (
            f"https://www.googleapis.com/youtube/v3/channels?"
            f"part=statistics&id={self.channel_id}&key={self.api_key}"
        )
        try:
            response = requests.get(url)
            data = response.json()
            if "items" in data and len(data["items"]) > 0:
                subs = int(data["items"][0]["statistics"]["subscriberCount"])
                return subs
            else:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error: Could not fetch subscriber count. Check API key and Channel ID.")
                return None
        except Exception as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error fetching YouTube data: {e}")
            return None

    def send(self, subs):
        """
        Send the subscriber count to the AWTRIX clock.
        """
        payload = {
            "name": "YouTube Subs",  
            "icon": "youtube",     
            "text": str(subs)        
        }
        try:
            # Append a query parameter to uniquely identify the YouTube app.
            url = f"{self.awtrix_ip}?name=YouTubeSubs"
            response = requests.post(url, json=payload)
            if response.status_code == 200:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] AWTRIX updated successfully for YouTube!")
            else:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error updating AWTRIX for YouTube: {response.text}")
        except Exception as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Error sending YouTube data to AWTRIX: {e}")

import logging
from datetime import datetime, timedelta

import requests
from plugin_base import ClockApp

LOGGER = logging.getLogger("awtrix.pomodoro")


class PomodoroApp(ClockApp):
    def __init__(
        self,
        awtrix_ip: str,
        focus_icon: int = 3389,
        break_icon: int = 91,
    ):
        super().__init__()
        self.awtrix_ip = awtrix_ip
        self.focus_icon = focus_icon
        self.break_icon = break_icon

        self._phase: str | None = None
        self._focus_start: datetime | None = None
        self._focus_end: datetime | None = None
        self._break_start: datetime | None = None
        self._break_end: datetime | None = None

    def _next_aligned_focus_end(self, now: datetime, mode_minutes: int) -> datetime:
        if mode_minutes == 25:
            marks = {25, 50}
        elif mode_minutes == 50:
            marks = {50}
        else:
            raise ValueError("Pomodoro mode must be 25 or 50 minutes")

        cursor = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(180):
            if cursor.minute in marks:
                return cursor
            cursor += timedelta(minutes=1)
        raise RuntimeError("Could not calculate aligned Pomodoro target")

    def start_aligned_session(self, mode_minutes: int, now: datetime | None = None) -> None:
        reference = now or datetime.now()
        focus_end = self._next_aligned_focus_end(reference, mode_minutes)
        break_minutes = 5 if mode_minutes == 25 else 10

        self._phase = "focus"
        self._focus_start = reference
        self._focus_end = focus_end
        self._break_start = focus_end
        self._break_end = focus_end + timedelta(minutes=break_minutes)

    def stop_session(self) -> None:
        self._phase = None
        self._focus_start = None
        self._focus_end = None
        self._break_start = None
        self._break_end = None

    @property
    def is_active(self) -> bool:
        return self._phase in {"focus", "break"}

    @property
    def phase(self) -> str | None:
        return self._phase

    def _seconds_left(self, now: datetime, end: datetime) -> int:
        delta = end - now
        return max(0, int(delta.total_seconds()))

    def _progress(self, now: datetime, start: datetime, end: datetime) -> int:
        total = max(1.0, (end - start).total_seconds())
        elapsed = min(total, max(0.0, (now - start).total_seconds()))
        return int((elapsed / total) * 100)

    def _build_payload(self, now: datetime) -> dict | None:
        if self._phase == "focus" and self._focus_start and self._focus_end:
            seconds_left = self._seconds_left(now, self._focus_end)
            mm, ss = divmod(seconds_left, 60)
            return {
                "text": f"{mm:02d}:{ss:02d}",
                "hold": True,
            }

        if self._phase == "break" and self._break_start and self._break_end:
            seconds_left = self._seconds_left(now, self._break_end)
            mm, ss = divmod(seconds_left, 60)
            return {
                "text": f"{mm:02d}:{ss:02d}",
                "hold": True,
            }

        return None

    def tick(self, now: datetime | None = None) -> bool:
        if not self.is_active:
            return False

        current = now or datetime.now()
        if self._phase == "focus" and self._focus_end and current >= self._focus_end:
            self._phase = "break"
            current = datetime.now()

        if self._phase == "break" and self._break_end and current >= self._break_end:
            self.stop_session()
            self.clear_display()
            return False

        payload = self._build_payload(current)
        if payload is not None:
            self.send(payload)
        return True

    def clear_display(self) -> None:
        try:
            url = f"{self.awtrix_ip.replace('/api/custom', '')}/api/notify/dismiss"
            response = requests.post(url, json={}, timeout=5)
            response.raise_for_status()
            LOGGER.debug("Pomodoro notification dismissed")
        except requests.RequestException as exc:
            LOGGER.warning("Error dismissing Pomodoro notification: %s", exc)

    def update(self) -> dict | None:
        return self._build_payload(datetime.now())

    def send(self, data: dict | None) -> None:
        if data is None:
            return
        try:
            url = f"{self.awtrix_ip.replace('/api/custom', '')}/api/notify"
            LOGGER.debug("Sending Pomodoro notification to %s: %s", url, data)
            response = requests.post(url, json=data, timeout=5)
            response.raise_for_status()
            LOGGER.debug("Pomodoro notification sent successfully (status %d)", response.status_code)
        except requests.RequestException as exc:
            LOGGER.error("Error sending Pomodoro notification to AWTRIX: %s", exc)

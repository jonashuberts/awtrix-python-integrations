# AWTRIX API - Official References (Relevant Extract)

This file is an **official-doc based quick reference** for this repository.

## Official source

- AWTRIX device docs (on the device UI): `http://<awtrix-ip>/`
- Navigate: **API → MQTT / HTTP API**
- Use this local file only as a shortcut for the endpoints used in this project.

## Relevant HTTP endpoints (from official API docs)

1. **Custom Apps**
   - `POST http://<ip>/api/custom?name=<appname>`

2. **Notifications**
   - `POST http://<ip>/api/notify`
   - `POST http://<ip>/api/notify/dismiss`

3. **Settings**
   - `GET/POST http://<ip>/api/settings`

4. **Switch apps**
   - `POST http://<ip>/api/switch` with body like `{"name":"Time"}`
   - `POST http://<ip>/api/nextapp`
   - `POST http://<ip>/api/previousapp`

## Relevant payload keys (from official API docs)

- `text`
- `icon`
- `repeat`
- `duration`
- `hold` (notification)
- `stack` (notification)
- `progress` (custom app / notification)

## Project finding (AWTRIX2)

Pomodoro countdown implementation was removed after integration testing.

- Initial send and stop/dismiss worked.
- Real-time timer text refresh was not reliable for a stable Pomodoro UX on this setup.

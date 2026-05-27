# Universal Remote — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ricardogoncalves89/ha-universal-remote?style=for-the-badge)](https://github.com/ricardogoncalves89/ha-universal-remote/releases)
[![License](https://img.shields.io/github/license/ricardogoncalves89/ha-universal-remote?style=for-the-badge)](LICENSE)

A single Home Assistant integration that exposes **one `media_player` + one `remote`
entity** per TV / media box, with a **unified canonical button vocabulary** across
LG WebOS, Samsung Tizen, Android TV and Apple TV.

## Why this exists

If you have multiple TVs from different vendors and use a remote-style Lovelace
card (RosCard, LG WebOS Remote Card, custom buttons…), you normally end up with:

1. A separate scripted wrapper per button per TV — dozens of scripts to maintain.
2. Vendor-specific cards that don't share configuration.

This integration gives every TV the same shape:

- `media_player.<name>` — standard play/pause/volume/source/mute.
- `remote.<name>` — `send_command` that accepts canonical button names
  (`HOME`, `MENU`, `BACK`, `RED`, `NUM_5`, …) regardless of vendor.

Your Lovelace card then targets `remote.send_command` with the **same button
names for every TV**.

## Status

| Vendor        | Adapter        | Library used      |
|---------------|----------------|-------------------|
| LG WebOS      | ✅ implemented  | `aiowebostv`      |
| Samsung Tizen | ⏳ planned      | `samsungtvws`     |
| Android TV    | ⏳ planned      | `androidtvremote2`|
| Apple TV      | ⏳ planned      | `pyatv`           |

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant.
2. Go to **Integrations**.
3. Click the three-dots menu (top right) → **Custom repositories**.
4. Add `https://github.com/ricardogoncalves89/ha-universal-remote` with category
   **Integration**.
5. Find **Universal Remote** in the HACS list and install.
6. **Restart Home Assistant.**
7. **Settings → Devices & services → Add integration → Universal Remote.**

### Manual

1. Copy `custom_components/universal_remote/` into your HA `config/custom_components/`.
2. Restart Home Assistant.
3. Add the integration via the UI as in step 7 above.

## Configuration

All configuration is done through the UI. For each TV:

1. Pick the device type (currently only LG WebOS is enabled).
2. Enter the IP/hostname and a friendly name.
3. *(LG)* Optionally enter the MAC address — needed for Wake-on-LAN when the TV
   is fully off.
4. Accept the pairing prompt on the TV.

## Usage

### Send a single button

```yaml
service: remote.send_command
target:
  entity_id: remote.lg_sala
data:
  command:
    - HOME
```

### Send a sequence (e.g. tune to channel 123)

```yaml
service: remote.send_command
target:
  entity_id: remote.lg_sala
data:
  command:
    - NUM_1
    - NUM_2
    - NUM_3
    - OK
  delay_secs: 0.3
```

### Canonical button vocabulary

| Group        | Buttons                                                                |
|--------------|------------------------------------------------------------------------|
| Power        | `POWER`, `POWER_ON`, `POWER_OFF`                                        |
| Direction    | `UP`, `DOWN`, `LEFT`, `RIGHT`, `OK`, `BACK`, `EXIT`                     |
| Navigation   | `HOME`, `MENU`, `INFO`, `GUIDE`, `SETTINGS`                             |
| Channel      | `CH_UP`, `CH_DOWN`, `CH_LIST`                                            |
| Volume       | `VOL_UP`, `VOL_DOWN`, `MUTE`                                             |
| Transport    | `PLAY`, `PAUSE`, `STOP`, `REWIND`, `FAST_FORWARD`, `NEXT`, `PREVIOUS`, `RECORD` |
| Color        | `RED`, `GREEN`, `YELLOW`, `BLUE`                                         |
| Keypad       | `NUM_0`–`NUM_9`                                                          |
| Source       | `INPUT`                                                                  |

Sending a button that the adapter does not map will raise a `ValueError` with a
clear message — your automations fail loud, not silent.

### Use with RosCard

```yaml
type: custom:ros-tv-card
entity: media_player.lg_sala
remote_entity: remote.lg_sala
# RosCard calls remote.send_command with canonical button names.
```

The same config block works for every TV — just swap `entity` and `remote_entity`.

## Architecture

```
config_flow.py  ──► creates ConfigEntry
                          │
                          ▼
__init__.py.async_setup_entry
                          │
                          ▼
UniversalRemoteCoordinator
   │
   ├── build_adapter(device_type, config) → RemoteAdapter
   ├── adapter.connect()
   ├── adapter.add_listener(coordinator._on_adapter_state)
   │
   ▼
media_player.py  (CoordinatorEntity) ──┐
remote.py        (CoordinatorEntity) ──┴── read coordinator.data (DeviceState)
                                            and call coordinator.adapter.<method>
```

The adapter pushes state via callbacks rather than polling — matching how
WebOS, Apple TV and Android TV all behave (websocket / mDNS push).

## Adding a new vendor adapter

1. Create `custom_components/universal_remote/adapters/<vendor>.py` subclassing
   `RemoteAdapter`.
2. Define `_BUTTON_MAP: dict[str, str]` mapping canonical → vendor-native.
3. Set `SUPPORTED_BUTTONS` from the map keys.
4. Implement `connect`, `disconnect`, `press_button`, `turn_on`, `turn_off`,
   `volume_*`, `mute`, `select_source`, `play`/`pause`/`stop`.
5. Register the adapter in `adapters/__init__.py:build_adapter`.
6. Add a step to `config_flow.py` for the new device type's UI.
7. Add the library to `manifest.json` → `requirements`.

## Contributing

PRs welcome, especially for new adapters. Please open an issue first to discuss
larger changes.

## License

[MIT](LICENSE)

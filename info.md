# Universal Remote

One integration, one button vocabulary, every TV.

For each TV/box you add, this integration creates:

- A `media_player` entity (play/pause/volume/source)
- A `remote` entity that accepts a unified set of button names
  (`HOME`, `MENU`, `BACK`, `RED`, `NUM_5`, ...) regardless of vendor

This means a single Lovelace card config works across every TV in your home —
the canonical button names are the same whether the underlying device is an
LG WebOS, a Samsung Tizen, an Android TV or an Apple TV.

## Supported devices

- ✅ LG WebOS (via `aiowebostv`)
- ⏳ Samsung Tizen — planned
- ⏳ Android TV / Google TV — planned
- ⏳ Apple TV — planned

## Setup

Settings → Devices & services → Add integration → **Universal Remote**.

Pick the device type, enter the IP, accept the pairing prompt on the TV.

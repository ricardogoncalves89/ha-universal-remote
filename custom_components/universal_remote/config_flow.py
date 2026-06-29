"""Config flow — UI for adding and reconfiguring TVs/boxes."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from .adapters import build_adapter
from .adapters.base import AdapterAuthError, AdapterConnectionError
from .const import (
    CONF_CLIENT_KEY,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_APPLE_TV,
    DEVICE_TYPE_LABELS,
    DEVICE_TYPE_LG_WEBOS,
    DEVICE_TYPE_SAMSUNG,
    DEVICE_TYPES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class UniversalRemoteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Stepped UI for adding and reconfiguring devices."""

    VERSION = 1

    def __init__(self) -> None:
        self._device_type: str | None = None
        self._user_input: dict[str, Any] = {}
        self._reconfigure_entry: ConfigEntry | None = None
        # Apple TV flow state
        self._atv_devices: list[Any] = []  # pyatv scan results
        self._atv_chosen: Any = None
        self._atv_pairing_companion: Any = None
        self._atv_pairing_airplay: Any = None

    @staticmethod
    def async_get_options_flow(entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow for an existing entry."""
        return UniversalRemoteOptionsFlow(entry)

    # ----- Add new device -----

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1 - pick device type."""
        if user_input is not None:
            self._device_type = user_input[CONF_DEVICE_TYPE]
            if self._device_type == DEVICE_TYPE_LG_WEBOS:
                return await self.async_step_lg_webos()
            if self._device_type == DEVICE_TYPE_APPLE_TV:
                return await self.async_step_apple_tv_scan()
            if self._device_type == DEVICE_TYPE_SAMSUNG:
                return await self.async_step_samsung()
            return self.async_abort(reason="device_type_not_implemented")

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_TYPE): vol.In(
                    {dt: DEVICE_TYPE_LABELS[dt] for dt in DEVICE_TYPES}
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_lg_webos(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2a - LG-specific host/name/MAC, then attempt pairing."""
        errors: dict[str, str] = {}

        defaults = {CONF_NAME: "LG TV", CONF_HOST: "", CONF_MAC: ""}
        if self._reconfigure_entry is not None:
            data = self._reconfigure_entry.data
            defaults = {
                CONF_NAME: data.get(CONF_NAME, "LG TV"),
                CONF_HOST: data.get(CONF_HOST, ""),
                CONF_MAC: data.get(CONF_MAC) or "",
            }

        if user_input is not None:
            host = user_input[CONF_HOST]
            self._user_input = {
                CONF_DEVICE_TYPE: DEVICE_TYPE_LG_WEBOS,
                CONF_HOST: host,
                CONF_NAME: user_input[CONF_NAME],
                CONF_MAC: user_input.get(CONF_MAC) or None,
                CONF_CLIENT_KEY: (
                    self._reconfigure_entry.data.get(CONF_CLIENT_KEY)
                    if self._reconfigure_entry is not None
                    else None
                ),
            }

            if self._reconfigure_entry is None:
                await self.async_set_unique_id(f"{DEVICE_TYPE_LG_WEBOS}_{host}")
                self._abort_if_unique_id_configured()

            # Skip pairing on reconfigure when we already have the key
            # and the host didn't change.
            if (
                self._reconfigure_entry is not None
                and self._user_input[CONF_CLIENT_KEY]
                and host == self._reconfigure_entry.data.get(CONF_HOST)
            ):
                return self._finish_reconfigure()

            return await self.async_step_pair()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
                vol.Required(CONF_HOST, default=defaults[CONF_HOST]): str,
                vol.Optional(CONF_MAC, default=defaults[CONF_MAC]): str,
            }
        )
        return self.async_show_form(
            step_id="lg_webos", data_schema=schema, errors=errors
        )

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 3 - pairing handshake (user must accept prompt on TV)."""
        errors: dict[str, str] = {}

        adapter = build_adapter(self._user_input[CONF_DEVICE_TYPE], self._user_input)

        try:
            await adapter.connect()
        except AdapterAuthError:
            errors["base"] = "pair_refused"
        except AdapterConnectionError:
            errors["base"] = "cannot_connect"
        else:
            key = getattr(adapter, "client_key", None)
            await adapter.disconnect()

            if not key:
                errors["base"] = "no_client_key"
            else:
                self._user_input[CONF_CLIENT_KEY] = key
                if self._reconfigure_entry is not None:
                    return self._finish_reconfigure()
                return self.async_create_entry(
                    title=self._user_input[CONF_NAME],
                    data=self._user_input,
                )

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"host": self._user_input[CONF_HOST]},
        )

    # ----- Apple TV: scan -> pick -> pair Companion -> pair AirPlay -> done -----

    async def async_step_apple_tv_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Scan the network for Apple TVs and let the user pick one.

        pyatv.scan() returns every device that speaks any of its supported
        protocols (AirPlay, RAOP, MRP, Companion, DMAP). That includes
        HomePods, AirPort Express, AV receivers with AirPlay 2, and smart
        TVs with AirPlay 2 baked in — none of which are Apple TVs proper.

        We filter to keep only real Apple TVs using two combined signals:
          1. operating_system in {TvOS, Legacy}  — covers Apple TV 4+ and 2/3
          2. service in {Companion, MRP}  — these two protocols are only
             implemented by Apple TVs, not by HomePods/airplay receivers.
        """
        import pyatv  # local import: optional dep loaded at request time
        from pyatv.const import OperatingSystem, Protocol

        errors: dict[str, str] = {}

        if user_input is not None:
            chosen_id = user_input["device"]
            chosen = next(
                (a for a in self._atv_devices
                 if str(a.identifier) == chosen_id),
                None,
            )
            if chosen is None:
                errors["base"] = "cannot_connect"
            else:
                self._atv_chosen = chosen
                await self.async_set_unique_id(f"apple_tv_{chosen_id}")
                self._abort_if_unique_id_configured()
                return await self.async_step_apple_tv_pair_companion()

        try:
            loop = self.hass.loop
            all_devices = await pyatv.scan(loop, timeout=5)
        except Exception:  # noqa: BLE001
            return self.async_show_form(
                step_id="apple_tv_scan",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )

        # Real-Apple-TV filter.
        apple_tv_os = {OperatingSystem.TvOS, OperatingSystem.Legacy}
        apple_tv_only_protocols = {Protocol.Companion, Protocol.MRP}

        def _is_apple_tv(device: Any) -> bool:
            try:
                os = device.device_info.operating_system
            except Exception:  # noqa: BLE001
                os = None
            if os not in apple_tv_os:
                return False
            try:
                service_protocols = {s.protocol for s in device.services}
            except Exception:  # noqa: BLE001
                service_protocols = set()
            return bool(service_protocols & apple_tv_only_protocols)

        self._atv_devices = [d for d in all_devices if _is_apple_tv(d)]

        if not self._atv_devices:
            return self.async_abort(reason="no_devices_found")

        choices = {
            str(a.identifier): f"{a.name} ({a.address})"
            for a in self._atv_devices
            if a.identifier
        }

        schema = vol.Schema({vol.Required("device"): vol.In(choices)})
        return self.async_show_form(
            step_id="apple_tv_scan", data_schema=schema, errors=errors
        )

    async def async_step_apple_tv_pair_companion(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Start (or finish) the Companion-protocol pairing.

        On first entry we kick off pairing on the device — a PIN shows on the
        TV. On submit we finalise with the PIN typed by the user.
        """
        import pyatv
        from pyatv.const import Protocol

        errors: dict[str, str] = {}

        # Submission with PIN
        if user_input is not None and self._atv_pairing_companion is not None:
            pin = user_input.get("pin", "").strip()
            try:
                self._atv_pairing_companion.pin(int(pin))
                await self._atv_pairing_companion.finish()
            except Exception:  # noqa: BLE001
                errors["base"] = "pair_refused"
            else:
                if self._atv_pairing_companion.has_paired:
                    creds = self._atv_pairing_companion.service.credentials
                    self._user_input["credentials_companion"] = creds
                    try:
                        await self._atv_pairing_companion.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._atv_pairing_companion = None
                    return await self.async_step_apple_tv_pair_airplay()
                errors["base"] = "pair_refused"

        # First entry — initiate pairing
        if self._atv_pairing_companion is None:
            try:
                self._atv_pairing_companion = await pyatv.pair(
                    self._atv_chosen, Protocol.Companion, self.hass.loop
                )
                await self._atv_pairing_companion.begin()
            except Exception:  # noqa: BLE001
                return self.async_show_form(
                    step_id="apple_tv_pair_companion",
                    data_schema=vol.Schema({vol.Required("pin"): str}),
                    errors={"base": "cannot_connect"},
                )

        return self.async_show_form(
            step_id="apple_tv_pair_companion",
            data_schema=vol.Schema({vol.Required("pin"): str}),
            errors=errors,
            description_placeholders={"device": self._atv_chosen.name},
        )

    async def async_step_apple_tv_pair_airplay(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Same idea as companion pairing but for AirPlay protocol."""
        import pyatv
        from pyatv.const import Protocol

        errors: dict[str, str] = {}

        if user_input is not None and self._atv_pairing_airplay is not None:
            pin = user_input.get("pin", "").strip()
            try:
                self._atv_pairing_airplay.pin(int(pin))
                await self._atv_pairing_airplay.finish()
            except Exception:  # noqa: BLE001
                errors["base"] = "pair_refused"
            else:
                if self._atv_pairing_airplay.has_paired:
                    creds = self._atv_pairing_airplay.service.credentials
                    self._user_input["credentials_airplay"] = creds
                    try:
                        await self._atv_pairing_airplay.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._atv_pairing_airplay = None
                    return await self._finalise_apple_tv()
                errors["base"] = "pair_refused"

        if self._atv_pairing_airplay is None:
            try:
                self._atv_pairing_airplay = await pyatv.pair(
                    self._atv_chosen, Protocol.AirPlay, self.hass.loop
                )
                await self._atv_pairing_airplay.begin()
            except Exception:  # noqa: BLE001
                return self.async_show_form(
                    step_id="apple_tv_pair_airplay",
                    data_schema=vol.Schema({vol.Required("pin"): str}),
                    errors={"base": "cannot_connect"},
                )

        return self.async_show_form(
            step_id="apple_tv_pair_airplay",
            data_schema=vol.Schema({vol.Required("pin"): str}),
            errors=errors,
            description_placeholders={"device": self._atv_chosen.name},
        )

    async def _finalise_apple_tv(self) -> FlowResult:
        """Both pairings succeeded — write the config entry."""
        entry_data = {
            CONF_DEVICE_TYPE: DEVICE_TYPE_APPLE_TV,
            "name": self._atv_chosen.name,
            CONF_HOST: str(self._atv_chosen.address),
            "atv_identifier": str(self._atv_chosen.identifier),
            "credentials_companion": self._user_input.get("credentials_companion"),
            "credentials_airplay": self._user_input.get("credentials_airplay"),
        }
        return self.async_create_entry(
            title=self._atv_chosen.name,
            data=entry_data,
        )

    # ----- Samsung Tizen: host+name+MAC -> pair (accept on TV) -> done -----

    async def async_step_samsung(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single-step Samsung config: ask for IP, name, MAC, then attempt
        a websocket connection — the TV pops up a permission prompt.

        On success we get back a token and persist it. On user denial or
        timeout we surface a clear error.
        """
        errors: dict[str, str] = {}

        defaults = {
            CONF_NAME: "Samsung TV",
            CONF_HOST: "",
            CONF_MAC: "",
        }

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            mac = (user_input.get(CONF_MAC) or "").strip() or None
            name = user_input[CONF_NAME].strip()
            defaults = {CONF_NAME: name, CONF_HOST: host, CONF_MAC: mac or ""}

            try:
                from samsungtvws.async_remote import SamsungTVWSAsyncRemote
            except ImportError:
                _LOGGER.exception("samsungtvws library not available")
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="samsung",
                    data_schema=self._samsung_schema(defaults),
                    errors=errors,
                )

            # Attempt pairing — this triggers the popup on the TV.
            # NOTE: the WebSocket client name is hardcoded to "HomeAssistant"
            # (matching the official HA samsungtv integration) — see the
            # adapter for why this matters. The user-provided `name` is used
            # only as the HA entity title.
            token: str | None = None
            try:
                remote = SamsungTVWSAsyncRemote(
                    host=host,
                    port=8002,
                    token=None,
                    name="HomeAssistant",
                    timeout=31,  # give user time to accept on TV
                )
                await remote.start_listening()
                token = getattr(remote, "token", None)
                try:
                    await remote.close()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as err:  # noqa: BLE001
                err_name = type(err).__name__
                _LOGGER.warning(
                    "Samsung pairing failed for %s: %s: %s", host, err_name, err
                )
                if "Unauthorized" in err_name or "Unauthorized" in str(err):
                    errors["base"] = "pair_refused"
                else:
                    errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="samsung",
                    data_schema=self._samsung_schema(defaults),
                    errors=errors,
                )

            if not token:
                errors["base"] = "no_client_key"
                return self.async_show_form(
                    step_id="samsung",
                    data_schema=self._samsung_schema(defaults),
                    errors=errors,
                )

            await self.async_set_unique_id(f"samsung_{host}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SAMSUNG,
                    CONF_NAME: name,
                    CONF_HOST: host,
                    CONF_MAC: mac,
                    "samsung_token": token,
                },
            )

        return self.async_show_form(
            step_id="samsung",
            data_schema=self._samsung_schema(defaults),
            errors=errors,
        )

    def _samsung_schema(self, defaults: dict[str, Any]) -> vol.Schema:
        return vol.Schema({
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Samsung TV")): str,
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Optional(CONF_MAC, default=defaults.get(CONF_MAC, "")): str,
        })

    # ----- Reconfigure existing device -----

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point when the user clicks Reconfigure on a device."""
        self._reconfigure_entry = self._get_reconfigure_entry()
        self._device_type = self._reconfigure_entry.data[CONF_DEVICE_TYPE]
        if self._device_type == DEVICE_TYPE_LG_WEBOS:
            return await self.async_step_lg_webos()
        return self.async_abort(reason="device_type_not_implemented")

    def _get_reconfigure_entry(self) -> ConfigEntry:
        """Lookup the entry being reconfigured via the flow context."""
        entry_id = self.context.get("entry_id")
        if not entry_id:
            raise RuntimeError("Reconfigure flow has no entry_id in context")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise RuntimeError(f"Could not find config entry {entry_id}")
        return entry

    def _finish_reconfigure(self) -> FlowResult:
        """Update the existing entry and reload."""
        assert self._reconfigure_entry is not None
        self.hass.config_entries.async_update_entry(
            self._reconfigure_entry,
            data=self._user_input,
            title=self._user_input[CONF_NAME],
        )
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self._reconfigure_entry.entry_id)
        )
        return self.async_abort(reason="reconfigure_successful")


class UniversalRemoteOptionsFlow(config_entries.OptionsFlow):
    """Options flow — lets the user pick which sources show in the picker.

    The full list of sources is read from the running coordinator's adapter
    (which knows what the TV reported via its state callbacks). The user
    selects a subset; if nothing is selected we treat that as "show all"
    so first-time users see everything.
    """

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the source-filter form, catching any internal error so the
        user sees a clear message instead of an HTTP 500."""
        try:
            return await self._async_step_init_impl(user_input)
        except Exception as err:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "Universal Remote options flow blew up; this is a bug. "
                "Falling back to empty schema."
            )
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
                description_placeholders={
                    "note": f" (Internal error: {type(err).__name__}: {err})"
                },
            )

    async def _async_step_init_impl(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        from .const import DOMAIN  # local import to avoid circulars at top
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        # Three sources of "what the TV can offer", in priority order:
        # 1. Live source_map from the adapter (freshest, may be empty if the
        #    adapter just restarted and the state callback hasn't fired yet).
        # 2. known_sources persisted in entry.options (saved by the coordinator
        #    when the adapter last reported a different set). Survives reloads.
        # 3. The user's currently-saved allow-list (ensures saved choices are
        #    always shown as already-selected, even if the TV is off).
        live: list[str] = []
        if coordinator is not None:
            # Preserve the adapter's natural order — do NOT sort
            # alphabetically. This means sources show up in the order
            # declared in _HARDCODED_APPS (or as reported by the device).
            live = list(getattr(coordinator.adapter, "_source_map", {}).keys())

        persisted = self._entry.options.get("known_sources", [])
        if not isinstance(persisted, list):
            persisted = []

        if user_input is not None:
            selected = user_input.get("allowed_sources", [])
            # Preserve known_sources — we don't want to drop the cached list
            # by writing only allowed_sources here.
            new_options = {
                **self._entry.options,
                "allowed_sources": selected,
            }
            return self.async_create_entry(title="", data=new_options)

        current = self._entry.options.get("allowed_sources", [])
        # Make sure all are strings (defensive — some adapters may store
        # placeholder values that snuck in).
        live = [str(x) for x in live if x]
        persisted = [str(x) for x in persisted if x]
        current = [str(x) for x in current if x]
        # Build the checkbox list preserving order, NOT alphabetically.
        # Priority: live (current adapter order) > current (user's saved
        # selection) > persisted (extras from past sessions). Dedupe
        # while keeping first-seen order.
        seen: set[str] = set()
        choices: list[str] = []
        for src in live + current + persisted:
            if src and src not in seen:
                seen.add(src)
                choices.append(src)

        if not choices:
            # No sources to choose from yet. Show an empty schema with only
            # the explanatory note — submitting just dismisses the dialog.
            # We deliberately do NOT include an `allowed_sources` field here
            # because there are no valid values to offer.
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "note": "No sources are visible yet. Turn the device on so it can report its sources, then come back."
                },
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    "allowed_sources",
                    default=current,
                ): vol.All(
                    cv_multi_select(choices),
                ),
            }
        )
        # Pass an empty placeholders dict so the templated description string
        # ("...{note}") is rendered to the empty string rather than crashing
        # with KeyError when no note is applicable.
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"note": ""},
        )


def cv_multi_select(options: list[str]):
    """Tiny voluptuous helper for a multi-select dropdown.

    We import lazily because homeassistant.helpers.selector requires
    HA at import time and we want config_flow to remain importable in tests.
    """
    from homeassistant.helpers import selector

    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )

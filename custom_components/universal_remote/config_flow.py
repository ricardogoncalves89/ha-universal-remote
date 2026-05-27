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
    DEVICE_TYPE_LABELS,
    DEVICE_TYPE_LG_WEBOS,
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
        from .const import DOMAIN  # local import to avoid circulars at top
        coordinator = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        # Available sources come from the adapter's current source_map.
        available: list[str] = []
        if coordinator is not None:
            available = sorted(getattr(coordinator.adapter, "_source_map", {}).keys())

        if user_input is not None:
            # Empty list means "no filter — show everything".
            selected = user_input.get("allowed_sources", [])
            return self.async_create_entry(
                title="",
                data={"allowed_sources": selected},
            )

        current = self._entry.options.get("allowed_sources", [])
        # Make sure currently saved options are still selectable even if the
        # adapter's list happens to be empty right now (TV off, etc.).
        choices = sorted(set(available) | set(current))

        if not choices:
            # Nothing to pick — explain to the user.
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Optional("allowed_sources", default=[]): list,
                }),
                description_placeholders={
                    "note": "No sources are currently visible. Turn the TV on so it can report its inputs and apps, then come back."
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
        return self.async_show_form(step_id="init", data_schema=schema)


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

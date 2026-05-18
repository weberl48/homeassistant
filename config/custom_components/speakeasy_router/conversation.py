"""Conversation entity for speakeasy_router.

On every input:
1. (re)load /config/custom_sentences/en/learned.yaml if mtime changed
2. exact-match the lowercased user text against the registered phrases
3. if matched, fire the target_script and return done_message (action_done)
4. else, delegate to conversation.claude_conversation

This bypasses HA's pipeline `_async_local_fallback_intent_filter`, which only
allows HassGetState and MediaSearchAndPlay to match locally when the
conversation agent advertises CONTROL. Result: learned phrases actually
match locally — sub-second, no Claude round-trip.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

import yaml

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_FALLBACK_AGENT, DOMAIN, LEARNED_YAML

_LOGGER = logging.getLogger(__name__)


def _load_phrases() -> dict[str, dict]:
    """Return {lowercased_phrase: slots_dict} parsed from learned.yaml."""
    if not LEARNED_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(LEARNED_YAML.read_text(encoding="utf-8")) or {}
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not parse %s: %s", LEARNED_YAML, err)
        return {}
    intents_block = (data.get("intents") or {}).get("RunLearnedScript") or {}
    out: dict[str, dict] = {}
    for entry in intents_block.get("data", []) or []:
        slots = entry.get("slots", {}) or {}
        for sentence in entry.get("sentences", []) or []:
            key = (sentence or "").strip().lower()
            if key and key not in out:
                out[key] = slots
    return out


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the router entity."""
    agent = SpeakeasyRouter(hass, entry)
    async_add_entities([agent])


class SpeakeasyRouter(conversation.ConversationEntity):
    """Local-first phrase router with Claude fallback."""

    _attr_has_entity_name = False
    _attr_name = "Speakeasy Router"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}"
        self.entity_id = "conversation.speakeasy_router"
        self._phrases: dict[str, dict] = {}
        self._mtime: float = 0.0

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    def _refresh_phrases(self) -> None:
        """Reload learned.yaml if it changed on disk."""
        try:
            mtime = LEARNED_YAML.stat().st_mtime
        except FileNotFoundError:
            if self._phrases:
                self._phrases = {}
                self._mtime = 0.0
            return
        if mtime != self._mtime:
            self._phrases = _load_phrases()
            self._mtime = mtime
            _LOGGER.info(
                "speakeasy_router: loaded %d learned phrases", len(self._phrases)
            )

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Try local phrase match first; on miss, delegate to Claude."""
        text = (user_input.text or "").strip()
        if not text:
            return await self._delegate(user_input)

        # Hot-reload phrases if file changed
        await self.hass.async_add_executor_job(self._refresh_phrases)

        slots = self._phrases.get(text.lower())
        if slots:
            return await self._fire_script(user_input, slots)

        return await self._delegate(user_input)

    async def _fire_script(
        self,
        user_input: conversation.ConversationInput,
        slots: dict,
    ) -> conversation.ConversationResult:
        """Run the script associated with a learned phrase."""
        target_script = slots.get("target_script")
        done_message = slots.get("done_message") or "Okay"
        script_vars_json = slots.get("script_vars_json") or ""

        variables: dict = {}
        if script_vars_json:
            if isinstance(script_vars_json, dict):
                variables = dict(script_vars_json)
            elif isinstance(script_vars_json, str):
                try:
                    variables = json.loads(script_vars_json)
                except json.JSONDecodeError:
                    _LOGGER.warning(
                        "Bad script_vars_json for %s: %r", target_script, script_vars_json
                    )

        if not target_script or not str(target_script).startswith("script."):
            _LOGGER.warning(
                "Learned phrase has invalid target_script=%r; delegating to Claude",
                target_script,
            )
            return await self._delegate(user_input)

        try:
            await self.hass.services.async_call(
                "script",
                "turn_on",
                {
                    "entity_id": target_script,
                    "variables": variables,
                },
                blocking=False,
                context=user_input.context,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("speakeasy_router: failed to fire %s: %s", target_script, err)
            return await self._delegate(user_input)

        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(done_message)
        return conversation.ConversationResult(
            response=response,
            conversation_id=user_input.conversation_id,
        )

    async def _delegate(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Delegate to the fallback agent (Claude)."""
        return await conversation.async_converse(
            hass=self.hass,
            text=user_input.text,
            conversation_id=user_input.conversation_id,
            context=user_input.context,
            language=user_input.language,
            agent_id=DEFAULT_FALLBACK_AGENT,
            device_id=user_input.device_id,
            satellite_id=user_input.satellite_id,
            extra_system_prompt=user_input.extra_system_prompt,
        )

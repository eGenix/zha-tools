"""Reliable action calls for the ZHA Tools integration.

Wraps a regular Home Assistant action call, such as ``climate.set_temperature``
or ``cover.close_cover``, and keeps calling it until it goes through or an
overall timeout runs out. This helps with Zigbee devices that miss commands now
and then, e.g. sleepy thermostat valves or covers at the edge of the mesh.

An attempt counts as successful when the wrapped action returns without an
error. For ZHA entities this means the device acknowledged the command: ZHA
raises an error when the device does not answer or rejects the command. Other
integrations may not report delivery failures as errors, in which case the first
call always counts as successful.

Home Assistant silently skips unavailable entities when running an entity
action, and reports success without sending anything. The wrapper therefore
checks the targeted entities before each attempt and counts the attempt as
failed, without calling the action, while any of them is unavailable.

Between attempts the wrapper waits ``retry_delay`` seconds at first, then
multiplies the delay by ``backoff`` after each failure, up to ``max_delay``. A
backoff slightly above 1 gives a slow exponential backoff: frequent retries at
first, fewer later on, so a device that is gone for a while is not flooded.

Only failures a retry may fix are retried: errors raised by the action,
attempts which do not finish within ``attempt_timeout`` and unavailable
entities. Invalid calls (unknown action or entity, bad data, missing
permissions, ZHA Tools actions) fail right away, as does any unexpected error,
so the caller sees the real failure.

A failed or timed out attempt may still have reached the device, so the wrapped
action is repeated. Only actions which can safely run more than once (setting a
value or state, not toggling or stepping it) should be wrapped.
"""

from __future__ import annotations

import asyncio
import logging
import time

import voluptuous as vol

from homeassistant.const import (
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ENTITY_MATCH_ALL,
    ENTITY_MATCH_NONE,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN, SERVICE_RETRY_ACTION, STATUS_COMPLETE, STATUS_TIMEOUT
from .helpers import device_name

_LOGGER = logging.getLogger(__name__)

# The clock used for the overall timeout and the sleep between attempts.
# Module-level names, so tests can replace both with a fake clock.
_now = time.monotonic
_sleep = asyncio.sleep

# Minimum time in seconds the last attempt gets before the overall deadline
# (or ``attempt_timeout``, if that is shorter).
MIN_ATTEMPT_TIME = 5.0

# Waits shorter than this (or than the current delay, if that is shorter) are
# not worth another attempt: the wrapper gives up instead.
MIN_RETRY_WAIT = 1.0


def _entity_refs(value: object) -> list[str]:
    """Return the entities named in an ``entity_id`` field as a list.

    Accepts the same forms as a regular action call: a single entity id, a
    comma separated string, a list, or entity registry ids. ``all`` and
    ``none`` name no particular entity and give an empty list.

    Raises:
        vol.Invalid: If the value is not a valid ``entity_id`` field.
    """
    if value is None:
        return []
    refs = cv.comp_entity_ids_or_uuids(value)
    if refs in (ENTITY_MATCH_ALL, ENTITY_MATCH_NONE):
        return []
    return list(refs)


def _device_refs(value: object) -> list[str]:
    """Return the devices named in a ``device_id`` field as a list."""
    if value is None or value == ENTITY_MATCH_NONE:
        return []
    return [str(device_id) for device_id in cv.ensure_list(value)]


def _target_sources(data: dict, target: dict) -> tuple[dict, dict]:
    """Return the two places an action call can name its target in."""
    return target, data


def target_device_names(hass: HomeAssistant, data: dict, target: dict) -> str | None:
    """Return the display names of the devices an action call is aimed at.

    Entities and devices named in ``target`` or ``data`` are resolved to
    their devices; areas, floors and labels are not expanded.

    Args:
        hass: The Home Assistant instance.
        data: The action data, which may carry ``entity_id`` / ``device_id``.
        target: The action target.

    Returns:
        The device names, joined by ``", "`` in order of first mention, or
        ``None`` if no named device was found.

    Raises:
        vol.Invalid: If an ``entity_id`` field is malformed.
    """
    entity_registry = er.async_get(hass)
    device_ids: list[str] = []
    for source in _target_sources(data, target):
        for ref in _entity_refs(source.get(ATTR_ENTITY_ID)):
            entry = entity_registry.async_get(ref)
            if entry is not None and entry.device_id is not None:
                device_ids.append(entry.device_id)
        device_ids.extend(_device_refs(source.get(ATTR_DEVICE_ID)))

    names: list[str] = []
    for device_id in device_ids:
        name = device_name(hass, device_id)
        if name is not None and name not in names:
            names.append(name)
    return ", ".join(names) if names else None


def targeted_entity_ids(
    hass: HomeAssistant, domain: str, data: dict, target: dict
) -> list[str]:
    """Return the entity ids an action call is aimed at, for availability checks.

    Named entities are included as given (registry ids are resolved). Named
    devices contribute their enabled entities of the action's domain, as those
    are the ones an entity action of that domain works on. Areas, floors and
    labels are not expanded.

    Args:
        hass: The Home Assistant instance.
        domain: The domain of the wrapped action, e.g. ``cover``.
        data: The action data.
        target: The action target.

    Returns:
        The entity ids, without duplicates, in order of first mention.

    Raises:
        ServiceValidationError: If a named entity is unknown to Home Assistant
            (neither registered nor having a state), which no retry can fix.
        vol.Invalid: If an ``entity_id`` field is malformed.
    """
    registry = er.async_get(hass)
    entity_ids: list[str] = []
    for source in _target_sources(data, target):
        for ref in _entity_refs(source.get(ATTR_ENTITY_ID)):
            entity_id = er.async_resolve_entity_id(registry, ref)
            if entity_id is None or (
                registry.async_get(entity_id) is None
                and hass.states.get(entity_id) is None
            ):
                raise ServiceValidationError(f"Unknown entity {ref!r}")
            entity_ids.append(entity_id)
        for device_id in _device_refs(source.get(ATTR_DEVICE_ID)):
            entity_ids.extend(
                entry.entity_id
                for entry in er.async_entries_for_device(registry, device_id)
                if entry.domain == domain
            )
    return list(dict.fromkeys(entity_ids))


def _unavailable(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """Return the entities which are unavailable or have no state yet.

    Home Assistant silently skips unavailable entities when running an entity
    action, so calling the action for them would report success without
    sending anything.
    """
    unavailable = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            unavailable.append(entity_id)
    return unavailable


async def async_retry_action(
    hass: HomeAssistant,
    action: str,
    data: dict,
    target: dict,
    timeout: float,
    attempt_timeout: float,
    retry_delay: float,
    backoff: float,
    max_delay: float,
    context: Context | None = None,
) -> dict:
    """Call an action until it succeeds or the overall timeout runs out.

    Before each attempt the targeted entities are checked. If any of them is
    unavailable, the attempt fails without calling the action: Home Assistant
    would skip such entities and report success without sending anything.

    Args:
        hass: The Home Assistant instance.
        action: The action to call, as ``domain.service``.
        data: The action data.
        target: The action target (``entity_id``, ``device_id``, ...).
        timeout: Overall time limit in seconds for all attempts and delays.
        attempt_timeout: Time limit in seconds for a single attempt.
        retry_delay: Seconds to wait after the first failed attempt.
        backoff: Factor the delay is multiplied by after each failed attempt.
        max_delay: Upper limit for the delay between attempts, in seconds.
        context: The context of the calling action, so the wrapped calls run
            with the caller's permissions and are attributed to it.

    Returns:
        A mapping with the ``action``, the targeted ``device_name`` (names of
        several devices are joined by ``", "``; ``None`` if unknown), the
        ``status`` (``complete`` or ``timeout``), the number of ``attempts``,
        the ``elapsed`` seconds and the ``error`` of the last failed attempt
        (``None`` if there was none).

    Raises:
        ServiceValidationError: If the call is invalid (e.g. unknown action or
            entity, or a ZHA Tools action), which no retry can fix.
        vol.Invalid: If the data does not match the action's schema.
        Unauthorized: If the caller may not run the action.
    """
    domain, service = action.split(".", 1)
    if domain == DOMAIN:
        # ZHA Tools actions retry on their own and report failures as a
        # status rather than an error, so wrapping them would only nest
        # timeouts and count every outcome as a success.
        raise ServiceValidationError(
            f"{DOMAIN}.{SERVICE_RETRY_ACTION} cannot wrap {DOMAIN} actions; "
            f"use their own retry options instead"
        )
    name = target_device_names(hass, data, target)
    entity_ids = targeted_entity_ids(hass, domain, data, target)
    label = f"{action} on {name}" if name else action
    started = _now()
    deadline = started + timeout
    # The last attempt gets at least this much time; a retry is only scheduled
    # if it still fits before the deadline.
    min_attempt_time = min(attempt_timeout, MIN_ATTEMPT_TIME)
    _LOGGER.info(
        "Calling %s reliably (timeout=%ss, attempt_timeout=%ss, retry_delay=%ss, "
        "backoff=%s, max_delay=%ss)",
        label,
        timeout,
        attempt_timeout,
        retry_delay,
        backoff,
        max_delay,
    )

    attempts = 0
    delay = min(retry_delay, max_delay)
    error: str | None = None
    status = STATUS_TIMEOUT
    while True:
        attempts += 1
        limit = min(attempt_timeout, max(deadline - _now(), min_attempt_time))
        unavailable = _unavailable(hass, entity_ids)
        if unavailable:
            error = f"unavailable: {', '.join(unavailable)}"
        else:
            timer = asyncio.timeout(limit)
            try:
                async with timer:
                    await hass.services.async_call(
                        domain,
                        service,
                        dict(data),
                        blocking=True,
                        context=context,
                        target=dict(target) or None,
                    )
            except (ServiceValidationError, vol.Invalid, Unauthorized):
                # An invalid call stays invalid; retrying it would only hide
                # that.
                _LOGGER.error("%s is not a valid call; not retrying", label)
                raise
            except TimeoutError as err:
                if timer.expired():
                    error = f"no result within {limit:g}s"
                else:
                    # Raised by the wrapped action itself.
                    error = str(err) or type(err).__name__
            except HomeAssistantError as err:
                error = str(err) or type(err).__name__
            else:
                status = STATUS_COMPLETE
                break

        wait = min(delay, deadline - _now() - min_attempt_time)
        if wait < min(delay, MIN_RETRY_WAIT):
            _LOGGER.debug(
                "%s: attempt %d failed (%s); no time left for another one",
                label,
                attempts,
                error,
            )
            break
        _LOGGER.info(
            "%s: attempt %d failed (%s), retrying in %.1fs",
            label,
            attempts,
            error,
            wait,
        )
        await _sleep(wait)
        delay = min(delay * backoff, max_delay)

    elapsed = round(_now() - started, 1)
    if status == STATUS_COMPLETE:
        _LOGGER.info(
            "%s succeeded after %d attempt(s) in %ss", label, attempts, elapsed
        )
        error = None
    else:
        _LOGGER.warning(
            "%s did not succeed within %ss (%d attempt(s), last error: %s), "
            "reporting %s",
            label,
            timeout,
            attempts,
            error,
            status,
        )
    return {
        "action": action,
        "device_name": name,
        "status": status,
        "attempts": attempts,
        "elapsed": elapsed,
        "error": error,
    }

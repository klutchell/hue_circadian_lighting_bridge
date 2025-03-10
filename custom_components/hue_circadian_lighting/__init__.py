import json
import logging
from homeassistant import config_entries, core
import voluptuous as vol
import aiohue
import aiohttp
from aiohttp import ClientSession
import asyncio
import requests
import re

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'circadian_lighting_bridge'
BRIDGE_DATA_KEY = "circadian_lighting_bridge_bridge"
ENTITY_DOMAIN = "switch"
ENTITY_PREFIX = "circadian_lighting"

async def async_setup(hass, config):
    """Set up the Circadian Lighting Bridge component."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in config:
        for entry in config[DOMAIN]:
            await async_setup_bridge(hass, entry)

    async def sensor_value_changed_event_listener(event):
        """Event listener for sensor value changes."""
        entity_id = 'sensor.circadian_values'
        new_state = event.data.get('new_state')

        if new_state is not None and new_state.entity_id == entity_id:
            _LOGGER.debug("sensor_value_changed_event_listener - New state: %s", new_state)
            print(f"sensor_value_changed_event_listener - New state of {entity_id}: {new_state}")
            await update_hue_scenes(hass, new_state)

    hass.bus.async_listen('state_changed', sensor_value_changed_event_listener)

    return True


def get_hue_gateway_and_key():
    with open('/config/.storage/core.config_entries', 'r') as entries_json:
        response = json.load(entries_json)

    bridges = []

    for entry in response["data"]["entries"]:
        if entry["domain"] == "hue":
            if "data" in entry and "host" in entry["data"] and "api_key" in entry["data"]:
                bridge_ip = entry["data"]["host"]
                bridge_username = entry["data"]["api_key"]
                bridges.append((bridge_ip, bridge_username))
                _LOGGER.info("Bridge IP: %s", bridge_ip)
                _LOGGER.info("Bridge Username: %s", bridge_username)

    if not bridges:
        raise ValueError("No Philips Hue bridges found")

    return bridges


async def update_scene_lights(session, hue_gateway, key, scene, brightness, mired):
    url = f"http://{hue_gateway}/api/{key}/scenes/{scene}/"
    async with session.get(url) as response:
        r = await response.json()
        r = r['lights']
        _LOGGER.debug(f"Updating scene id: {scene}")
        for val in r:
            url = f"http://{hue_gateway}/api/{key}/scenes/{scene}/lightstates/{val}"
            body = json.dumps({'on': True, 'bri': brightness, 'ct': mired})
            async with session.put(url, data=body) as r_response:
                _LOGGER.debug(f"light id: {val} body {body} status code: {r_response.status}")
                if r_response.status != 200:
                    _LOGGER.error(f"light id: {val} body {body} status code: {r_response.status}")
                j = await r_response.json()
                _LOGGER.debug(f"light id: {val} response: {j}")



async def update_hue_scenes(hass, new_state):
    try:
        bridges = get_hue_gateway_and_key()

        async with ClientSession() as session:
            await asyncio.sleep(10)
            tasks = []

            try:
                switch_id = get_switch_id(hass)

                # switch_state = get_switch_state(hass, switch_id)

                brightness = get_brightness(hass, switch_id)
                _LOGGER.info("Got brightness '%s' for switch '%s'", brightness, switch_id)
                
                brightness_scaled = round(brightness * 2.54)
                _LOGGER.info("Got brightness_scaled '%s' for switch '%s'", brightness_scaled, switch_id)

                colortemp_mireds = get_colortemp(hass, switch_id)
                _LOGGER.info("Got colortemp '%s' for switch '%s'", colortemp_mireds, switch_id)
            except Exception as e:
                return

            for bridge_ip, bridge_username in bridges:
                hue_gateway = bridge_ip
                key = bridge_username

                url = f"http://{hue_gateway}/api/{key}/scenes"
                async with session.get(url) as response:
                    r = await response.json()
                    scenes = []
                    for val in r:
                        name = r[val]['name']
                        if re.match(r"Circadian", name):
                            scenes.append(val)

                    for val in scenes:
                        _LOGGER.info(
                            "Updating scene '%s' with brightness: %s, colortemp: %s",
                            val,
                            brightness_scaled,
                            colortemp_mireds,
                        )

                        tasks.append(
                            update_scene_lights(
                                session,
                                hue_gateway,
                                key,
                                val,
                                brightness_scaled,
                                colortemp_mireds,
                            )
                        )

            await asyncio.gather(*tasks)

    except Exception as e:
        raise e

def get_switch_id(hass):
    switches = [
        entity_id
        for entity_id in hass.states.async_entity_ids("switch")
        if entity_id.startswith("switch.circadian_lighting")
    ]

    if len(switches) == 0:
        raise ValueError("No switch found")

    return switches[0]

def get_switch_state(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None:
        raise ValueError(f'Entity {entity_id} not found')
    return state

def get_colortemp(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None:
        raise ValueError(f'Entity {entity_id} not found')

    colortemp_kelvin = state.attributes.get('colortemp')
    if colortemp_kelvin is None:
        raise ValueError(f'colortemp attribute not found for entity {entity_id}')

    colortemp_mireds = int(round(1000000 / colortemp_kelvin))

    return colortemp_mireds

def get_xy_color(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None:
        raise ValueError(f'Entity {entity_id} not found')

    xy_color = state.attributes.get('xy_color')
    if xy_color is None or len(xy_color) != 2:
        raise ValueError(f'xy_color attribute not found or invalid for entity {entity_id}')

    return xy_color

def get_brightness(hass, entity_id):
    state = hass.states.get(entity_id)
    if state is None:
        raise ValueError(f'Entity {entity_id} not found')

    brightness = state.attributes.get('brightness', 100)  # Default to 100 if not found
    if brightness is None:
        raise ValueError(f'brightness attribute not found for entity {entity_id}')
    return brightness

async def async_setup_bridge(hass, config_entry):
    """Set up the Circadian Lighting Bridge component."""
    bridges = get_hue_gateway_and_key()

    for bridge_ip, bridge_username in bridges:
        bridge = aiohue.HueBridgeV2(bridge_ip, bridge_username)

        try:
            await bridge.initialize()
            _LOGGER.debug(
                "Unauthorized: Successfully connected to the Philips Hue bridge at %s",
                bridge_ip,
            )
        except aiohue.Unauthorized:
            _LOGGER.error(
                "Unauthorized: Failed to connect to the Philips Hue bridge at %s. "
                "Please make sure you have entered a valid API key.",
                bridge_ip,
            )
            return False
        except aiohue.BridgeBusy:
            _LOGGER.error(
                "BridgeBusy: Failed to connect to the Philips Hue bridge at %s. "
                "The bridge is busy and cannot process the request at the moment.",
                bridge_ip,
            )
            return False
        except aiohttp.ClientError as e:
            _LOGGER.error(
                "ClientError: Error connecting to the Philips Hue bridge at %s: %s",
                bridge_ip,
                str(e),
            )
            return False

        hass.data[DOMAIN][BRIDGE_DATA_KEY] = bridge

        async def retry_connect():
            for _ in range(3): 
                config = bridge.config.get("")
                if config is not None:
                    _LOGGER.debug(
                        "Successfully connected to the Philips Hue bridge at %s",
                        bridge_ip,
                    )
                    return True
                else:
                    _LOGGER.warning(
                        "Retrying connection to the Philips Hue bridge at %s...",
                        bridge_ip,
                    )
                    await asyncio.sleep(5)
            return False

        # if not await retry_connect():
        #     _LOGGER.error(
        #         "Failed to connect to the Philips Hue bridge at %s. "
        #         "API Key: %s"
        #         "Please check the host and try again.",
        #         bridge_ip, bridge_username
        #     )
        #     return False

    return True


class CircadianLightingBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Circadian Lighting Bridge."""

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            bridge_id = user_input['bridge_id']
            data = {"bridge_id": bridge_id}

            return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("bridge_id"): str,
                }
            ),
        )


async def async_setup_entry(hass, config_entry):
    """Set up Circadian Lighting Bridge from a config entry."""
    bridge_setup_success = await async_setup_bridge(hass, config_entry)

    if bridge_setup_success:
        entity_id = "sensor.circadian_values"
        sensor_state = await hass.async_add_executor_job(
            hass.states.get, entity_id
        )
        await update_hue_scenes(hass, sensor_state)

    return bridge_setup_success


async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    bridge = hass.data[DOMAIN].pop(BRIDGE_DATA_KEY, None)
    if bridge is not None:
        await bridge.close()
    return True

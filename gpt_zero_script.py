#!/usr/bin/env python3
import time
import requests
from gpiozero import Button
from PIL import Image, ImageDraw, ImageFont

# Waveshare e-Paper driver
from waveshare_epd import epd2in7_V2

# ---------- CONFIG ----------
token = input("what is the token: ")
HA_URL = "http://homeassistant.local:8123"    # or "http://192.168.1.xx:8123"
HA_TOKEN = token

LIGHT_ENTITY = "light.basement_lights"
WEATHER_ENTITY = "weather.forecast_home"
ADAPTIVE_SWITCH = "switch.adaptive_lighting_basement_adaptive"

# Button GPIOs - change these to match your 2.7" HAT buttons
BTN1_PIN = 5    # example: KEY0
BTN2_PIN = 6    # example: KEY1
BTN3_PIN = 13   # example: KEY2
BTN4_PIN = 19   # example: KEY3 (if present) or an external button

# Color temperature presets (Kelvin)
WARM_K = 2700
NEUTRAL_K = 4000
COOL_K = 6000

# How often to refresh from HA (seconds)
REFRESH_INTERVAL = 15

# Font path
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# ----------------------------

HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def ha_get_state(entity_id):
    try:
        r = requests.get(f"{HA_URL}/api/states/{entity_id}",
                         headers=HEADERS, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Error getting state for", entity_id, ":", e)
        return None

def ha_call_service(domain, service, data):
    try:
        r = requests.post(f"{HA_URL}/api/services/{domain}/{service}",
                          headers=HEADERS, json=data, timeout=5)
        if r.status_code != 200:
            print("Service error:", r.status_code, r.text)
    except Exception as e:
        print("Error calling service", domain, service, ":", e)

def get_light_info():
    data = ha_get_state(LIGHT_ENTITY)
    if not data:
        return {"state": "unknown", "brightness": None,
                "brightness_pct": None, "kelvin": None, "mired": None}

    state = data["state"]
    attrs = data.get("attributes", {})
    bri = attrs.get("brightness")          # 0–255
    color_temp = attrs.get("color_temp")   # mireds

    if bri is not None:
        bri_pct = int(bri / 255 * 100)
    else:
        bri_pct = None

    kelvin = None
    if color_temp:
        kelvin = int(1_000_000 / color_temp)

    return {
        "state": state,
        "brightness": bri,
        "brightness_pct": bri_pct,
        "mired": color_temp,
        "kelvin": kelvin,
    }

def get_weather_info():
    data = ha_get_state(WEATHER_ENTITY)
    if not data:
        return None
    attrs = data.get("attributes", {})
    temp = attrs.get("temperature")
    cond = data["state"]    # e.g. 'sunny', 'cloudy'
    if temp is None:
        return None
    return {"temp": temp, "condition": cond}

def get_adaptive_on():
    data = ha_get_state(ADAPTIVE_SWITCH)
    if not data:
        return False
    return data.get("state") == "on"

def set_light(brightness=None, kelvin=None, toggle=False):
    if toggle:
        ha_call_service("light", "toggle", {"entity_id": LIGHT_ENTITY})
        return

    payload = {"entity_id": LIGHT_ENTITY}
    if brightness is not None:
        b = max(1, min(255, brightness))
        payload["brightness"] = b

    if kelvin is not None:
        mired = int(1_000_000 / kelvin)
        payload["color_temp"] = mired

    ha_call_service("light", "turn_on", payload)

def cycle_color_temp(light_info):
    current_k = light_info.get("kelvin") or NEUTRAL_K
    presets = [WARM_K, NEUTRAL_K, COOL_K]
    diffs = [abs(current_k - p) for p in presets]
    idx = diffs.index(min(diffs))
    next_k = presets[(idx + 1) % len(presets)]
    set_light(brightness=light_info.get("brightness"), kelvin=next_k)

# ---------- e-Paper init ----------

epd = epd2in7_V2.EPD()
epd.init()
epd.Clear()

# Note: for Waveshare 2.7" HAT, epd.width/height are usually 176x264
# We'll use landscape: WIDTH = epd.height, HEIGHT = epd.width
WIDTH = epd.height   # 264
HEIGHT = epd.width   # 176

font_large = ImageFont.truetype(FONT_PATH, 18)
font_med   = ImageFont.truetype(FONT_PATH, 14)
font_small = ImageFont.truetype(FONT_PATH, 12)

def draw_panel(light_info, weather_info, adaptive_on):
    # Monochrome image (1-bit), all white to start
    image = Image.new('1', (WIDTH, HEIGHT), 255)  # 255=white
    draw = ImageDraw.Draw(image)

    # Time
    now_str = time.strftime("%a %b %d  %H:%M")
    draw.text((4, 2), now_str, font=font_large, fill=0)

    # Light info
    state = light_info["state"].upper()
    bri_pct = light_info["brightness_pct"]
    kelvin = light_info["kelvin"]

    line = f"Lights: {state}"
    if bri_pct is not None:
        line += f" {bri_pct}%"
    if kelvin is not None:
        line += f" {kelvin}K"
    draw.text((4, 26), line, font=font_med, fill=0)

    # Mode
    mode_str = "MODE: NATURAL" if adaptive_on else "MODE: MANUAL"
    draw.text((4, 44), mode_str, font=font_med, fill=0)

    # Weather
    if weather_info:
        draw.text((4, 64), "Weather:", font=font_med, fill=0)
        w_line = f"{weather_info['temp']}° {weather_info['condition']}"
        draw.text((4, 80), w_line, font=font_med, fill=0)

    # Button legend
    draw.text((4, 102), "BTN1: Natural ON/OFF", font=font_small, fill=0)
    draw.text((4, 118), "BTN2: Brighter",       font=font_small, fill=0)
    draw.text((4, 134), "BTN3: Dimmer",         font=font_small, fill=0)
    draw.text((4, 150), "BTN4: Cycle CT (Nat)", font=font_small, fill=0)

    # IMPORTANT: single-buffer display for epd2in7_V2
    epd.display(epd.getbuffer(image))

# ---------- Buttons ----------

btn1 = Button(BTN1_PIN, pull_up=True, bounce_time=0.1)
btn2 = Button(BTN2_PIN, pull_up=True, bounce_time=0.1)
btn3 = Button(BTN3_PIN, pull_up=True, bounce_time=0.1)
btn4 = Button(BTN4_PIN, pull_up=True, bounce_time=0.1)

# We'll refresh after a button press as well as on a timer
state_cache = {
    "light": get_light_info(),
    "weather": get_weather_info(),
    "adaptive": get_adaptive_on(),
}

def refresh_display():
    state_cache["light"] = get_light_info()
    state_cache["weather"] = get_weather_info()
    state_cache["adaptive"] = get_adaptive_on()
    draw_panel(state_cache["light"], state_cache["weather"], state_cache["adaptive"])

def on_btn1():
    """Toggle Natural/Adaptive mode."""
    current = get_adaptive_on()
    if current:
        print("BTN1: Natural OFF")
        ha_call_service("switch", "turn_off", {"entity_id": ADAPTIVE_SWITCH})
    else:
        print("BTN1: Natural ON")
        ha_call_service("switch", "turn_on", {"entity_id": ADAPTIVE_SWITCH})
    refresh_display()

def on_btn2():
    print("BTN2: Brighter")
    info = get_light_info()
    bri = info.get("brightness") or 128
    set_light(brightness=bri + 25)
    refresh_display()

def on_btn3():
    print("BTN3: Dimmer")
    info = get_light_info()
    bri = info.get("brightness") or 128
    set_light(brightness=bri - 25)
    refresh_display()

def on_btn4():
    """Cycle color temp only when Natural mode is ON."""
    if not get_adaptive_on():
        print("BTN4: Natural is OFF, ignoring")
        return
    print("BTN4: Cycle color temp")
    info = get_light_info()
    cycle_color_temp(info)
    refresh_display()

btn1.when_pressed = on_btn1
btn2.when_pressed = on_btn2
btn3.when_pressed = on_btn3
btn4.when_pressed = on_btn4

def main():
    
    try:
        print("Booting!")
        refresh_display()
        last = time.time()
        while True:
            # periodic refresh even without button presses
            now = time.time()
            if now - last > REFRESH_INTERVAL:
                refresh_display()
                last = now
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Exiting, putting display to sleep")
        epd.sleep()

if __name__ == "__main__":
    main()






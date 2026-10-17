"""Build the phone-first 'Home' dashboard (url_path home-dashboard) from the Mushroom dashboard (2026-10-17).

Usage:
    python tools/haws.py '{"type":"lovelace/config","url_path":"mushroom-mushroom"}' > $DIR/dash-p2.json
    python tools/ha_home_dashboard.py $DIR            # writes $DIR/home-save.json
    python tools/haws.py "$(cat $DIR/home-save.json)"  # lovelace/config/save

Room subviews become Bubble Card pop-ups on the Home view; utility subviews (climate, tesla, ...) are copied.
Re-running OVERWRITES any edits made to home-dashboard in the UI — once that dashboard is the one being
edited, treat this as a record of how it was built, not a tool to run again.
"""
import copy
import json
import re
import sys

SP = sys.argv[1]
NEW = "home-dashboard"
DANIEL = "5d0a8897a9304054aa8225fe62f5909c"  # Daniel Lin (verified via hass.user.id)
ANGIE = "d4be585fe0c24c9890c83a0f13cb155f"  # Angie Ng

src = json.load(open(f"{SP}/dash-p2.json"))["result"]
views = {v["path"]: v for v in src["views"]}

ROOMS = ["bedroom", "kitchen-and-dining", "living-room", "office", "utility", "powder", "game-room", "study",
         "garage", "entrance", "baby-room", "guest-room"]
SUBVIEWS = ["climate", "tesla", "vacuum", "admin", "homelab", "maintenance"]


def rewrite_nav(obj, in_home):
    """Old /mushroom-mushroom/<x> links -> pop-up hashes (rooms) or the new dashboard's subviews."""
    s = json.dumps(obj)

    def sub(m):
        p = m.group(1)
        if p in ROOMS:
            return f'"#{p}"' if in_home else f'"/{NEW}/home#{p}"'
        return f'"/{NEW}/{p}"'
    s = re.sub(r'"/mushroom-mushroom/([a-z-]+)"', sub, s)
    assert "/mushroom-mushroom" not in s, re.findall(r'/mushroom-mushroom[^"]*', s)
    return json.loads(s)


def flat_tiles(cards):
    for x in cards:
        if x.get("type") == "horizontal-stack":
            yield from flat_tiles(x["cards"])
        else:
            yield x


home_old = views["home"]["cards"]
old_tiles = list(flat_tiles(home_old[home_old.index(next(x for x in home_old if x.get("title") == "Rooms")) + 1:]))
tile_by_nav = {(t.get("tap_action") or {}).get("navigation_path", "").rsplit("/", 1)[-1]: t for t in old_tiles}
assert set(ROOMS) <= set(tile_by_nav), set(ROOMS) - set(tile_by_nav)

half = {"grid_options": {"columns": 6}}
full = {"grid_options": {"columns": 12}}


def heading(text, icon=None):
    h = {"type": "heading", "heading": text, "heading_style": "title"}
    if icon:
        h["icon"] = icon
    return h


def light_tile(entity, name=None):
    t = {"type": "tile", "entity": entity, "tap_action": {"action": "toggle"},
         "icon_tap_action": {"action": "toggle"}, "hold_action": {"action": "more-info"}, **half}
    if name:
        t["name"] = name
    return t



# ---------- Right now: one strip of status pills ----------
# Context pills (always): people, weather, thermostat, solar (daytime), car. Alert pills appear only when
# something is happening. Each pill opens its detail (pop-up or page), so the strip stays one or two lines.
LIGHTS_EXCLUDE = ["light.resty_light", "light.resty_clock", "light.master_bedroom_fan_light"]


def chip(content, icon, color, tap, entity=None, picture=None):
    c = {"type": "template", "content": content, "tap_action": tap}
    if picture:
        c["picture"] = picture
    else:
        c["icon"] = icon
        c["icon_color"] = color
    if entity:
        c["entity"] = entity
        c["hold_action"] = {"action": "more-info"}
    return c


def when(conditions, c):
    return {"type": "conditional", "conditions": conditions, "chip": c}


def nav(path):
    return {"action": "navigate", "navigation_path": path}


def person_chip(entity):
    # avatar alone when home; avatar + where they are when not
    return chip("{{ '' if is_state('" + entity + "', 'home') else (states('" + entity + "') | replace('not_home', 'Away') | replace('_', ' ') | title) }}",
                None, None, {"action": "more-info"}, entity=entity,
                picture="{{ state_attr('" + entity + "', 'entity_picture') }}")


car_alert = ("is_state('lock.the_last_light_doors', 'unlocked') or is_state('cover.the_last_light_trunk', 'open')"
             " or is_state('cover.the_last_light_frunk', 'open') or is_state('cover.the_last_light_windows', 'open')")
car_content = ("{% set p = [states('sensor.the_last_light_battery') | int(0) ~ '%'] %}"
               "{% if is_state('sensor.tesla_state', 'driving') %}{% set p = p + ['Driving'] %}"
               "{% else %}"
               "{% if is_state('binary_sensor.the_last_light_charging', 'on') %}{% set p = p + ['Charging'] %}{% endif %}"
               "{% if is_state('lock.the_last_light_doors', 'unlocked') %}{% set p = p + ['Unlocked'] %}{% endif %}"
               "{% if is_state('cover.the_last_light_trunk', 'open') %}{% set p = p + ['Trunk open'] %}{% endif %}"
               "{% if is_state('cover.the_last_light_frunk', 'open') %}{% set p = p + ['Frunk open'] %}{% endif %}"
               "{% if is_state('cover.the_last_light_windows', 'open') %}{% set p = p + ['Windows open'] %}{% endif %}"
               "{% endif %}{{ p | join(' · ') }}")
car_color = ("{% if is_state('sensor.tesla_state', 'driving') %}blue{% elif " + car_alert + " %}red"
             "{% elif is_state('binary_sensor.the_last_light_charging', 'on') %}green{% else %}grey{% endif %}")

pills = {
    "type": "custom:mushroom-chips-card",
    "alignment": "start",
    "chips": [
        person_chip("person.daniel_lin"),
        person_chip("person.angie_ng"),
        {"type": "weather", "entity": "weather.pirateweather", "show_conditions": True, "show_temperature": True,
         "tap_action": nav(f"/{NEW}/climate")},
        chip("{{ states('select.thermostat_mode') }} · {{ states('sensor.downstairs_current_temperature') | float(0) | round(0) | int }}°",
             "{{ state_attr('select.thermostat_mode', 'icon') }}", "deep-orange", nav(f"/{NEW}/climate"), entity="select.thermostat_mode"),
        when([{"condition": "numeric_state", "entity": "sensor.my_home_solar_power", "above": 50}],
             chip("{{ (states('sensor.my_home_solar_power') | float(0) / 1000) | round(1) }} kW", "mdi:solar-power", "amber",
                  nav("#garage"), entity="sensor.my_home_solar_power")),
        chip(car_content, "mdi:car-electric", car_color, nav(f"/{NEW}/tesla"), entity="sensor.the_last_light_battery"),
        # ---- alerts ----
        when([{"condition": "numeric_state", "entity": "sensor.lights_on_count", "above": 0}],
             chip("{{ states('sensor.lights_on_count') }} on", "mdi:lightbulb-group", "amber", nav("#lights"))),
        when([{"condition": "or", "conditions": [{"condition": "state", "entity": "sensor.washer", "state": "on"},
                                                  {"condition": "state", "entity": "sensor.dryer", "state": "on"}]}],
             chip("{% set w = is_state('sensor.washer', 'on') %}{{ 'Washer' if w else 'Dryer' }} "
                  "{{ (state_attr('sensor.washer' if w else 'sensor.dryer', 'remain_time') | string)[:-3] }}",
                  "mdi:washing-machine", "blue", nav("#utility"))),
        when([{"condition": "state", "entity": "media_player.resty_media_player", "state": "playing"}],
             chip("{{ state_attr('media_player.resty_media_player', 'current_favorite_name') or 'Hatch playing' }}",
                  "mdi:speaker", "light-blue", nav("#baby-room"), entity="media_player.resty_media_player")),
        when([{"condition": "numeric_state", "entity": "sensor.vacuums_cleaning", "above": 0}],
             chip("{{ states('sensor.vacuums_cleaning') }} cleaning", "mdi:robot-vacuum", "green", nav(f"/{NEW}/vacuum"))),
        when([{"condition": "numeric_state", "entity": "sensor.maintenance_attention", "above": 0}],
             chip("{{ states('sensor.maintenance_attention') }}", "mdi:wrench", "red", nav(f"/{NEW}/maintenance"))),
    ],
    **full,
}
right_now = {"type": "grid", "cards": [heading("Right now", "mdi:clock-outline"), pills]}

# "#lights" pop-up: what's on, tap to turn off, plus All lights off
lights_on = {
    "type": "custom:auto-entities",
    "show_empty": False,
    "card": {"type": "grid", "columns": 2, "square": False, "cards": []},  # empty list: grid rejects a missing one
    "card_param": "cards",
    "sort": {"method": "name"},
    "filter": {"template": (
        "[{% for e in states.light | selectattr('state', 'eq', 'on') | rejectattr('attributes.entity_id', 'defined')"
        " | map(attribute='entity_id') | reject('in', " + json.dumps(LIGHTS_EXCLUDE).replace('"', "'") + ") %}"
        "{{ {'type': 'tile', 'entity': e, 'tap_action': {'action': 'toggle'}, 'icon_tap_action': {'action': 'toggle'},"
        " 'hold_action': {'action': 'more-info'}} }}{{ ',' if not loop.last }}{% endfor %}]")},
}
all_off = {
    "type": "custom:mushroom-template-card",
    "primary": "All lights off",
    "secondary": "Skips Holden's lamp and the bedroom fan light",
    "icon": "mdi:lightbulb-group-off",
    "color": "amber",
    "tap_action": {"action": "perform-action", "perform_action": "script.all_lights_off",
                   "confirmation": {"text": "Turn off all lights (except Holden's lamp and the bedroom fan light)?"}},
}
lights_popup = {"type": "custom:bubble-card", "card_type": "pop-up", "hash": "#lights", "name": "Lights on",
                "icon": "mdi:lightbulb-group", "button_type": "name", "cards": [all_off, lights_on]}

# ---------- Favorites: one personal list each (from 30 days of per-user history) ----------
daniel = {"type": "grid", "visibility": [{"condition": "user", "users": [DANIEL]}], "cards": [
    heading("Favorites", "mdi:star"),
    light_tile("light.bedside_lamp_light", "Bedside Lamp"),
    light_tile("light.master_bedroom_lights", "Bedroom"),
    light_tile("light.bedroom_fan_light", "Fan Light"),
    light_tile("light.globe_lamp_light", "Holden's Lamp"),
    light_tile("light.kitchen_lights", "Kitchen"),
    light_tile("light.office_lights", "Office"),
    light_tile("light.garage_light_group", "Garage"),
    light_tile("light.holden_desk_lamp", "Holden's Desk Lamp"),
    light_tile("light.game_room_lights", "Game Room"),
    light_tile("light.study_light_group", "Study"),
    {"type": "tile", "entity": "select.thermostat_mode", "name": "Thermostat", "features": [{"type": "select-options"}], **full},
]}
# Angie's: the shared bedroom/Holden items + her next-most-used (thin data: 1-2 uses each); she can swap them.
angie = {"type": "grid", "visibility": [{"condition": "user", "users": [ANGIE]}], "cards": [
    heading("Favorites", "mdi:star"),
    light_tile("light.bedside_lamp_light", "Bedside Lamp"),
    light_tile("light.master_bedroom_lights", "Bedroom"),
    light_tile("light.bedroom_fan_light", "Fan Light"),
    light_tile("light.globe_lamp_light", "Holden's Lamp"),
    light_tile("light.master_bathroom_lights", "Bathroom"),
    light_tile("fan.master_bathroom_exhaust", "Bathroom Fan"),
    light_tile("light.living_room_lights", "Living Room"),
    light_tile("light.master_entry", "Bedroom Entry"),
    {"type": "tile", "entity": "vacuum.roborock_q7_max", "name": "Roborock",
     "features": [{"type": "vacuum-commands", "commands": ["start_pause", "return_home"]}], **full},
]}

# ---------- Rooms ----------
def compact(sec):
    """'🌡️{{ states(t, with_unit=true) }} 💧{{ states(h, with_unit=true) }}' -> '68° · 💧44%' so it fits a 360 px phone."""
    sec = re.sub(r"\U0001F321\uFE0F\{\{ states\('([^']+)', with_unit=true\) \}\}",
                 r"{{ states('\1') | float(0) | round(0) | int }}°", sec)
    sec = re.sub(r" ?\U0001F4A7\{\{ states\('([^']+)', with_unit=true\) \}\}",
                 r" · 💧{{ states('\1') | float(0) | round(0) | int }}%", sec)
    return sec.strip()


room_cards = [heading("Rooms", "mdi:home")]
for t in old_tiles:
    nav = (t.get("tap_action") or {}).get("navigation_path", "").rsplit("/", 1)[-1]
    n = copy.deepcopy(t)
    n.pop("double_tap_action", None)
    n["hold_action"] = {"action": "more-info"}
    if nav in ROOMS:
        n["tap_action"] = {"action": "navigate", "navigation_path": f"#{nav}"}
        if nav == "baby-room":  # night-safe: the remote's script never turns his lamp off at night
            n["icon_tap_action"] = {"action": "perform-action", "perform_action": "script.holden_light_toggle"}
        else:
            n["icon_tap_action"] = {"action": "toggle"}
    else:
        n["icon_tap_action"] = {"action": "none"}
    n.update(half)
    n["secondary"] = compact(n.get("secondary", ""))
    room_cards.append(rewrite_nav(n, True))
room_cards += [
    {"type": "custom:mushroom-template-card", "primary": "Vacuums", "icon": "mdi:robot-vacuum",
     "secondary": "{% set n = states('sensor.vacuums_cleaning') | int(0) %}{{ n ~ ' cleaning' if n else 'Docked' }}",
     "color": "{{ 'green' if states('sensor.vacuums_cleaning') | int(0) > 0 else '' }}",
     "tap_action": {"action": "navigate", "navigation_path": f"/{NEW}/vacuum"}, **half},
    {"type": "custom:mushroom-template-card", "primary": "Admin", "icon": "mdi:security",
     "tap_action": {"action": "navigate", "navigation_path": f"/{NEW}/admin"}, **half},
]
rooms = {"type": "grid", "cards": room_cards}

# ---------- Layout normalization (phones: Pixel 10a ~412 px, Galaxy Z Flip 5 ~360 px) ----------
# Small controls (light / fan / entity / template cards) are re-flowed two per row, all with
# fill_container and a state line, so neighbours always match. Anything wider (cameras, media players,
# graphs, glances, ...) gets its own row. A lone small card takes the full row.
SMALL = {"custom:mushroom-light-card", "custom:mushroom-fan-card", "custom:mushroom-entity-card", "custom:mushroom-template-card"}
SHORT_NAMES = {  # names that truncate at ~150 px (half of a 360 px phone)
    "Bathroom Light": "Bath Light", "Bathroom Fan": "Bath Fan", "Whole House Humidifier": "Humidifier",
    "Big Humidier": "Humidifier", "Overhead Lights": "Overhead", "Auto Brightness and Color": "Adaptive Lighting",
    "Remote test mode": "Test Mode", "Hatch Noise Machine": "Hatch Sound", "Flash Lights": "Flash",
    "Clear Selections": "Clear", "Motion Sensor": "Motion", "Mushroom Lamp": "Mushroom",
}


def is_group_header(c):  # the room's "Lights · N of M on" row stays full width
    return c.get("type") == "custom:mushroom-template-card" and c.get("primary") == "Lights"


def tidy_small(c, horizontal=False):
    c = copy.deepcopy(c)
    for k in ("name", "primary"):
        if c.get(k) in SHORT_NAMES:
            c[k] = SHORT_NAMES[c[k]]
    if c.get("secondary_info") == "none":
        del c["secondary_info"]
    if c.get("layout") == "vertical" and (horizontal or c["type"] != "custom:mushroom-template-card"):
        c["layout"] = "horizontal"
    if horizontal:
        if c.pop("vertical", None):
            c["layout"] = "horizontal"
        if c["type"] == "custom:mushroom-template-card" and not c.get("secondary") and c.get("entity"):
            c["secondary"] = "{{ states('" + c["entity"] + "') | replace('_', ' ') | title }}"  # match neighbours' state line
    c["fill_container"] = True
    return c


def units(cards):
    """Yield ('pair', [a, b]) for deliberate 2-card rows, ('single', c) for small cards, ('wide', c) otherwise."""
    for c in cards:
        kids = c.get("cards", [])
        if c.get("type") == "horizontal-stack" and kids and all(x.get("type") in SMALL for x in kids):
            headers = [x for x in kids if is_group_header(x)]
            rest = [x for x in kids if not is_group_header(x)]
            for h in headers:
                yield "wide", h
            if len(rest) == 2:
                yield "pair", rest
            else:
                for x in rest:
                    yield "single", x
        elif c.get("type") in SMALL:
            yield ("wide" if is_group_header(c) else "single"), c
        elif c.get("type") == "horizontal-stack" and any(x.get("type") == "custom:mushroom-media-player-card" for x in kids):
            for x in kids:  # media players expand when playing; each gets its own row
                yield "wide", x
        else:
            yield "wide", c


def reflow(cards, popup=False):
    """Keep deliberate pairs; pair up stray single small cards with the next stray single (until a wide card).
    popup=True: every row spans the pop-up's grid (Bubble Card otherwise makes lone cards half width) and all
    small cards use the horizontal layout."""
    out, open_slot = [], None

    def tidy(c):
        return tidy_small(c, horizontal=popup)
    for kind, c in units(cards):
        if kind == "pair":
            out.append({"type": "horizontal-stack", "cards": [tidy(x) for x in c]})
        elif kind == "single":
            if open_slot is not None:
                out[open_slot] = {"type": "horizontal-stack", "cards": [out[open_slot], tidy(c)]}
                open_slot = None
            else:
                out.append(tidy(c))
                open_slot = len(out) - 1
        else:
            open_slot = None
            if c.get("type") in SMALL:
                c = tidy(c)
            elif c.get("name") in SHORT_NAMES:
                c = dict(c, name=SHORT_NAMES[c["name"]])
            out.append(c)
    if popup:
        out = [dict(c, grid_options={"columns": 12}) for c in out]
    return out


def round_chip(ch):
    """Room chips: '43.64%' / '69.5 °F' -> '44%' / '70°' (same format as the Home room tiles)."""
    e = ch.get("entity", "")
    if ch.get("type") != "entity" or not e.startswith("sensor."):
        return ch
    for key, icon, color, unit in (("temperature", "mdi:thermometer", "deep-purple", "°"),
                                   ("humidity", "mdi:water-percent", "blue", "%"),
                                   ("illuminance", "mdi:brightness-7", "amber", " lx"), ("_range", "mdi:map-marker-distance", "blue", " mi")):
        if key in e:
            return {"type": "template", "entity": e, "icon": ch.get("icon", icon), "icon_color": color,
                    "content": "{{ states('" + e + "') | float(0) | round(0) | int }}" + unit,
                    "tap_action": {"action": "more-info"}}
    return ch


def strip_chips(cards):
    out = []
    for c in cards:
        if c.get("type") == "custom:mushroom-chips-card":
            c = copy.deepcopy(c)
            c["chips"] = [round_chip(x) for x in c["chips"] if x.get("type") not in ("back", "menu", "spacer")]
            c["alignment"] = "start"
            if not c["chips"]:
                continue
        out.append(c)
    return out


# ---------- Room pop-ups ----------
popups = []
for r in ROOMS:
    v = copy.deepcopy(views[r])
    cards = v["cards"]
    title = cards[0].get("title") if cards and cards[0].get("type") == "custom:mushroom-title-card" else v.get("title")
    if cards and cards[0].get("type") == "custom:mushroom-title-card":
        cards = cards[1:]
    out = reflow(strip_chips(cards), popup=True)
    t = tile_by_nav[r]
    popups.append({
        "type": "custom:bubble-card",
        "card_type": "pop-up",
        "hash": f"#{r}",
        "name": title or t.get("primary"),
        "icon": t.get("icon"),
        "button_type": "name",
        "cards": rewrite_nav(out, True),
    })
popup_section = {"type": "grid", "cards": popups + [lights_popup]}

home = {
    "title": "Home",
    "path": "home",
    "icon": "mdi:home",
    "type": "sections",
    "max_columns": 3,
    "badges": [],
    "sections": [right_now, daniel, angie, rooms, popup_section],
}

subs = []
VIEW_TITLES = {"climate": "Climate"}
for p in SUBVIEWS:
    v = rewrite_nav(copy.deepcopy(views[p]), False)
    v["subview"] = True
    v["back_path"] = f"/{NEW}/home"
    v["title"] = VIEW_TITLES.get(p, v.get("title"))
    cards = v["cards"]
    # the header already shows the title and a back arrow: drop a leading title card that repeats it
    if cards and cards[0].get("type") == "custom:mushroom-title-card" and \
            (cards[0].get("title") or "").lower().replace(" ", "") in (p, (v.get("title") or "").lower().replace(" ", ""), "vacuums", "homelab"):
        cards = cards[1:]
    v["cards"] = strip_chips(cards)
    subs.append(v)
sv = {v["path"]: v for v in subs}

# Tesla: 90 KB picture instead of 16.8 MB; short labels that fit a 360 px phone
for c in sv["tesla"]["cards"]:
    if c.get("type") == "picture" and c.get("image") == "/local/ModelY_56.jpg":
        c["image"] = "/local/tesla/normal.jpeg"
    for x in c.get("cards", []):
        if x.get("name") in SHORT_NAMES:
            x["name"] = SHORT_NAMES[x["name"]]
        if x.get("entity") == "input_number.model_y_charge_limit":
            x["name"] = "Limit"
    for e in c.get("entities", []) if c.get("type") == "glance" else []:
        e["name"] = {"Energy Added": "Added", "Front Left": "Front L", "Front Right": "Front R",
                     "Rear Left": "Rear L", "Rear Right": "Rear R"}.get(e.get("name"), e.get("name"))

# Tesla battery chip: "75.0 %" -> "75%"
for c in sv["tesla"]["cards"]:
    for ch in c.get("chips", []):
        if ch.get("content") == '{{ states("sensor.the_last_light_battery") }} %':
            ch["content"] = '{{ states("sensor.the_last_light_battery") | float(0) | round(0) | int }}%'

# Vacuum: room toggles 3 per row (4 truncated every name), short names
VAC_NAMES = {"Main Floor": "Main Floor", "M. Bedroom": "Bedroom", "M. Bathroom": "Bathroom", "Office": "Office",
             "TV/Game": "Game Room", "Study": "Study", "Holden's": "Holden's", "Guest": "Guest",
             "G. Bathroom": "Guest Bath", "S. Bathroom": "Study Bath", "Hallway": "Hallway"}
vc = sv["vacuum"]["cards"]
rooms_vac = [x for c in vc if c.get("type") == "horizontal-stack" for x in c["cards"]
             if (x.get("entity") or "").startswith("input_boolean.clean_")]
assert len(rooms_vac) == 11, len(rooms_vac)
for x in rooms_vac:
    x["name"] = VAC_NAMES[x["name"]]
first = next(i for i, c in enumerate(vc) if c.get("type") == "horizontal-stack" and rooms_vac[0] in c["cards"])
vc[:] = [c for c in vc if not (c.get("type") == "horizontal-stack" and any(x in rooms_vac for x in c["cards"]))]
vc[first:first] = [{"type": "horizontal-stack", "cards": rooms_vac[i:i + 3]} for i in range(0, 11, 3)]
for c in vc:
    for x in c.get("cards", []):
        if x.get("primary") in SHORT_NAMES:
            x["primary"] = SHORT_NAMES[x["primary"]]
        if "claeaning" in (x.get("secondary") or ""):
            x["secondary"] = x["secondary"].replace("claeaning", "cleaning")
for i, c in enumerate(vc):
    if c.get("type") == "horizontal-stack" and len(c["cards"]) == 4 and all(
            (x.get("entity") or "").startswith("automation.") for x in c["cards"]):
        vc[i:i + 1] = [{"type": "horizontal-stack", "cards": c["cards"][:2]}, {"type": "horizontal-stack", "cards": c["cards"][2:]}]
        break

# Admin: threshold names that fit
ADMIN_NAMES = {"AC > Temperature": "AC Above", "Heater  < Threshold": "Heat Below", "Away - AC Temp": "Away AC",
               "Away - Heater Temp": "Away Heat", "Home - AC Temp": "Home AC", "Home - Heater Temp": "Home Heat",
               "Sleep - AC Temp": "Sleep AC", "Sleep - Heater Temp": "Sleep Heat"}
for c in sv["admin"]["cards"]:
    for x in c.get("cards", []):
        if x.get("name") in ADMIN_NAMES:
            x["name"] = ADMIN_NAMES[x["name"]]

# Climate: rows stay as they are (Dog Sitting's description needs the full width); tidy single small cards;
# graphs named by place only, whole numbers
cc = sv["climate"]["cards"]
for c in cc:
    if c.get("type") == "horizontal-stack" and len(c["cards"]) == 1 and c["cards"][0].get("type") in SMALL:
        c["cards"][0] = tidy_small(c["cards"][0])
for c in cc:
    if c.get("type") == "horizontal-stack" and all(x.get("type") == "custom:mini-graph-card" for x in c["cards"]):
        place = None
        for x in c["cards"]:
            x["decimals"] = 0
            for e in x["entities"]:
                n = e.get("name") or ""
                n = re.sub(r"\s*(Temp\.?|Temperature|Humidity)$", "", n).strip()
                if n:
                    place = place or n
                e["name"] = n or place or e.get("name")

cfg = {"title": "Home", "views": [home] + subs}
json.dump({"type": "lovelace/config/save", "url_path": NEW, "config": cfg}, open(f"{SP}/home-save.json", "w"))
print("views:", [v["path"] for v in cfg["views"]], "| pop-ups:", [p["hash"] for p in popups],
      "| room tiles:", len(room_cards) - 1, "| bytes:", len(json.dumps(cfg)))

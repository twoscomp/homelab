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


# ---------- Top: shortcuts ----------
chips = copy.deepcopy(home_old[1])
assert chips["type"] == "custom:mushroom-chips-card"
chips["chips"] = [c for c in chips["chips"] if c.get("type") != "spacer"]
chips["alignment"] = "justify"
top = {"type": "grid", "cards": [rewrite_nav(chips, True) | full]}

# ---------- Right now ----------
LIGHTS_EXCLUDE = ["light.resty_light", "light.resty_clock", "light.master_bedroom_fan_light"]
lights_on = {
    "type": "custom:auto-entities",
    "show_empty": False,
    "card": {"type": "grid", "columns": 2, "square": False},
    "card_param": "cards",
    "sort": {"method": "name"},
    "filter": {"template": (
        "[{% for e in states.light | selectattr('state', 'eq', 'on') | rejectattr('attributes.entity_id', 'defined')"
        " | map(attribute='entity_id') | reject('in', " + json.dumps(LIGHTS_EXCLUDE).replace('"', "'") + ") %}"
        "{{ {'type': 'tile', 'entity': e, 'tap_action': {'action': 'toggle'}, 'icon_tap_action': {'action': 'toggle'},"
        " 'hold_action': {'action': 'more-info'}} }}{{ ',' if not loop.last }}{% endfor %}]")},
    **full,
}
all_off = {
    "type": "custom:mushroom-template-card",
    "primary": "All lights off",
    "secondary": "Skips Holden's lamp and the bedroom fan light",
    "icon": "mdi:lightbulb-group-off",
    "color": "amber",
    "tap_action": {"action": "perform-action", "perform_action": "script.all_lights_off",
                   "confirmation": {"text": "Turn off all lights (except Holden's lamp and the bedroom fan light)?"}},
    "visibility": [{"condition": "numeric_state", "entity": "sensor.lights_on", "above": 0}],
    **full,
}
utility_tile = tile_by_nav["utility"]
laundry = {
    "type": "custom:mushroom-template-card",
    "primary": "{{ 'Washer' if is_state('sensor.washer', 'on') else 'Dryer' }}",
    "secondary": ("{% if is_state('sensor.washer', 'on') %}{{ state_attr('sensor.washer', 'remain_time') }} to go"
                  "{% else %}{{ state_attr('sensor.dryer', 'remain_time') }} to go{% endif %}"),
    "icon": "mdi:washing-machine",
    "color": "blue",
    "tap_action": {"action": "navigate", "navigation_path": "#utility"},
    "visibility": [{"condition": "or", "conditions": [
        {"condition": "state", "entity": "sensor.washer", "state": "on"},
        {"condition": "state", "entity": "sensor.dryer", "state": "on"}]}],
    **half,
}
hatch = {
    "type": "custom:mushroom-media-player-card",
    "entity": "media_player.resty_media_player",
    "name": "Holden's Hatch",
    "icon": "mdi:speaker",
    "use_media_info": True,
    "media_controls": ["play_pause_stop"],
    "volume_controls": ["volume_set"],
    "collapsible_controls": True,
    "visibility": [{"condition": "state", "entity": "media_player.resty_media_player", "state": "playing"}],
    **full,
}
vacuums = {
    "type": "custom:auto-entities",
    "show_empty": False,
    "card": {"type": "vertical-stack"},
    "card_param": "cards",
    "filter": {
        "include": [{"domain": "vacuum", "options": {
            "type": "custom:mushroom-vacuum-card", "commands": ["start_pause", "return_home"], "icon_animation": True}}],
        "exclude": [{"state": s} for s in ("docked", "idle", "charging", "unavailable", "unknown")],
    },
    **full,
}
tesla_issues = ("{% set p = [] %}"
                "{% if is_state('binary_sensor.the_last_light_charging', 'on') %}{% set p = p + ['Charging ' ~ states('sensor.the_last_light_battery') ~ '%'] %}{% endif %}"
                "{% if is_state('lock.the_last_light_doors', 'unlocked') %}{% set p = p + ['Unlocked'] %}{% endif %}"
                "{% if is_state('cover.the_last_light_trunk', 'open') %}{% set p = p + ['Trunk open'] %}{% endif %}"
                "{% if is_state('cover.the_last_light_frunk', 'open') %}{% set p = p + ['Frunk open'] %}{% endif %}"
                "{% if is_state('cover.the_last_light_windows', 'open') %}{% set p = p + ['Windows open'] %}{% endif %}"
                "{{ p | join(' · ') }}")
tesla = {
    "type": "custom:mushroom-template-card",
    "primary": "The Last Light",
    "secondary": tesla_issues,
    "icon": "mdi:car-electric",
    "color": ("{{ 'red' if is_state('lock.the_last_light_doors', 'unlocked') or is_state('cover.the_last_light_trunk', 'open')"
              " or is_state('cover.the_last_light_frunk', 'open') or is_state('cover.the_last_light_windows', 'open') else 'green' }}"),
    "tap_action": {"action": "navigate", "navigation_path": f"/{NEW}/tesla"},
    "visibility": [{"condition": "or", "conditions": [
        {"condition": "state", "entity": "binary_sensor.the_last_light_charging", "state": "on"},
        {"condition": "and", "conditions": [
            {"condition": "state", "entity": "sensor.tesla_state", "state_not": "driving"},
            {"condition": "or", "conditions": [
                {"condition": "state", "entity": "lock.the_last_light_doors", "state": "unlocked"},
                {"condition": "state", "entity": "cover.the_last_light_trunk", "state": "open"},
                {"condition": "state", "entity": "cover.the_last_light_frunk", "state": "open"},
                {"condition": "state", "entity": "cover.the_last_light_windows", "state": "open"}]}]}]}],
    **half,
}
maint = {
    "type": "custom:mushroom-template-card",
    "primary": "Maintenance",
    "secondary": "{% set n = states('sensor.maintenance_attention') | int(0) %}{{ n }} need{{ 's' if n == 1 else '' }} attention",
    "icon": "mdi:wrench",
    "color": "red",
    "tap_action": {"action": "navigate", "navigation_path": f"/{NEW}/maintenance"},
    "visibility": [{"condition": "numeric_state", "entity": "sensor.maintenance_attention", "above": 0}],
    **half,
}
right_now = {"type": "grid", "cards": [heading("Right now", "mdi:clock-outline"), all_off, lights_on, laundry, tesla, maint,
                                       hatch, vacuums]}

# ---------- Favorites ----------
favorites = {"type": "grid", "cards": [
    heading("Favorites", "mdi:star"),
    light_tile("light.bedside_lamp_light", "Bedside Lamp"),
    light_tile("light.master_bedroom_lights", "Bedroom"),
    light_tile("light.bedroom_fan_light", "Fan Light"),
    light_tile("light.globe_lamp_light", "Holden's Lamp"),
    {"type": "tile", "entity": "media_player.resty_media_player", "name": "Holden's Hatch", **half},
    {"type": "tile", "entity": "vacuum.roborock_q7_max", "name": "Roborock",
     "features": [{"type": "vacuum-commands", "commands": ["start_pause", "return_home"]}], **half},
]}
daniel = {"type": "grid", "visibility": [{"condition": "user", "users": [DANIEL]}], "cards": [
    heading("Daniel's", "mdi:account"),
    light_tile("light.office_lights", "Office"),
    light_tile("light.kitchen_lights", "Kitchen"),
    light_tile("light.game_room_lights", "Game Room"),
    light_tile("light.garage_light_group", "Garage"),
    light_tile("light.holden_desk_lamp", "Holden's Desk Lamp"),
    {"type": "tile", "entity": "input_select.hvac_mode", "name": "Thermostat", "icon": "mdi:thermostat",
     "features": [{"type": "select-options"}], **half},
]}

# ---------- Rooms ----------
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
    room_cards.append(rewrite_nav(n, True))
rooms = {"type": "grid", "cards": room_cards}

# ---------- Room pop-ups ----------
popups = []
for r in ROOMS:
    v = copy.deepcopy(views[r])
    cards = v["cards"]
    title = cards[0].get("title") if cards and cards[0].get("type") == "custom:mushroom-title-card" else v.get("title")
    if cards and cards[0].get("type") == "custom:mushroom-title-card":
        cards = cards[1:]
    out = []
    for c in cards:
        if c.get("type") == "custom:mushroom-chips-card":
            c["chips"] = [x for x in c["chips"] if x.get("type") not in ("back", "menu")]
            if not c["chips"]:
                continue
        out.append(c)
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
popup_section = {"type": "grid", "cards": popups}

home = {
    "title": "Home",
    "path": "home",
    "icon": "mdi:home",
    "type": "sections",
    "max_columns": 3,
    "badges": [
        {"type": "entity", "entity": "person.daniel_lin", "show_name": True, "name": "Daniel", "show_entity_picture": True},
        {"type": "entity", "entity": "person.angie_ng", "show_name": True, "name": "Angie", "show_entity_picture": True},
        {"type": "entity", "entity": "sensor.downstairs_current_temperature", "name": "Inside", "show_name": False},
        {"type": "entity", "entity": "input_select.hvac_mode", "name": "Thermostat", "show_name": False, "icon": "mdi:thermostat"},
    ],
    "sections": [top, right_now, favorites, daniel, rooms, popup_section],
}

subs = []
for p in SUBVIEWS:
    v = rewrite_nav(copy.deepcopy(views[p]), False)
    v["subview"] = True
    v["back_path"] = f"/{NEW}/home"
    subs.append(v)
tesla_view = next(v for v in subs if v["path"] == "tesla")
for c in tesla_view["cards"]:
    if c.get("type") == "picture" and c.get("image") == "/local/ModelY_56.jpg":
        c["image"] = "/local/tesla/normal.jpeg"  # 90 KB instead of 16.8 MB; state-driven picture comes later

cfg = {"title": "Home", "views": [home] + subs}
json.dump({"type": "lovelace/config/save", "url_path": NEW, "config": cfg}, open(f"{SP}/home-save.json", "w"))
print("views:", [v["path"] for v in cfg["views"]], "| pop-ups:", [p["hash"] for p in popups],
      "| room tiles:", len(room_cards) - 1, "| bytes:", len(json.dumps(cfg)))

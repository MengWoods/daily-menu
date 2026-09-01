#!/usr/bin/env python3
"""Fetch today's lunch menu from configured restaurants and generate MENU.md + index.html.

Run manually:  python scripts/fetch_menus.py
Config:        config/restaurants.yaml
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from bs4 import BeautifulSoup
import markdown as md

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "restaurants.yaml"
TEMPLATE_PATH = ROOT / "templates" / "page_template.html"
MENU_MD_PATH = ROOT / "MENU.md"
INDEX_HTML_PATH = ROOT / "index.html"

TZ = ZoneInfo("Europe/Helsinki")
FI_WEEKDAYS = ["Maanantai", "Tiistai", "Keskiviikko", "Torstai", "Perjantai", "Lauantai", "Sunnuntai"]
EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; daily-menu-bot/1.0)"}
TIMEOUT = 20

# (regex pattern, emoji) — checked in order, first match per category wins, duplicates removed.
EMOJI_RULES = [
    (r"(?<!k)kana(?!nmun)|chicken|broiler|kalkkuna|turkey", "🐔"),
    (r"(?<!rans)kala(?!kkuna)|\bfish\b|lohi|lohta|salmon|trout|siika|\bcod\b|silli|herring|tonnikala|tuna", "🐟"),
    (r"katkarapu|shrimp|prawn|äyriäis|seafood", "🍤"),
    (r"keitto|\bsoup\b", "🍲"),
    (r"riisi|\brice\b", "🍚"),
    (r"kasvis|kasviks|vihannek|vegetable|veggie", "🥦"),
    (r"salaatti|\bsalad\b", "🥗"),
    (r"peruna|\bpotato\b", "🥔"),
    (r"porsas|possu|\bpork\b", "🐖"),
    (r"nauta|naudan|jauheliha|härkä(?!is)|\bbeef\b", "🐄"),
    (r"muna(?!koiso)|\begg\b", "🥚"),
    (r"pasta|spagetti|spaghetti", "🍝"),
    (r"juusto|\bcheese\b", "🧀"),
    (r"jälkiruoka|kakku|dessert|\bcake\b", "🍰"),
]


def add_emojis(line: str) -> str:
    """Append emoji hints for noticeable ingredients found in a menu line."""
    found = []
    lowered = line.lower()
    for pattern, emoji in EMOJI_RULES:
        if re.search(pattern, lowered) and emoji not in found:
            found.append(emoji)
    if not found:
        return line
    return f"{line} {' '.join(found)}"


def now_helsinki() -> datetime:
    return datetime.now(TZ)


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def fetch_json(url: str) -> dict:
    resp = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Per-site parsers. Each returns a list of plain dish-line strings for today,
# or raises an exception (caught by the caller) if the menu can't be found.
# ---------------------------------------------------------------------------

def parse_comfort_sello(cfg: dict, today: datetime) -> list[str]:
    html = fetch(cfg["url"])
    soup = BeautifulSoup(html, "html.parser")
    date_key = today.strftime("%Y%m%d")
    container = soup.select_one(f"div.date-container.date-{date_key}")
    if container is None:
        raise ValueError("today's menu block not found")
    desc = container.select_one(".description")
    price = container.select_one(".price")
    if desc is None:
        raise ValueError("menu description not found")
    text = desc.get_text(" ", strip=True)
    if price is not None:
        price_text = price.get_text(" ", strip=True).replace("\xa0", " ")
        text = f"{text} — {price_text}"
    return [text]


def parse_ravintola_factory(cfg: dict, today: datetime) -> list[str]:
    html = fetch(cfg["url"])
    soup = BeautifulSoup(html, "html.parser")
    fi_day = FI_WEEKDAYS[today.weekday()]
    date_str = f"{today.day}.{today.month}.{today.year}"
    target = f"{fi_day} {date_str}"
    heading = None
    for h3 in soup.find_all("h3"):
        if target.lower() in h3.get_text(" ", strip=True).lower():
            heading = h3
            break
    if heading is None:
        raise ValueError("today's day heading not found")
    node = heading.find_next_sibling()
    dishes_p = None
    while node is not None:
        if node.name == "h3":
            # decorative image-only heading between day title and dishes; keep scanning
            if node.get_text(strip=True):
                break  # reached next day's heading
            node = node.find_next_sibling()
            continue
        if node.name == "p" and node.get_text(strip=True):
            dishes_p = node
            break
        node = node.find_next_sibling()
    if dishes_p is None:
        raise ValueError("today's dish list not found")
    html_fragment = dishes_p.decode_contents()
    lines = [BeautifulSoup(part, "html.parser").get_text(" ", strip=True)
             for part in re.split(r"<br\s*/?>", html_fragment, flags=re.I)]
    return [line for line in lines if line]


def parse_kathmandu(cfg: dict, today: datetime) -> list[str]:
    html = fetch(cfg["url"])
    soup = BeautifulSoup(html, "html.parser")
    en_day = EN_WEEKDAYS[today.weekday()]
    title = None
    for candidate in soup.select(".elementor-tab-title, .elementor-accordion-title"):
        if candidate.get_text(strip=True).lower() == en_day.lower():
            title = candidate
            break
    if title is None:
        raise ValueError("today's tab not found")
    item = title.find_parent(class_="elementor-accordion-item") or title.parent
    content = item.select_one(".elementor-tab-content") if item else None
    if content is None:
        raise ValueError("today's menu content not found")
    if not content.find("strong") and "closed" in content.get_text(" ", strip=True).lower():
        return ["Closed today — see website for details"]
    lines = []
    for p in content.find_all("p"):
        strong = p.find("strong")
        name = strong.get_text(" ", strip=True) if strong else ""
        full_text = p.get_text(" ", strip=True)
        rest = full_text[len(name):].strip(" -–") if name else full_text
        line = f"{name} — {rest}" if name and rest else (name or rest)
        if line:
            lines.append(line)
    if not lines:
        raise ValueError("no dishes parsed")
    return lines


def parse_vermo(cfg: dict, today: datetime) -> list[str]:
    html = fetch(cfg["url"])
    soup = BeautifulSoup(html, "html.parser")
    fi_day = FI_WEEKDAYS[today.weekday()]
    date_str = f"{today.day}.{today.month}."
    target = f"{fi_day} {date_str}"
    container = soup.select_one(".element-text") or soup
    paragraphs = container.find_all("p")
    start = None
    for i, p in enumerate(paragraphs):
        text = p.get_text(" ", strip=True)
        if text.lower().startswith(target.lower()):
            start = i
            break
    if start is None:
        raise ValueError("today's day heading not found")
    lines = []
    for p in paragraphs[start + 1:]:
        text = p.get_text(" ", strip=True)
        if not text or set(text) == {"*"}:
            break
        lines.append(text)
    if not lines:
        raise ValueError("no dishes parsed")
    return lines


def parse_lounastaja(cfg: dict, today: datetime) -> list[str]:
    api_url = f"https://lounastaja.app/api/v1/widget/{cfg['api_key']}/{cfg['widget_id']}"
    data = fetch_json(api_url)
    days = data.get("data", {}).get("week", {}).get("days", [])
    date_str = today.strftime("%Y-%m-%d")
    day = next((d for d in days if d.get("dateString") == date_str), None)
    if day is None:
        raise ValueError("today's menu day not found")
    if day.get("isClosed"):
        return ["Closed today — see website for details"]
    lines = []
    for lunch in day.get("lunches", []):
        name = (lunch.get("title") or {}).get("fi") or (lunch.get("title") or {}).get("en")
        if not name:
            continue
        abbrevs = [a["abbreviation"]["fi"] for a in lunch.get("allergens", []) if a.get("abbreviation", {}).get("fi")]
        price = (lunch.get("normalPrice") or {}).get("price")
        unit = (lunch.get("normalPrice") or {}).get("unit", {}).get("fi", "")
        line = name
        if abbrevs:
            line += f" ({', '.join(abbrevs)})"
        if price:
            line += f" — {price} {unit}".rstrip()
        lines.append(line)
    if not lines:
        raise ValueError("no lunches listed for today")
    return lines


JAMIX_SKIP_OPTIONS = {"BREAKFAST", "DESSERT", "SIDES", "MY SALAD", "INFO!", "CLOSED"}


def parse_jamix(cfg: dict, today: datetime) -> list[str]:
    api_url = f"https://fi.jamix.cloud/apps/menuservice/rest/haku/menu/{cfg['jamix_customer']}/{cfg['jamix_kitchen']}"
    kitchens = fetch_json(api_url)
    date_num = int(today.strftime("%Y%m%d"))
    lines = []
    for kitchen in kitchens:
        for menu_type in kitchen.get("menuTypes", []):
            for menu in menu_type.get("menus", []):
                for day in menu.get("days", []):
                    if day.get("date") != date_num:
                        continue
                    for option in day.get("mealoptions", []):
                        opt_name = (option.get("name") or "").strip()
                        if opt_name.upper() in JAMIX_SKIP_OPTIONS:
                            continue
                        item_names = [mi.get("name", "").strip() for mi in option.get("menuItems", [])]
                        item_names = [n for n in item_names if n and n != "***"]
                        if not item_names:
                            continue
                        prefix = f"{opt_name.title()}: " if opt_name else ""
                        lines.append(f"{prefix}{', '.join(item_names)}")
    if not lines:
        raise ValueError("no lunch items found for today")
    return lines


def parse_manual(cfg: dict, today: datetime) -> list[str]:
    raise ValueError("manual entry — see website")


PARSERS = {
    "comfort_sello": parse_comfort_sello,
    "ravintola_factory": parse_ravintola_factory,
    "kathmandu": parse_kathmandu,
    "vermo": parse_vermo,
    "lounastaja": parse_lounastaja,
    "jamix": parse_jamix,
    "manual": parse_manual,
}


def load_config() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("restaurants", [])


def build_markdown(restaurants: list[dict], today: datetime) -> str:
    fi_day = FI_WEEKDAYS[today.weekday()]
    en_day = EN_WEEKDAYS[today.weekday()]
    date_str = today.strftime("%d.%m.%Y")
    updated = today.strftime("%Y-%m-%d %H:%M")

    lines = [
        "# 🍱 Daily Lunch Menu — Leppävaara, Espoo",
        "",
        f"**{fi_day} {date_str}** ({en_day})",
        "",
        f"_Last updated: {updated} (Europe/Helsinki)_",
        "",
    ]

    for cfg in restaurants:
        name = cfg.get("name", "Restaurant")
        emoji = cfg.get("emoji", "🍴")
        url = cfg.get("url", "#")
        parser_name = cfg.get("parser", "manual")
        parser = PARSERS.get(parser_name, parse_manual)

        lines.append(f"## {emoji} [{name}]({url})")
        lines.append("")
        try:
            dishes = parser(cfg, today)
            for dish in dishes:
                lines.append(f"- {add_emojis(dish)}")
        except Exception as exc:  # noqa: BLE001 - best effort per restaurant
            lines.append(f"- ⚠️ Menu unavailable right now — see the [website]({url}) directly. ({exc})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_html(markdown_text: str, today: datetime) -> str:
    body = md.markdown(markdown_text, extensions=["extra", "sane_lists"])
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    updated = today.strftime("%Y-%m-%d %H:%M")
    return template.replace("{{BODY}}", body).replace("{{UPDATED}}", updated)


def main() -> int:
    today = now_helsinki()
    if today.weekday() >= 5:
        print("Weekend — no lunch menus to fetch. Skipping.")
        return 0

    restaurants = load_config()
    markdown_text = build_markdown(restaurants, today)
    MENU_MD_PATH.write_text(markdown_text, encoding="utf-8")

    html = build_html(markdown_text, today)
    INDEX_HTML_PATH.write_text(html, encoding="utf-8")

    print(f"Wrote {MENU_MD_PATH} and {INDEX_HTML_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

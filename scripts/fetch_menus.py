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
    (r"(?<!k)kana(?!nmun)|chicken(?! leg)|broiler|kalkkuna|turkey", "🐔"),
    (r"koipi|drumstick|chicken leg", "🍕"),
    (r"(?<!rans)kala(?!kkuna)|\bfish\b|lohi|lohta|salmon|taimen|trout|siika|seiti\w*|\bcod\b|silli|herring|tonnikala|\btuna\b", "🐟"),
    (r"friteerattu katkarapu|fried shrimp|katkarapu|shrimp|prawn|äyriäis|seafood", "🍤"),
    (r"keitto|\bsoup\b", "🍲"),
    (r"riisi|\brice\b", "🍚"),
    (r"bataatti|sweet potato", "🍠"),
    (r"ranskalaiset|french fries|fries", "🍟"),
    (r"kasvis|kasviks|vihannek|vegetable|veggie", "🥬"),
    (r"salaatti|\bsalad\b", "🥗"),
    (r"peruna|(?<!sweet )\bpotato\b", "🥔"),
    (r"porkkan|\bcarrot\b", "🥕"),
    (r"pekoni|bacon", "🥓"),
    (r"porsa\w*|possu|kink[ku]|\bham\b|\bpork\b", "🐖"),
    (r"nauta|naudan|jauheliha|vasikan|\bveal\b|härkä(?!is|pap)|\bbeef\b", "🥩"),
    (r"lammas|lampaan|karitsa|\blamb\b", "🐑"),
    (r"lihapulla|meatball", "🧆"),
    (r"liha(?!ndon)|\bmeat\b", "🍖"),
    (r"makkara|sausage|nakki", "🌭"),
    (r"muna(?!koiso)|\begg\b", "🥚"),
    (r"pannukakku|lettu|letut|ohukainen|ohukaiset|pancake", "🥞"),
    (r"taco", "🌮"),
    (r"papu|pavut|härkis|bean|chickpea|chickpeas|chana", "🫘"),
    (r"avokado|avocado", "🥑"),
    (r"vesimeloni|watermelon", "🍉"),
    (r"mango", "🥭"),
    (r"oliivi|olive", "🫒"),
    (r"hampurilainen|burger", "🍔"),
    (r"pizza", "🍕"),
    (r"maissi|corn", "🌽"),
    (r"dumpling", "🥟"),
    (r"nuudeli|noodle", "🍜"),
    (r"pasta|spagetti|spaghetti", "🍝"),
    (r"leipä|sämpylä|patonki|\bbread\b|\broll\b", "🍞"),
    (r"juusto|\bcheese\b", "🧀"),
    (r"sieni|mushroom|kantarelli|herkkusieni", "🍄‍🟫"),
    (r"tomaatti(?!\w*kastik)(?!\w*\s+kastik)\w*|\btomato\b(?!\s+(?:\w+\s+){0,3}sauce\b)", "🍅"),
    (r"sitruuna|\blemon\b|\blime\b", "🍋"),
    (r"grilli|grillattu|paisti|\broast\w*|grilled", "🔥"),
    (r"suklaa|chocolate", "🍫"),
    (r"jäätelö|ice.?cream|sorbetti|parfait", "🍦"),
    (r"donitsi|munkki|doughnut|donut", "🍩"),
    (r"kuppikakku|cupcake", "🧁"),
    (r"keksi|cookie", "🍪"),
    (r"pretzel", "🥨"),
    (r"pähkin|nut", "🌰"),
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


# Matches the trailing run of emoji appended by add_emojis(), so the HTML build
# step can move it into its own right-aligned column.
_ALL_EMOJI = sorted({emoji for _, emoji in EMOJI_RULES}, key=len, reverse=True)
TRAILING_EMOJI_RE = re.compile(r"(?:\s*(?:" + "|".join(re.escape(e) for e in _ALL_EMOJI) + r"))+\s*$")


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
    heading = None
    for strong in container.find_all("strong"):
        if strong.get_text(" ", strip=True).lower().startswith(target.lower()):
            heading = strong
            break
    if heading is None:
        raise ValueError("today's day heading not found")
    lines = []
    start = heading.parent if heading.parent.name == "p" and heading.parent.get_text(" ", strip=True) == heading.get_text(" ", strip=True) else heading
    for sib in start.next_siblings:
        text = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
        if sib.name == "strong" or (sib.name == "p" and sib.find("strong")) or (text and set(text) == {"*"}):
            break
        if text:
            lines.append(text)
    if not lines:
        raise ValueError("no dishes parsed")
    return lines


def parse_lounastaja(cfg: dict, today: datetime) -> list[tuple[str | None, list[str]]]:
    """Returns dish lines grouped by the widget's own `lunchType` category
    (e.g. Buffetlounas, Keittolounas, Lautasannos), preserving first-seen order."""
    api_url = f"https://lounastaja.app/api/v1/widget/{cfg['api_key']}/{cfg['widget_id']}"
    data = fetch_json(api_url)
    days = data.get("data", {}).get("week", {}).get("days", [])
    date_str = today.strftime("%Y-%m-%d")
    day = next((d for d in days if d.get("dateString") == date_str), None)
    if day is None:
        raise ValueError("today's menu day not found")
    if day.get("isClosed"):
        return [(None, ["Closed today — see website for details"])]
    groups: list[tuple[str, list[str]]] = []
    group_index: dict[str, int] = {}
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
        category = lunch.get("lunchType") or "Lounas"
        if category not in group_index:
            group_index[category] = len(groups)
            groups.append((category, []))
        groups[group_index[category]][1].append(line)
    if not groups:
        raise ValueError("no lunches listed for today")
    return groups


def parse_jamix(cfg: dict, today: datetime) -> list[tuple[str | None, list[str]]]:
    """Returns dish lines grouped by the Jamix `menuTypeName` category
    (e.g. Salad and soup, Lounas, My Plate), preserving first-seen order.
    Options with no menu items (e.g. an empty breakfast slot) are skipped."""
    lang = cfg.get("jamix_language", "fi")
    api_url = (
        f"https://fi.jamix.cloud/apps/menuservice/rest/haku/menu/"
        f"{cfg['jamix_customer']}/{cfg['jamix_kitchen']}?lang={lang}"
    )
    kitchens = fetch_json(api_url)
    date_num = int(today.strftime("%Y%m%d"))
    groups: list[tuple[str, list[str]]] = []
    group_index: dict[str, int] = {}
    for kitchen in kitchens:
        for menu_type in kitchen.get("menuTypes", []):
            category = (menu_type.get("menuTypeName") or "").strip() or "Lounas"
            for menu in menu_type.get("menus", []):
                for day in menu.get("days", []):
                    if day.get("date") != date_num:
                        continue
                    for option in day.get("mealoptions", []):
                        opt_name = (option.get("name") or "").strip()
                        item_names = []
                        for mi in option.get("menuItems", []):
                            name = (mi.get("name") or "").strip()
                            if not name or name == "***":
                                continue
                            diets = (mi.get("diets") or "").strip()
                            item_names.append(f"{name} {diets}" if diets else name)
                        if not item_names:
                            continue
                        prefix = f"{opt_name.title()}: " if opt_name else ""
                        line = f"{prefix}{', '.join(item_names)}"
                        if category not in group_index:
                            group_index[category] = len(groups)
                            groups.append((category, []))
                        groups[group_index[category]][1].append(line)
    if not groups:
        raise ValueError("no lunch items found for today")
    return groups


def _add_group(groups: list, index: dict, category: str, line: str) -> None:
    if category not in index:
        index[category] = len(groups)
        groups.append((category, []))
    groups[index[category]][1].append(line)


def parse_compass(cfg: dict, today: datetime) -> list[tuple[str | None, list[str]]]:
    """Compass Group (Food & Co, Tastory, ...) JSON menu feed, keyed by cost number.
    Returns dishes grouped by SetMenu name (e.g. Lounas buffet) with its price."""
    lang = cfg.get("compass_language", "fi")
    api_url = f"https://www.compass-group.fi/menuapi/feed/json?costNumber={cfg['compass_cost_number']}&language={lang}"
    data = fetch_json(api_url)
    date_str = today.strftime("%Y-%m-%d")
    day = next((d for d in data.get("MenusForDays", []) if (d.get("Date") or "").startswith(date_str)), None)
    if day is None:
        raise ValueError("today's menu day not found")
    groups: list[tuple[str | None, list[str]]] = []
    for set_menu in day.get("SetMenus", []):
        dishes = [" ".join(c.split()) for c in set_menu.get("Components", []) if c and c.strip()]
        if not dishes:
            continue
        name = (set_menu.get("Name") or "").strip()
        price = (set_menu.get("Price") or "").strip()
        label = " — ".join(p for p in (name, price) if p) or None
        groups.append((label, dishes))
    if not groups:
        raise ValueError("no lunches listed for today")
    if len(groups) == 1 and groups[0][0] is None:
        return groups[0][1]
    return groups


def parse_iss(cfg: dict, today: datetime) -> list[tuple[str | None, list[str]]]:
    """ravintolapalvelut.iss.fi restaurant pages: one div.day_menu_container per
    date (data-date=YYYY-MM-DD), with category headings + prices followed by dish
    rows. A category with an empty heading continues the previous one.
    Breakfast categories (Aamiainen) are skipped."""
    html = fetch(cfg["url"])
    soup = BeautifulSoup(html, "html.parser")
    day = soup.select_one(f'div.day_menu_container[data-date="{today.strftime("%Y-%m-%d")}"]')
    if day is None:
        raise ValueError("today's menu block not found")
    groups: list[tuple[str, list[str]]] = []
    index: dict[str, int] = {}
    category = "Lounas"
    for node in day.find_all("div", recursive=False):
        classes = node.get("class", [])
        if "name_price_container" in classes:
            heading = node.find("h3")
            title = heading.get_text(" ", strip=True) if heading else ""
            if title:
                price_el = node.select_one(".price")
                price = price_el.get_text("", strip=True).replace("\xa0", " ") if price_el else ""
                price = re.sub(r"(\d)€", r"\1 €", price)
                category = f"{title} — {price}" if price else title
        elif "menu_item_container" in classes:
            if category.lower().startswith("aamiainen"):
                continue
            name_el = node.select_one(".menu_item_name")
            line = name_el.get_text(" ", strip=True) if name_el else ""
            if line:
                _add_group(groups, index, category, line)
    if not groups:
        raise ValueError("no dishes parsed")
    return groups


def parse_mezza(cfg: dict, today: datetime) -> list[tuple[str | None, list[str]]]:
    """mezza.fi/lounas: one article.mlm-front-day per day, with a date span
    (e.g. "11.11.") and sections (soup, mains, sides, ...) each with an h4."""
    html = fetch(cfg["url"])
    soup = BeautifulSoup(html, "html.parser")
    date_str = f"{today.day}.{today.month}."
    day = None
    for article in soup.select("article.mlm-front-day"):
        date_el = article.select_one(".mlm-date")
        if date_el and date_el.get_text(strip=True) == date_str:
            day = article
            break
    if day is None:
        raise ValueError("today's menu not published on the website")
    groups: list[tuple[str | None, list[str]]] = []
    for section in day.select("section.mlm-front-section"):
        heading = section.find("h4")
        title = heading.get_text(" ", strip=True).capitalize() if heading else None
        items = [li.get_text(" ", strip=True) for li in section.find_all("li")]
        if not items:
            items = [p.get_text(" ", strip=True) for p in section.find_all("p")]
        items = [i for i in items if i]
        if items:
            groups.append((title, items))
    if not groups:
        raise ValueError("no dishes parsed")
    return groups


def parse_manual(cfg: dict, today: datetime) -> list[str]:
    raise ValueError("manual entry — see website")


PARSERS = {
    "comfort_sello": parse_comfort_sello,
    "ravintola_factory": parse_ravintola_factory,
    "kathmandu": parse_kathmandu,
    "vermo": parse_vermo,
    "lounastaja": parse_lounastaja,
    "jamix": parse_jamix,
    "compass": parse_compass,
    "iss": parse_iss,
    "mezza": parse_mezza,
    "manual": parse_manual,
}


def load_config() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("restaurants", [])


def normalize_groups(result: list) -> list[tuple[str | None, list[str]]]:
    """Parsers may return a flat list[str] (no categories) or an already
    grouped list[(category, [dishes])]; make it uniformly the latter."""
    if result and isinstance(result[0], tuple):
        return result
    return [(None, result)]


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

    for idx, cfg in enumerate(restaurants, start=1):
        name = cfg.get("name", "Restaurant")
        emoji = cfg.get("emoji", "🍴")
        url = cfg.get("url", "#")
        parser_name = cfg.get("parser", "manual")
        parser = PARSERS.get(parser_name, parse_manual)

        lines.append(f"## {idx}. {emoji} [{name}]({url})")
        lines.append("")
        try:
            groups = normalize_groups(parser(cfg, today))
            for category, dish_lines in groups:
                if category:
                    lines.append(f"- {category}")
                    for dish in dish_lines:
                        lines.append(f"    - {add_emojis(dish)}")
                else:
                    for dish in dish_lines:
                        lines.append(f"- {add_emojis(dish)}")
        except Exception as exc:  # noqa: BLE001 - best effort per restaurant
            lines.append(f"- ⚠️ Menu unavailable right now — see the [website]({url}) directly. ({exc})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def restructure_html(body_html: str) -> str:
    """Group each restaurant's heading+list into a card, split the trailing
    emoji hints of each dish line into their own right-aligned column, and
    turn a nested <ul> inside an <li> into a category label + sub-list."""
    soup = BeautifulSoup(body_html, "html.parser")

    # Dish rows (no nested list): split off trailing emoji tags.
    for li in soup.find_all("li"):
        if li.find("ul") is not None:
            continue
        inner = li.decode_contents()
        match = TRAILING_EMOJI_RE.search(inner)
        if match and match.group(0).strip():
            dish_html, tags_text = inner[: match.start()].rstrip(), match.group(0).strip()
        else:
            dish_html, tags_text = inner.strip(), ""
        li.clear()
        dish_span = soup.new_tag("span", **{"class": "dish"})
        for node in list(BeautifulSoup(dish_html, "html.parser").contents):
            dish_span.append(node)
        li.append(dish_span)
        if tags_text:
            tags_span = soup.new_tag("span", **{"class": "tags"})
            tags_span.string = tags_text
            li.append(tags_span)

    # Category rows: <li>Label<ul>...</ul></li> -> labeled group with sub-list.
    for li in soup.find_all("li"):
        nested_ul = li.find("ul")
        if nested_ul is None:
            continue
        nested_ul.extract()
        label_text = li.get_text(" ", strip=True)
        li.clear()
        li["class"] = li.get("class", []) + ["category-item"]
        label_span = soup.new_tag("span", **{"class": "category-label"})
        label_span.string = label_text
        li.append(label_span)
        nested_ul["class"] = nested_ul.get("class", []) + ["sub-list"]
        li.append(nested_ul)

    top_level_tags = soup.find_all(recursive=False)
    grid = soup.new_tag("div", **{"class": "restaurants-grid"})
    i = 0
    while i < len(top_level_tags):
        tag = top_level_tags[i]
        if tag.name == "h2":
            card = soup.new_tag("section", **{"class": "restaurant-card"})
            card.append(tag.extract())
            if i + 1 < len(top_level_tags) and top_level_tags[i + 1].name == "ul":
                card.append(top_level_tags[i + 1].extract())
                i += 1
            grid.append(card)
        i += 1
    soup.append(grid)
    return str(soup)


def build_html(markdown_text: str, today: datetime) -> str:
    body = md.markdown(markdown_text, extensions=["extra", "sane_lists"])
    body = restructure_html(body)
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

# daily-menu

Daily lunch menu near the office (Leppävaara, Espoo) — auto-updated every work day morning.

👉 **View today's menu:** open [`index.html`](index.html) (enable GitHub Pages on this repo to
get a live URL), or read the plain-text [`MENU.md`](MENU.md).

## How it works

- [`config/restaurants.yaml`](config/restaurants.yaml) lists the restaurants and their menu
  page links — **edit this file to add/remove restaurants or change links**.
- [`scripts/fetch_menus.py`](scripts/fetch_menus.py) fetches each restaurant's lunch menu for
  today, tags noticeable ingredients (chicken 🐔, fish 🐟, soup 🍲, rice 🍚, vegetables 🥦, etc.)
  with an emoji, and writes [`MENU.md`](MENU.md) and [`index.html`](index.html).
- A [GitHub Actions workflow](.github/workflows/update-menu.yml) runs the script automatically
  on weekday mornings (05:30 UTC) and commits the updated files, so the page is fresh before
  lunchtime. You can also trigger it manually from the *Actions* tab.

## Run it yourself

```bash
pip install -r scripts/requirements.txt
python scripts/fetch_menus.py
```

If a restaurant's website changes its layout, its parser in `scripts/fetch_menus.py` may need
updating — you can set `parser: manual` in the config to just show a link while that happens.

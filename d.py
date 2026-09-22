import json
import re
import time
from urllib.parse import urlparse, unquote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://divar.ir/s/iran/motorcycles"
OUTPUT = "divar_motorcycles.json"
TARGET = 100
SCROLL_WAIT_MS = 100
MAX_IDLE_ROUNDS = 25
PAGE_TIMEOUT = 600

DIGIT_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_digits(value):
    return str(value or "").translate(DIGIT_TRANS)


def clean_text(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def parse_number(value):
    value = clean_text(value)
    if not value:
        return None
    normalized = normalize_digits(value)
    digits = re.sub(r"[^0-9]", "", normalized)
    return int(digits) if digits else None


def parse_card(card):
    href = card.get("href") or ""
    href = unquote(href)
    parts = [p for p in urlparse(href).path.split("/") if p]
    if not parts:
        return None
    token = parts[-1]
    if not token:
        return None

    title = clean_text(card.get("title"))
    descriptions = [clean_text(x) for x in (card.get("descriptions") or [])]
    descriptions = [x for x in descriptions if x]

    price = None
    mileage = None
    extra = []

    for text in descriptions:
        normalized = normalize_digits(text)

        if re.search(r"کیلومتر", normalized, re.I):
            if mileage is None:
                mileage = parse_number(normalized)
            continue

        if re.search(r"(?:تومان|توافق|تماس)", normalized, re.I):
            if price is None:
                if "توافق" in normalized or "تماس" in normalized:
                    price = text
                else:
                    price = parse_number(normalized)
            continue

        extra.append(text)

    location = clean_text(card.get("location"))
    city = None
    if location:
        m = re.search(r"\sدر\s(.+)$", location)
        city = clean_text(m.group(1)) if m else location

    image = clean_text(card.get("image"))

    return {
        "token": token,
        "url": f"https://divar.ir/v/{token}",
        "title": title,
        "description": " | ".join(extra) if extra else None,
        "district": None,
        "city": city,
        "price": price,
        "mileage": mileage,
        "image": image,
        "raw_web_info": {
            "title": title,
            "city_persian": city,
            "location_text": location,
        },
    }


def main():
    items = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1200},
            locale="fa-IR",
        )

        print("OPEN:", URL)
        page.goto(URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        try:
            page.wait_for_selector("article.kt-post-card", timeout=30000)
        except PlaywrightTimeoutError:
            page.screenshot(path="divar_no_cards.png", full_page=False)
            raise RuntimeError("هیچ kt-post-card در صفحه پیدا نشد.")

        idle = 0
        last_count = 0
        scroll_round = 0

        while len(items) < TARGET and idle < MAX_IDLE_ROUNDS:
            cards = page.locator("article.kt-post-card").evaluate_all(
                """
                els => els.map(card => ({
                    href: card.querySelector('a.kt-post-card__action')?.getAttribute('href') || '',
                    title: card.querySelector('.kt-post-card__title')?.textContent || '',
                    descriptions: Array.from(card.querySelectorAll('.kt-post-card__description')).map(x => x.textContent || ''),
                    location: card.querySelector('.kt-post-card__bottom-description')?.getAttribute('title')
                              || card.querySelector('.kt-post-card__bottom-description')?.textContent || '',
                    image: card.querySelector('img.kt-image-block__image')?.getAttribute('src')
                           || card.querySelector('img.kt-image-block__image')?.getAttribute('data-src') || ''
                }))
                """
            )

            for card in cards:
                item = parse_card(card)
                if item and item["token"] not in items:
                    items[item["token"]] = item

            current = len(items)
            scroll_round += 1

            if current == last_count:
                idle += 1
            else:
                idle = 0
                last_count = current

            print(f"SCROLL {scroll_round} | cards={len(cards)} | unique={current} | idle={idle}")

            if current >= TARGET:
                break

            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(SCROLL_WAIT_MS)

        browser.close()

    result = list(items.values())[:TARGET]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with_price = sum(1 for x in result if x.get("price") is not None)
    with_mileage = sum(1 for x in result if x.get("mileage") is not None)

    print("=" * 70)
    print("FINISHED")
    print("COUNT:", len(result))
    print("WITH PRICE:", with_price)
    print("WITH MILEAGE:", with_mileage)
    print("OUTPUT:", OUTPUT)
    print("=" * 70)


if __name__ == "__main__":
    main()

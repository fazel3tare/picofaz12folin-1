import json
import time
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


LIST_API_URL = "https://api.divar.ir/v8/postlist/w/search"
DETAIL_API_URL = "https://api.divar.ir/v8/posts-v2/web/{token}"

TARGET = 10000
PAUSE = 0.6
TIMEOUT = 20
# سرعت بالاتر — در صورت 429/بلاک، DETAIL_WORKERS را کم کن
DETAIL_WORKERS = 12
DETAIL_TIMEOUT = 1
DETAIL_PAUSE = 0.0
# اگر True باشد فقط برای آگهی‌هایی که قیمت/عکس ندارند detail می‌زند (خیلی سریع‌تر، بدون کارکرد)
SKIP_DETAIL_IF_PRICE_OK = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://divar.ir",
    "Referer": "https://divar.ir/",
    "X-Render-Type": "CSR",
    "X-Standard-Divar-Error": "true",
}

session = requests.Session()
session.headers.update(HEADERS)


def make_body(pagination_data=None):
    body = {
        "city_ids": [],
        "search_data": {
            "form_data": {
                "data": {
                    "category": {
                        "str": {
                            "value": "motorcycles"
                        }
                    }
                }
            }
        }
    }

    if pagination_data:
        body["pagination_data"] = pagination_data

    return body


def get_page(pagination_data=None, retries=3):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.post(
                LIST_API_URL,
                json=make_body(pagination_data),
                timeout=TIMEOUT,
            )
            print("LIST STATUS:", r.status_code)
            print("LIST SIZE:", len(r.content))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            wait = min(30, 2 ** attempt)
            print(f"LIST ERROR (try {attempt}/{retries}): {repr(e)} — sleep {wait}s")
            time.sleep(wait)
    raise last_err


def get_detail(token):
    url = DETAIL_API_URL.format(token=token)
    r = session.get(url, timeout=DETAIL_TIMEOUT)
    r.raise_for_status()
    return r.json()


def strip_html_text(value):
    if value is None:
        return None
    import html as _html
    value = re.sub(r"<[^>]+>", " ", str(value), flags=re.S)
    value = _html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def fetch_post_html(token):
    url = f"https://divar.ir/v/{token}"
    r = requests.get(
        url,
        headers={
            "User-Agent": HEADERS.get("User-Agent", "Mozilla/5.0"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://divar.ir/",
        },
        timeout=DETAIL_TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def normalize_digits(value):
    if value is None:
        return ""

    text = str(value)
    trans = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )
    return text.translate(trans)


def clean_text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    value = str(value).strip()
    return value or None


def walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk_dicts(value)


def find_direct_value(obj, keys):
    wanted = {str(k).lower() for k in keys}
    for d in walk_dicts(obj):
        for k, v in d.items():
            if str(k).lower() in wanted:
                if isinstance(v, (str, int, float)) and str(v).strip() != "":
                    return v
                if isinstance(v, dict):
                    nested = find_direct_value(
                        v,
                        {
                            "value",
                            "text",
                            "display_value",
                            "str",
                            "amount",
                            "price",
                            "mileage",
                            "km",
                            "kilometers",
                        },
                    )
                    if nested is not None:
                        return nested
    return None


def find_labeled_value(obj, labels):
    labels = [str(x).lower() for x in labels]

    for d in walk_dicts(obj):
        for k, v in d.items():
            ks = str(k).lower()
            if any(label in ks for label in labels):
                if isinstance(v, (str, int, float)) and str(v).strip():
                    return v

        label_texts = []
        for k in ("label", "name", "title", "key", "caption", "text"):
            v = d.get(k)
            if isinstance(v, str):
                label_texts.append(v.lower())

        if label_texts and any(any(label in t for label in labels) for t in label_texts):
            for k in ("value", "str", "value_text", "text", "content", "display_value"):
                v = d.get(k)
                if isinstance(v, (str, int, float)) and str(v).strip():
                    return v

    return None


def first_url(value):
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("http://") or s.startswith("https://"):
            return s
        return None

    if isinstance(value, dict):
        for key in ("url", "image_url", "src", "href"):
            u = first_url(value.get(key))
            if u:
                return u
        for v in value.values():
            u = first_url(v)
            if u:
                return u

    if isinstance(value, list):
        for v in value:
            u = first_url(v)
            if u:
                return u

    return None


def extract_image(obj):
    preferred_keys = {
        "image",
        "image_url",
        "thumbnail",
        "thumbnail_url",
        "photo",
        "photo_url",
        "cover",
        "cover_url",
        "media_url",
        "original_url",
    }

    for d in walk_dicts(obj):
        for k, v in d.items():
            ks = str(k).lower()
            if ks in preferred_keys or any(
                x in ks for x in ("image", "photo", "thumbnail", "cover")
            ):
                u = first_url(v)
                if u:
                    return u

    return None


def extract_price(obj):
    if isinstance(obj, str):
        value = obj
    else:
        value = find_direct_value(
            obj,
            {
                "price",
                "price_value",
                "current_price",
                "total_price",
                "amount",
                "price_text",
                "price_value_text",
                "middle_description_text",
            },
        )
    if isinstance(value, dict):
        value = find_direct_value(
            value, {"value", "text", "price", "amount", "display_value"}
        )
    if value is None and not isinstance(obj, str):
        value = find_labeled_value(obj, ("قیمت", "price", "amount"))

    value = clean_text(value)
    if not value:
        return None

    normalized = normalize_digits(value)
    if "توافق" in normalized or "تماس" in normalized:
        return value

    # فقط عددی که کنار تومان است (نه همه رقم‌های متن طولانی)
    m = re.search(r"([\d,\.]+)\s*تومان", normalized)
    if m:
        digits = re.sub(r"[^0-9]", "", m.group(1))
        if digits:
            try:
                return int(digits)
            except Exception:
                pass

    digits = re.sub(r"[^0-9]", "", normalized)
    if digits and len(digits) <= 15:
        try:
            return int(digits)
        except Exception:
            pass

    return value


def extract_mileage(obj):
    """فقط عدد کارکرد را برمی‌گرداند؛ مقادیر غیرمنطقی را رد می‌کند."""
    MAX_REASONABLE_KM = 2_000_000  # برای موتور/خودرو کافی است

    if isinstance(obj, str):
        value = obj
    else:
        value = find_direct_value(
            obj,
            {
                "mileage",
                "kilometer",
                "kilometers",
                "km",
                "distance",
                "mileage_text",
                "kilometer_text",
                "odometer",
                "کارکرد",
            },
        )
    if isinstance(value, dict):
        value = find_direct_value(
            value,
            {
                "value",
                "text",
                "mileage",
                "kilometers",
                "km",
                "distance",
                "display_value",
            },
        )
    if value is None and not isinstance(obj, str):
        value = find_labeled_value(
            obj, ("کارکرد", "کیلومتر", "mileage", "kilometer", "odometer")
        )

    value = clean_text(value)
    if not value:
        return None

    normalized = normalize_digits(value)

    # اولویت: عدد بلافاصله قبل از کیلومتر / km
    m = re.search(r"([\d,\.]+)\s*(?:کیلومتر|km)\b", normalized, flags=re.I)
    if m:
        digits = re.sub(r"[^0-9]", "", m.group(1))
        if digits:
            try:
                n = int(digits)
                if 0 <= n <= MAX_REASONABLE_KM:
                    return n
            except Exception:
                pass

    m = re.search(r"(?:کارکرد|کیلومتر)\s*[:：]?\s*([\d,\.]+)", normalized)
    if m:
        digits = re.sub(r"[^0-9]", "", m.group(1))
        if digits:
            try:
                n = int(digits)
                if 0 <= n <= MAX_REASONABLE_KM:
                    return n
            except Exception:
                pass

    # اگر کل رشته فقط عدد (با جداکننده) است
    if re.fullmatch(r"[\d,\.\s]+", normalized.strip()):
        digits = re.sub(r"[^0-9]", "", normalized)
        if digits and len(digits) <= 7:
            try:
                n = int(digits)
                if 0 <= n <= MAX_REASONABLE_KM:
                    return n
            except Exception:
                pass

    return None


def extract_from_embedded_json(raw_html):
    """Try structured fields from JSON blobs inside the page."""
    result = {}
    if not raw_html:
        return result

    candidates = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
        raw_html,
        flags=re.I | re.S,
    ):
        candidates.append(m.group(1))
    for m in re.finditer(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        raw_html,
        flags=re.I | re.S,
    ):
        candidates.append(m.group(1))
    for m in re.finditer(
        r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*</script>",
        raw_html,
        flags=re.I | re.S,
    ):
        candidates.append(m.group(1))

    for raw in candidates:
        try:
            obj = json.loads(raw.strip())
        except Exception:
            continue
        if result.get("price") is None:
            p = extract_price(obj)
            if p is not None:
                result["price"] = p
        if result.get("mileage") is None:
            m = extract_mileage(obj)
            if m is not None:
                result["mileage"] = m
        if not result.get("title"):
            t = find_direct_value(obj, {"title", "post_title"})
            if t:
                result["title"] = clean_text(t)
        if not result.get("description"):
            d = find_direct_value(
                obj, {"description", "middle_description", "top_description"}
            )
            if d:
                result["description"] = clean_text(d)
        if not result.get("district"):
            d = find_direct_value(
                obj, {"district_persian", "district", "district_name"}
            )
            if d:
                result["district"] = clean_text(d)
        if not result.get("city"):
            c = find_direct_value(obj, {"city_persian", "city", "city_name"})
            if c:
                result["city"] = clean_text(c)
        if not result.get("image"):
            img = extract_image(obj)
            if img:
                result["image"] = img
        if result.get("price") is not None and result.get("mileage") is not None:
            break
    return result


def extract_post_card_from_html(raw_html):
    """Extract from list-card HTML OR full detail page HTML."""
    if not raw_html:
        return {}

    result = {}

    # --- 1) list-card structure (kt-post-card) ---
    title_match = re.search(
        r'<h2[^>]*class=["\'][^"\']*kt-post-card__title[^"\']*["\'][^>]*>(.*?)</h2>',
        raw_html,
        flags=re.I | re.S,
    )
    if title_match:
        result["title"] = strip_html_text(title_match.group(1))

    descriptions = re.findall(
        r'<div[^>]*class=["\'][^"\']*kt-post-card__description[^"\']*["\'][^>]*>(.*?)</div>',
        raw_html,
        flags=re.I | re.S,
    )
    descriptions = [strip_html_text(x) for x in descriptions]
    descriptions = [x for x in descriptions if x]

    for text in descriptions:
        normalized = normalize_digits(text)
        if "تومان" in normalized:
            result["price"] = extract_price(text)
        elif "کیلومتر" in normalized or re.search(r"\bkm\b", normalized, re.I):
            result["mileage"] = extract_mileage(text)

    if result.get("price") is None:
        for text in descriptions:
            parsed = extract_price(text)
            if parsed is not None and (
                "تومان" in normalize_digits(text)
                or re.fullmatch(r"[0-9۰-۹٠-٩,، .]+", text or "")
            ):
                result["price"] = parsed
                break

    if result.get("mileage") is None:
        for text in descriptions:
            if "کیلومتر" in normalize_digits(text) or re.search(
                r"\bkm\b", normalize_digits(text), re.I
            ):
                result["mileage"] = extract_mileage(text)
                break

    extra = []
    for text in descriptions:
        n = normalize_digits(text)
        if "تومان" in n or "کیلومتر" in n or re.search(r"\bkm\b", n, re.I):
            continue
        extra.append(text)
    if extra:
        result["description"] = " | ".join(extra)

    bottom_match = re.search(
        r'<span[^>]*class=["\'][^"\']*kt-post-card__bottom-description[^"\']*["\'][^>]*>(.*?)</span>',
        raw_html,
        flags=re.I | re.S,
    )
    if bottom_match:
        bottom = strip_html_text(bottom_match.group(1))
        result["bottom_text"] = bottom
        if bottom:
            m = re.search(r"\bدر\s+(.+?)\s*$", bottom)
            if m:
                result["district"] = m.group(1).strip()

    image_match = re.search(
        r'<img[^>]*class=["\'][^"\']*kt-image-block__image[^"\']*["\'][^>]*src=["\']([^"\']+)',
        raw_html,
        flags=re.I | re.S,
    )
    if image_match:
        result["image"] = image_match.group(1).strip()

    # --- 2) embedded JSON on detail page ---
    embedded = extract_from_embedded_json(raw_html)
    for k, v in embedded.items():
        if result.get(k) is None and v is not None:
            result[k] = v

    # --- 3) detail-page text patterns (class-independent) ---
    text_only = re.sub(r"<script[^>]*>.*?</script>", " ", raw_html, flags=re.I | re.S)
    text_only = re.sub(r"<style[^>]*>.*?</style>", " ", text_only, flags=re.I | re.S)
    text_only = strip_html_text(text_only) or ""
    text_norm = normalize_digits(text_only)

    if result.get("price") is None:
        m = re.search(r"(توافقی|تماس|[\d,\.]+)\s*تومان", text_norm)
        if m:
            result["price"] = extract_price(m.group(0))
        else:
            m = re.search(
                r"قیمت\s*[:：]?\s*(توافقی|تماس|[\d,\.]+(?:\s*تومان)?)",
                text_norm,
            )
            if m:
                result["price"] = extract_price(m.group(1))

    if result.get("mileage") is None:
        m = re.search(r"([\d,\.]+)\s*(?:کیلومتر|km)\b", text_norm, flags=re.I)
        if m:
            result["mileage"] = extract_mileage(m.group(0))
        else:
            m = re.search(r"(?:کارکرد|کیلومتر)\s*[:：]?\s*([\d,\.]+)", text_norm)
            if m:
                result["mileage"] = extract_mileage(m.group(1))

    if not result.get("title"):
        m = re.search(
            r'<h1[^>]*class=["\'][^"\']*kt-page-title[^"\']*["\'][^>]*>(.*?)</h1>',
            raw_html,
            flags=re.I | re.S,
        )
        if not m:
            m = re.search(r"<h1[^>]*>(.*?)</h1>", raw_html, flags=re.I | re.S)
        if m:
            result["title"] = strip_html_text(m.group(1))

    if not result.get("district"):
        loc = re.search(
            r'<[^>]*class=["\'][^"\']*(?:location|district|breadcrumb)[^"\']*["\'][^>]*>(.*?)</',
            raw_html,
            flags=re.I | re.S,
        )
        if loc:
            loc_text = strip_html_text(loc.group(1))
            if loc_text:
                result["district"] = loc_text

    if not result.get("image"):
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            raw_html,
            flags=re.I,
        )
        if m:
            result["image"] = m.group(1).strip()
        else:
            m = re.search(
                r'(https://s\d+\.divarcdn\.com/static/photo/[^"\']+\.(?:webp|jpg|jpeg|png))',
                raw_html,
                flags=re.I,
            )
            if m:
                result["image"] = m.group(1)

    return result


def enrich_from_html(post):
    try:
        raw_html = fetch_post_html(post["token"])
        card = extract_post_card_from_html(raw_html)
        if not card:
            return post, False, "html_card_not_found"

        if not post.get("title") and card.get("title"):
            post["title"] = card["title"]
        if post.get("price") is None and card.get("price") is not None:
            post["price"] = card["price"]
        if post.get("mileage") is None and card.get("mileage") is not None:
            post["mileage"] = card["mileage"]
        if not post.get("description") and card.get("description"):
            post["description"] = card["description"]
        if not post.get("district") and card.get("district"):
            post["district"] = card["district"]
        if not post.get("city") and card.get("city"):
            post["city"] = card["city"]
        if not post.get("image") and card.get("image"):
            post["image"] = card["image"]

        return post, True, None
    except Exception as e:
        return post, False, repr(e)


def extract_posts(data):
    posts = []
    widgets = data.get("list_widgets", [])

    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        if widget.get("widget_type") != "POST_ROW":
            continue

        widget_data = widget.get("data", {})
        if not isinstance(widget_data, dict):
            continue

        action = widget_data.get("action", {})
        if not isinstance(action, dict):
            continue

        payload = action.get("payload", {})
        if not isinstance(payload, dict):
            continue

        token = payload.get("token")
        if not token:
            continue

        web_info = payload.get("web_info", {})
        if not isinstance(web_info, dict):
            web_info = {}

        # فیلدهای مستقیم کارت لیست (مهم‌ترین منبع قیمت)
        middle_text = widget_data.get("middle_description_text")
        top_text = widget_data.get("top_description_text")
        bottom_text = widget_data.get("bottom_description_text")
        list_title = widget_data.get("title")
        list_image = widget_data.get("image_url")

        price = None
        if middle_text:
            price = extract_price(str(middle_text))
        if price is None and top_text and "تومان" in normalize_digits(str(top_text)):
            price = extract_price(str(top_text))

        mileage = None
        for candidate in (top_text, middle_text, bottom_text):
            if candidate and (
                "کیلومتر" in normalize_digits(str(candidate))
                or re.search(r"\bkm\b", str(candidate), re.I)
            ):
                mileage = extract_mileage(str(candidate))
                if mileage is not None:
                    break

        district = web_info.get("district_persian")
        if not district and bottom_text:
            m = re.search(r"\bدر\s+(.+?)\s*$", str(bottom_text))
            if m:
                district = m.group(1).strip()

        post = {
            "token": token,
            "url": f"https://divar.ir/v/{token}",
            "title": list_title or web_info.get("title"),
            "description": None,  # توضیح کامل از detail/html
            "district": district,
            "city": web_info.get("city_persian"),
            "price": price,
            "mileage": mileage,
            "image": list_image or extract_image(widget_data),
            "raw_web_info": web_info,
        }

        posts.append(post)

    return posts


def enrich_one(post):
    detail_ok = False
    html_ok = False

    has_price = post.get("price") is not None
    has_image = bool(post.get("image"))
    has_mileage = post.get("mileage") is not None

    # کامل از لیست → هیچ درخواست اضافه
    if has_price and has_image and has_mileage:
        return post, True, None

    # حالت فوق‌سریع: قیمت و عکس از لیست کافی است، detail نزن
    if SKIP_DETAIL_IF_PRICE_OK and has_price and has_image:
        return post, True, None

    if DETAIL_PAUSE > 0:
        time.sleep(DETAIL_PAUSE)

    # فقط Detail API (یک بار، بدون retry سنگین)
    try:
        detail = get_detail(post["token"])
        detail_ok = True

        if post.get("price") is None:
            post["price"] = extract_price(detail)
        if not post.get("image"):
            post["image"] = extract_image(detail)
        if post.get("mileage") is None:
            post["mileage"] = extract_mileage(detail)

        if not post.get("title"):
            post["title"] = find_direct_value(detail, {"title", "post_title"})
        if not post.get("city"):
            post["city"] = find_direct_value(
                detail, {"city_persian", "city", "city_name"}
            )
        if not post.get("district"):
            post["district"] = find_direct_value(
                detail, {"district_persian", "district", "district_name"}
            )
        if not post.get("description"):
            post["description"] = find_direct_value(
                detail, {"description", "middle_description", "top_description"}
            )
    except Exception:
        pass

    # HTML فقط وقتی قیمت هنوز خالی است (گرانی شبکه)
    if post.get("price") is None:
        post, html_ok, _ = enrich_from_html(post)

    ok = (
        detail_ok
        or html_ok
        or post.get("price") is not None
        or bool(post.get("image"))
        or bool(post.get("title"))
    )

    return post, ok, None


def enrich_posts(posts):
    total = len(posts)
    if not total:
        return posts

    print("\nشروع تکمیل قیمت/عکس/کارکرد:", total)

    completed = 0
    failed = 0
    debug_saved = False

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
        futures = [executor.submit(enrich_one, post) for post in posts]

        for i, future in enumerate(as_completed(futures), 1):
            post, ok, error = future.result()

            if ok:
                completed += 1
            else:
                failed += 1

            if not debug_saved and (
                post.get("price") is None
                or not post.get("image")
                or post.get("mileage") is None
            ):
                try:
                    with open("divar_detail_debug.json", "w", encoding="utf-8") as f:
                        json.dump(
                            {"post": post, "error": error},
                            f,
                            ensure_ascii=False,
                            indent=2,
                        )
                    debug_saved = True
                except Exception:
                    pass

            if i % 20 == 0 or i == total:
                print(f"DETAIL {i}/{total} | OK={completed} | FAIL={failed}")

    print(f"تکمیل جزئیات: OK={completed} | FAIL={failed}")
    return posts


def get_pagination(data):
    pagination = data.get("pagination", {})
    if not isinstance(pagination, dict):
        return None, False

    has_next = bool(pagination.get("has_next_page"))
    next_cursor = pagination.get("data")
    return next_cursor, has_next


def save_debug(data):
    with open("divar_debug_response.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_progress(posts, path="divar_motorcycles_partial.json"):
    """ذخیره میانی تا در صورت گیر کردن داده از دست نرود."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[SAVE] {len(posts)} آگهی → {path}")
    except Exception as e:
        print("SAVE ERROR:", repr(e))


def main():
    all_posts = {}
    cursor = None
    request_number = 1
    stagnant_rounds = 0  # چند صفحه پشت‌سرهم بدون آگهی جدید
    MAX_STAGNANT = 5

    # ادامه از فایل partial اگر وجود داشت
    partial_path = "divar_motorcycles_partial.json"
    try:
        with open(partial_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if isinstance(existing, list) and existing:
            for p in existing:
                tok = p.get("token")
                if tok:
                    all_posts[tok] = p
            print(f"ادامه از {partial_path}: {len(all_posts)} آگهی قبلی")
    except FileNotFoundError:
        pass
    except Exception as e:
        print("خواندن partial ناموفق:", repr(e))

    while len(all_posts) < TARGET:
        print("\n" + "=" * 70)
        print(f"REQUEST {request_number} | TOTAL {len(all_posts)}")
        print("=" * 70)

        try:
            data = get_page(cursor)
        except Exception as e:
            print("REQUEST ERROR (final):", repr(e))
            save_progress(list(all_posts.values()))
            break

        if request_number == 1:
            with open("divar_first_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        posts = extract_posts(data)
        print("آگهی این درخواست:", len(posts))

        if not posts:
            print("\nهیچ POST_ROW پیدا نشد.")
            save_debug(data)
            save_progress(list(all_posts.values()))
            break

        new_count = 0
        for post in posts:
            token = post["token"]
            if token not in all_posts:
                all_posts[token] = post
                new_count += 1

        print("جدید:", new_count)
        print("مجموع یکتا:", len(all_posts))

        if new_count == 0:
            stagnant_rounds += 1
            print(f"بدون آگهی جدید ({stagnant_rounds}/{MAX_STAGNANT})")
            if stagnant_rounds >= MAX_STAGNANT:
                print("\nچند صفحه پشت‌سرهم آگهی جدید نبود — توقف.")
                save_debug(data)
                save_progress(list(all_posts.values()))
                break
        else:
            stagnant_rounds = 0

        # هر ۲۰ درخواست یکبار ذخیره میانی
        if request_number % 20 == 0:
            save_progress(list(all_posts.values()))

        next_cursor, has_next = get_pagination(data)
        print("HAS NEXT:", has_next)

        if not has_next:
            print("\nدیوار اعلام کرده صفحه بعدی وجود ندارد.")
            break

        if not next_cursor:
            print("\npagination.data وجود ندارد.")
            save_debug(data)
            save_progress(list(all_posts.values()))
            break

        # مقایسه پایدار cursor (dict ممکن است ترتیب کلید عوض شود)
        if cursor is not None:
            try:
                same = json.dumps(next_cursor, sort_keys=True, ensure_ascii=False) == json.dumps(
                    cursor, sort_keys=True, ensure_ascii=False
                )
            except Exception:
                same = next_cursor == cursor
            if same:
                print("\nCURSOR تکراری شد.")
                save_progress(list(all_posts.values()))
                break

        cursor = next_cursor
        request_number += 1
        time.sleep(PAUSE)

    result = list(all_posts.values())[:TARGET]
    save_progress(result, "divar_motorcycles_partial.json")

    result = enrich_posts(result)

    with open("divar_motorcycles.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with_price = sum(1 for p in result if p.get("price") is not None)
    with_mileage = sum(1 for p in result if p.get("mileage") is not None)
    print("\n" + "=" * 70)
    print("FINISHED")
    print("=" * 70)
    print("تعداد نهایی:", len(result))
    print(f"با قیمت: {with_price}/{len(result)} | با کارکرد: {with_mileage}/{len(result)}")
    print("فایل:", "divar_motorcycles.json")


if __name__ == "__main__":
    main()

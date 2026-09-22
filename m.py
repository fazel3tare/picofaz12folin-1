import json
import time
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


LIST_API_URL = "https://api.divar.ir/v8/postlist/w/search"
DETAIL_API_URL = "https://api.divar.ir/v8/posts-v2/web/{token}"

TARGET = 10000
PAUSE = 1.0
TIMEOUT = 30
DETAIL_WORKERS = 8
DETAIL_TIMEOUT = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0",
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


def get_page(pagination_data=None):
    r = session.post(
        LIST_API_URL,
        json=make_body(pagination_data),
        timeout=TIMEOUT,
    )
    print("LIST STATUS:", r.status_code)
    print("LIST SIZE:", len(r.content))
    r.raise_for_status()
    return r.json()


def get_detail(token):
    url = DETAIL_API_URL.format(token=token)
    r = session.get(url, timeout=DETAIL_TIMEOUT)
    r.raise_for_status()
    return r.json()


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
                    nested = find_direct_value(v, {"value", "text", "display_value", "str", "amount", "price", "mileage", "km", "kilometers"})
                    if nested is not None:
                        return nested
    return None


def find_labeled_value(obj, labels):
    labels = [str(x).lower() for x in labels]

    for d in walk_dicts(obj):
        # مستقیم روی keyها
        for k, v in d.items():
            ks = str(k).lower()
            if any(label in ks for label in labels):
                if isinstance(v, (str, int, float)) and str(v).strip():
                    return v

        # الگوی label/name/title/text + value/str/text/value_text
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
        "image", "image_url", "thumbnail", "thumbnail_url", "photo",
        "photo_url", "cover", "cover_url", "media_url", "original_url"
    }

    for d in walk_dicts(obj):
        for k, v in d.items():
            ks = str(k).lower()
            if ks in preferred_keys or any(x in ks for x in ("image", "photo", "thumbnail", "cover")):
                u = first_url(v)
                if u:
                    return u

    return None


def extract_price(obj):
    value = find_direct_value(obj, {
        "price", "price_value", "current_price", "total_price", "amount",
        "price_text", "price_value_text"
    })
    if isinstance(value, dict):
        value = find_direct_value(value, {"value", "text", "price", "amount", "display_value"})
    if value is None:
        value = find_labeled_value(obj, ("قیمت", "price", "amount"))

    value = clean_text(value)
    if not value:
        return None

    normalized = normalize_digits(value)
    if "توافق" in normalized or "تماس" in normalized:
        return value

    # عدد خالص با جداکننده‌ها
    digits = re.sub(r"[^0-9]", "", normalized)
    if digits:
        try:
            return int(digits)
        except Exception:
            pass

    return value


def extract_mileage(obj):
    value = find_direct_value(obj, {
        "mileage", "kilometer", "kilometers", "km", "distance",
        "mileage_text", "kilometer_text", "odometer", "کارکرد"
    })
    if isinstance(value, dict):
        value = find_direct_value(value, {"value", "text", "mileage", "kilometers", "km", "distance", "display_value"})
    if value is None:
        value = find_labeled_value(obj, ("کارکرد", "کیلومتر", "mileage", "kilometer", "odometer"))

    value = clean_text(value)
    if not value:
        return None

    normalized = normalize_digits(value)
    digits = re.sub(r"[^0-9]", "", normalized)
    if digits:
        try:
            return int(digits)
        except Exception:
            pass

    return value


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

        # فعلاً از خود کارت هر چیزی که موجود است می‌گیریم.
        combined = {
            "widget": widget,
            "payload": payload,
            "web_info": web_info,
        }

        post = {
            "token": token,
            "url": f"https://divar.ir/v/{token}",
            "title": web_info.get("title") or find_direct_value(combined, {"title"}),
            "description": (
                web_info.get("description")
                or web_info.get("middle_description")
                or web_info.get("top_description")
            ),
            "district": web_info.get("district_persian"),
            "city": web_info.get("city_persian"),
            "price": extract_price(combined),
            "mileage": extract_mileage(combined),
            "image": extract_image(combined),
            "raw_web_info": web_info,
        }

        posts.append(post)

    return posts


def enrich_one(post):
    # فقط زمانی Detail بزن که حداقل یکی از سه فیلد مهم ناقص باشد.
    if post.get("price") is not None and post.get("image") and post.get("mileage") is not None:
        return post, True, None

    try:
        detail = get_detail(post["token"])

        price = post.get("price")
        image = post.get("image")
        mileage = post.get("mileage")

        if price is None:
            price = extract_price(detail)
        if not image:
            image = extract_image(detail)
        if mileage is None:
            mileage = extract_mileage(detail)

        title = post.get("title")
        city = post.get("city")
        district = post.get("district")
        description = post.get("description")

        if not title:
            title = find_direct_value(detail, {"title", "post_title"})
        if not city:
            city = find_direct_value(detail, {"city_persian", "city", "city_name"})
        if not district:
            district = find_direct_value(detail, {"district_persian", "district", "district_name"})
        if not description:
            description = find_direct_value(detail, {"description", "middle_description", "top_description"})

        post.update({
            "title": title,
            "city": city,
            "district": district,
            "description": description,
            "price": price,
            "mileage": mileage,
            "image": image,
        })

        return post, True, None

    except Exception as e:
        return post, False, repr(e)


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

            # اولین پاسخ ناقص/خطادار را برای بررسی نگه دار
            if not debug_saved and (not post.get("price") or not post.get("image") or post.get("mileage") is None):
                try:
                    with open("divar_detail_debug.json", "w", encoding="utf-8") as f:
                        json.dump({"post": post, "error": error}, f, ensure_ascii=False, indent=2)
                    debug_saved = True
                except Exception:
                    pass

            if i % 50 == 0 or i == total:
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


def main():
    all_posts = {}
    cursor = None
    request_number = 1

    while len(all_posts) < TARGET:
        print("\n" + "=" * 70)
        print(f"REQUEST {request_number} | TOTAL {len(all_posts)}")
        print("=" * 70)

        try:
            data = get_page(cursor)
        except Exception as e:
            print("REQUEST ERROR:", repr(e))
            break

        if request_number == 1:
            with open("divar_first_response.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        posts = extract_posts(data)
        print("آگهی این درخواست:", len(posts))

        if not posts:
            print("\nهیچ POST_ROW پیدا نشد.")
            save_debug(data)
            break

        new_count = 0
        for post in posts:
            token = post["token"]
            if token not in all_posts:
                all_posts[token] = post
                new_count += 1

        print("جدید:", new_count)
        print("مجموع یکتا:", len(all_posts))

        next_cursor, has_next = get_pagination(data)
        print("HAS NEXT:", has_next)

        if not has_next:
            print("\nدیوار اعلام کرده صفحه بعدی وجود ندارد.")
            break

        if not next_cursor:
            print("\npagination.data وجود ندارد.")
            save_debug(data)
            break

        if next_cursor == cursor:
            print("\nCURSOR تکراری شد.")
            break

        cursor = next_cursor
        request_number += 1
        time.sleep(PAUSE)

    result = list(all_posts.values())[:TARGET]

    # تکمیل فیلدهای ناقص با detail API
    result = enrich_posts(result)

    with open("divar_motorcycles.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("FINISHED")
    print("=" * 70)
    print("تعداد نهایی:", len(result))
    print("فایل:", "divar_motorcycles.json")


if __name__ == "__main__":
    main()

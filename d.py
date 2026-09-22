import json
import time
import requests


API_URL = "https://api.divar.ir/v8/postlist/w/search"

TARGET = 10000
PAUSE = 1.0
TIMEOUT = 30

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
                            "value": "light"
                        }
                    }
                }
            }
        }
    }

    # فقط از درخواست دوم به بعد
    if pagination_data:
        body["pagination_data"] = pagination_data

    return body


def get_page(pagination_data=None):

    body = make_body(pagination_data)

    r = session.post(
        API_URL,
        json=body,
        timeout=TIMEOUT
    )

    print("STATUS:", r.status_code)
    print("SIZE:", len(r.content))

    r.raise_for_status()

    return r.json()


def extract_posts(data):

    posts = []

    # ساختار واقعی API فعلی
    widgets = data.get("list_widgets", [])

    for widget in widgets:

        if not isinstance(widget, dict):
            continue

        # فقط کارت آگهی
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

        post = {
            "token": token,
            "url": f"https://divar.ir/v/{token}",

            "title": web_info.get("title"),

            "description": (
                web_info.get("description")
                or web_info.get("middle_description")
                or web_info.get("top_description")
            ),

            "district": web_info.get(
                "district_persian"
            ),

            "city": web_info.get(
                "city_persian"
            ),

            "image": (
                web_info.get("image")
                or web_info.get("image_url")
            ),

            "raw_web_info": web_info,
        }

        posts.append(post)

    return posts


def get_pagination(data):

    pagination = data.get(
        "pagination",
        {}
    )

    if not isinstance(pagination, dict):
        return None, False

    has_next = bool(
        pagination.get("has_next_page")
    )

    next_cursor = pagination.get(
        "data"
    )

    return next_cursor, has_next


def save_debug(data):

    with open(
        "divar_debug_response.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def main():

    all_posts = {}
    cursor = None

    request_number = 1

    while len(all_posts) < TARGET:

        print("\n" + "=" * 70)
        print(
            f"REQUEST {request_number} | "
            f"TOTAL {len(all_posts)}"
        )
        print("=" * 70)

        try:

            data = get_page(cursor)

        except Exception as e:

            print("REQUEST ERROR:", repr(e))
            break

        # اگر ساختار عوض شد، پاسخ کامل را نگه دار
        if request_number == 1:

            with open(
                "divar_first_response.json",
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    data,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

        posts = extract_posts(data)

        print(
            "آگهی این درخواست:",
            len(posts)
        )

        # اطلاعات تشخیصی
        if not posts:

            print("\nهیچ POST_ROW پیدا نشد.")

            if isinstance(data, dict):

                print(
                    "ROOT KEYS:",
                    list(data.keys())
                )

                widgets = data.get(
                    "list_widgets",
                    []
                )

                print(
                    "LIST_WIDGETS:",
                    len(widgets)
                    if isinstance(widgets, list)
                    else type(widgets)
                )

                if isinstance(widgets, list):

                    print(
                        "WIDGET TYPES:"
                    )

                    types = {}

                    for w in widgets:

                        if isinstance(w, dict):

                            t = w.get(
                                "widget_type",
                                "UNKNOWN"
                            )

                            types[t] = (
                                types.get(t, 0) + 1
                            )

                    print(types)

            save_debug(data)

            break

        new_count = 0

        for post in posts:

            token = post["token"]

            if token not in all_posts:

                all_posts[token] = post
                new_count += 1

        print(
            "جدید:",
            new_count
        )

        print(
            "مجموع یکتا:",
            len(all_posts)
        )

        # cursor واقعی Infinite Scroll
        next_cursor, has_next = get_pagination(
            data
        )

        print(
            "HAS NEXT:",
            has_next
        )

        if not has_next:

            print(
                "\nدیوار اعلام کرده صفحه بعدی وجود ندارد."
            )

            break

        if not next_cursor:

            print(
                "\npagination.data وجود ندارد."
            )

            save_debug(data)

            break

        # جلوگیری از loop
        if next_cursor == cursor:

            print(
                "\nCURSOR تکراری شد."
            )

            break

        cursor = next_cursor

        request_number += 1

        time.sleep(PAUSE)

    result = list(
        all_posts.values()
    )

    # محدود کردن به تعداد هدف
    result = result[:TARGET]

    with open(
        "divar_cars.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("\n" + "=" * 70)
    print("FINISHED")
    print("=" * 70)

    print(
        "تعداد نهایی:",
        len(result)
    )

    print(
        "فایل:",
        "d.json"
    )


if __name__ == "__main__":
    main()

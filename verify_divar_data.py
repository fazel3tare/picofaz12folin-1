import json
import sys
from pathlib import Path

FILES = [
    (Path("divar_cars.json"), "cars"),
    (Path("divar_motorcycles.json"), "motorcycles"),
]

all_tokens = {}
failed = False

for path, label in FILES:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path}: top-level JSON is not a list")

    token_seen = set()
    url_seen = set()
    token_dupes = 0
    url_dupes = 0

    for item in data:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "").strip()
        url = str(item.get("url") or "").strip()
        if token:
            if token in token_seen:
                token_dupes += 1
            else:
                token_seen.add(token)
                all_tokens.setdefault(token, []).append(label)
        if url:
            if url in url_seen:
                url_dupes += 1
            else:
                url_seen.add(url)

    print(f"{label}: total={len(data)} unique_tokens={len(token_seen)} token_duplicates={token_dupes} url_duplicates={url_dupes}")
    if token_dupes:
        failed = True

cross = sum(1 for labels in all_tokens.values() if len(set(labels)) > 1)
print(f"cross_file_token_overlaps={cross}")
if cross:
    failed = True

if failed:
    sys.exit(1)

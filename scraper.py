import os
import re
import time
import requests
from bs4 import BeautifulSoup
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

GALLERIES = [
    {"id": "thesingularity", "name": "특갤"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://gall.dcinside.com/",
}


def fetch_gallery(gal_id: str, gal_name: str):
    url = f"https://gall.dcinside.com/mgallery/board/lists/?id={gal_id}&page=1"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("tr.ub-content.us-post")
    items = []

    for row in rows:
        num_el = row.select_one("td.gall_num")
        if not num_el:
            continue
        num_text = num_el.get_text(strip=True)
        if not num_text.isdigit():
            continue

        post_no = int(num_text)
        subject_el = row.select_one("td.gall_subject")
        subject = subject_el.get_text(strip=True) if subject_el else ""

        title_el = row.select_one("td.gall_tit a")
        if not title_el:
            continue
        reply_box = title_el.select_one("span.reply_num")
        if reply_box:
            reply_box.decompose()
        title = re.sub(r"\s+", " ", title_el.get_text(strip=True))

        writer_el = row.select_one("td.gall_writer")
        author = writer_el.get("data-nick", "") if writer_el else ""

        rec_el = row.select_one("td.gall_recommend")
        view_el = row.select_one("td.gall_count")
        recommend = int(rec_el.get_text(strip=True)) if rec_el and rec_el.get_text(strip=True).isdigit() else 0
        views = int(view_el.get_text(strip=True)) if view_el and view_el.get_text(strip=True).isdigit() else 0

        mobile_url = f"https://m.dcinside.com/board/{gal_id}/{post_no}"

        items.append({
            "id": f"{gal_id}_{post_no}",
            "gallery_id": gal_id,
            "gallery_name": gal_name,
            "post_no": post_no,
            "subject": subject,
            "title": title,
            "url": mobile_url,
            "author": author,
            "recommend": recommend,
            "views": views,
        })

    return items


def main():
    all_posts = []
    for g in GALLERIES:
        try:
            posts = fetch_gallery(g["id"], g["name"])
            all_posts.extend(posts)
            print(f"[{g['name']}] {len(posts)}건 수집 완료")
        except Exception as e:
            print(f"[{g['name']}] 수집 오류: {e}")
        time.sleep(1.5)

    if not all_posts:
        return

    existing = supabase.table("dc_posts").select("id,feedback").in_(
        "id", [p["id"] for p in all_posts]
    ).execute()
    fb_map = {row["id"]: row["feedback"] for row in (existing.data or [])}

    for p in all_posts:
        p["feedback"] = fb_map.get(p["id"], 0)

    supabase.table("dc_posts").upsert(all_posts, on_conflict="id").execute()
    print(f"총 {len(all_posts)}건 DB 반영 완료")


if __name__ == "__main__":
    main()

import os
import re
import time
from datetime import datetime, timezone, timedelta
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


def fetch_gallery_page(gal_id: str, gal_name: str, page: int):
    url = f"https://gall.dcinside.com/mgallery/board/lists/?id={gal_id}&page={page}"
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


def fetch_gallery(gal_id: str, gal_name: str, pages=(1, 2)):
    merged = {}
    for p in pages:
        for item in fetch_gallery_page(gal_id, gal_name, p):
            merged[item["id"]] = item
        time.sleep(1.0)
    return list(merged.values())


def cleanup_old_posts():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    res = (
        supabase.table("dc_posts")
        .delete()
        .eq("feedback", 0)
        .lt("created_at", cutoff)
        .execute()
    )
    deleted_cnt = len(res.data) if res.data else 0
    print(f"1일 경과 미평가 글 {deleted_cnt}건 자동 정리 완료")


def main():
    all_posts = []
    for g in GALLERIES:
        try:
            posts = fetch_gallery(g["id"], g["name"], pages=(1, 2))
            all_posts.extend(posts)
            print(f"[{g['name']}] {len(posts)}건 수집 완료 (1~2페이지)")
        except Exception as e:
            print(f"[{g['name']}] 수집 오류: {e}")
        time.sleep(1.5)

    if all_posts:
        ids = [p["id"] for p in all_posts]
        existing = supabase.table("dc_posts").select("id,feedback,created_at").in_("id", ids).execute()
        existing_map = {row["id"]: row for row in (existing.data or [])}

        for p in all_posts:
            prev = existing_map.get(p["id"])
            if prev:
                p["feedback"] = prev.get("feedback", 0)
                if prev.get("created_at"):
                    p["created_at"] = prev["created_at"]
            else:
                p["feedback"] = 0

        supabase.table("dc_posts").upsert(all_posts, on_conflict="id").execute()
        print(f"총 {len(all_posts)}건 DB 반영 완료")

    cleanup_old_posts()


if __name__ == "__main__":
    main()

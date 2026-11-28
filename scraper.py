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

KST = timezone(timedelta(hours=9))

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

DELETED_MARKERS = (
    "해당 게시물은 삭제되었습니다",
    "존재하지 않는 게시물",
    "정상적인 접근이 아닙니다",
)


def parse_dc_date(date_el):
    now_utc = datetime.now(timezone.utc)
    if not date_el:
        return now_utc.isoformat()

    title_attr = (date_el.get("title") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt_kst = datetime.strptime(title_attr, fmt).replace(tzinfo=KST)
            return dt_kst.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass

    txt = date_el.get_text(strip=True)
    m = re.match(r"^(\d{1,2}):(\d{2})$", txt)
    if m:
        now_kst = datetime.now(KST)
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            dt_kst = now_kst.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt_kst > now_kst + timedelta(minutes=5):
                dt_kst -= timedelta(days=1)
            return dt_kst.astimezone(timezone.utc).isoformat()

    return now_utc.isoformat()


def fetch_post_content(gal_id: str, post_no: int):
    """정상 본문이면 문자열 반환, 삭제된 글이면 None 반환"""
    view_url = f"https://gall.dcinside.com/mgallery/board/view/?id={gal_id}&no={post_no}"
    try:
        resp = requests.get(view_url, headers=HEADERS, timeout=12)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            return ""
        if any(marker in resp.text for marker in DELETED_MARKERS):
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        body_el = soup.select_one("div.write_div")
        if not body_el:
            return ""
        for tag in body_el.select("script, style, .og-div"):
            tag.decompose()
        text = re.sub(r"\s+", " ", body_el.get_text(" ", strip=True))
        return text[:1500]
    except Exception as e:
        print(f"[{gal_id}_{post_no}] 본문 수집 실패: {e}")
        return ""


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

        date_el = row.select_one("td.gall_date")
        created_at = parse_dc_date(date_el)

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
            "created_at": created_at,
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


def remove_vanished_posts(gal_id: str, alive_posts: list):
    """1~2페이지 최신 글 번호 범위 내에 있는데 목록에서 사라진 삭제글 정리"""
    if not alive_posts:
        return
    alive_nos = {p["post_no"] for p in alive_posts}
    min_no = min(alive_nos)

    db_recent = (
        supabase.table("dc_posts")
        .select("id,post_no")
        .eq("gallery_id", gal_id)
        .gte("post_no", min_no)
        .execute()
    )
    vanished_ids = [
        row["id"] for row in (db_recent.data or [])
        if row["post_no"] not in alive_nos
    ]
    if vanished_ids:
        supabase.table("dc_posts").delete().in_("id", vanished_ids).execute()
        print(f"[{gal_id}] 삭제된 게시물 {len(vanished_ids)}건 DB 제거 완료")


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
    for g in GALLERIES:
        try:
            posts = fetch_gallery(g["id"], g["name"], pages=(1, 2))
            print(f"[{g['name']}] {len(posts)}건 목록 수집 완료")
        except Exception as e:
            print(f"[{g['name']}] 목록 수집 오류: {e}")
            continue

        if not posts:
            continue

        ids = [p["id"] for p in posts]
        existing = (
            supabase.table("dc_posts")
            .select("id,feedback,created_at,content")
            .in_("id", ids)
            .execute()
        )
        existing_map = {row["id"]: row for row in (existing.data or [])}

        valid_posts = []
        deleted_during_fetch = []
        new_content_count = 0

        for p in posts:
            prev = existing_map.get(p["id"])
            p["feedback"] = prev.get("feedback", 0) if prev else 0

            if prev and prev.get("content"):
                p["content"] = prev["content"]
                valid_posts.append(p)
            else:
                content = fetch_post_content(p["gallery_id"], p["post_no"])
                if content is None:
                    deleted_during_fetch.append(p["id"])
                else:
                    p["content"] = content
                    valid_posts.append(p)
                new_content_count += 1
                time.sleep(0.4)

        if valid_posts:
            supabase.table("dc_posts").upsert(valid_posts, on_conflict="id").execute()
        if deleted_during_fetch:
            supabase.table("dc_posts").delete().in_("id", deleted_during_fetch).execute()

        remove_vanished_posts(g["id"], valid_posts)
        print(f"총 {len(valid_posts)}건 반영 완료 (신규 본문: {new_content_count}건, 삭제 제외: {len(deleted_during_fetch)}건)")
        time.sleep(1.0)

    cleanup_old_posts()


if __name__ == "__main__":
    main()

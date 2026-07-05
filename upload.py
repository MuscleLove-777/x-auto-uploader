# -*- coding: utf-8 -*-
"""Post one Google Drive video or image to X.

The workflow is intentionally conservative:
- one live post per scheduled run
- fail visibly when X credentials are stale
- skip unsafe filenames/tags
- use optional account insights generated from recent X posts
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

requests = None
OAuth1 = None

JST = timezone(timedelta(hours=9))

VIDEO_EXTENSIONS = {".mp4", ".mov"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
# X media specs: (mime type, media_category, max bytes)
MEDIA_SPECS = {
    ".mp4": ("video/mp4", "tweet_video", 512 * 1024 * 1024),
    ".mov": ("video/quicktime", "tweet_video", 512 * 1024 * 1024),
    ".jpg": ("image/jpeg", "tweet_image", 5 * 1024 * 1024),
    ".jpeg": ("image/jpeg", "tweet_image", 5 * 1024 * 1024),
    ".png": ("image/png", "tweet_image", 5 * 1024 * 1024),
    ".webp": ("image/webp", "tweet_image", 5 * 1024 * 1024),
    ".gif": ("image/gif", "tweet_gif", 15 * 1024 * 1024),
}
UPLOADED_LOG = "uploaded.json"
INSIGHTS_FILE = "x_account_insights.json"
FAILURE_LOG = "failure_log.jsonl"

MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
TWEET_URL = "https://api.x.com/2/tweets"
MAX_TWEET_CHARS = 280
AUTH_ERROR_EXIT_CODE = 20

DEFAULT_POST_HOURS_JST = "12"
DEFAULT_POST_WINDOW_MINUTES = 30

CONTENT_TAG_MAP = {
    "training": ["workout", "training", "gym", "fitness"],
    "workout": ["workout", "training", "gym", "fitness"],
    "pullups": ["pullups", "backworkout", "calisthenics"],
    "posing": ["posing", "bodybuilding", "physique"],
    "flex": ["flex", "muscle", "bodybuilding"],
    "muscle": ["muscle", "muscular", "fitness"],
    "bicep": ["biceps", "arms", "muscle"],
    "abs": ["abs", "sixpack", "core"],
    "leg": ["legs", "quads", "legday"],
    "back": ["back", "lats", "backday"],
    "squat": ["squat", "legs", "legday"],
    "deadlift": ["deadlift", "powerlifting"],
    "bench": ["benchpress", "chest"],
    "competition": ["competition", "bodybuilding", "contest"],
}

BASE_TAGS = [
    "musclegirl",
    "muscularwoman",
    "femalemuscle",
    "strongwomen",
    "fbb",
    "fitnessmotivation",
    "gymgirl",
    "musclebeauty",
    "workoutmotivation",
    "calisthenics",
]

UNSAFE_TAG_WORDS = {
    "nsfw",
    "adult",
    "sexy",
    "nude",
    "porn",
    "erotic",
}
OFF_TOPIC_CATEGORY_WORDS = {
    "golf",
    "grok",
    "video",
    "imagine",
    "default",
    "download",
    "downloads",
}
NG_WORDS = {"atsuro", "atsurou"}
HASHLIKE_RE = re.compile(r"^[a-f0-9]{6,}$", re.IGNORECASE)

# フォルダ名(英語)を日本語の見出しに変換して「俺の言葉」に馴染ませる
CATEGORY_JP = {
    "training": "トレーニング",
    "workout": "ワークアウト",
    "posing": "ポージング",
    "flex": "フレックス",
    "muscle": "筋肉",
    "abs": "腹筋",
    "back": "背中",
    "leg": "脚トレ",
    "legs": "脚トレ",
    "bicep": "上腕二頭筋",
    "pullups": "懸垂",
    "squat": "スクワット",
    "deadlift": "デッドリフト",
    "bench": "ベンチプレス",
    "competition": "大会",
}

# 俺っぽい一言（content_poolのcaption_templatesと同じ口調）。
# pool取得成功時はpool側が優先されるが、失敗時もこのフォールバックで口調を維持する。
TWEET_TEMPLATES = [
    "見てくれ、この圧。今日の{category}、格が違う。",
    "これは仕上がりエグい。{category}のキレよ。",
    "筋肉女子、やっぱ最高。今日は{category}。",
    "バキバキ。でも美しい。{category}の完成形。",
    "今日の一枚、強い。テーマは{category}。",
    "継続は力なり。今日も{category}で積み上げ💪",
    "この{category}、刺さる人には刺さるはず。",
    "しょーがないなぁ、特別に見せてあげる。今日は{category}。",
    "え、これ合法なの？ってレベルの{category}。",
    "Strong is beautiful💪 {category}の美学。",
]

# 流入計測の生命線: 投稿には必ず計測可能なリンクを1本入れる
HUB_LINK = "https://musclelove-777.github.io/?utm_source=x&utm_medium=autopost"
GAMES_LINK = "https://musclelove-games.vercel.app/?utm_source=x&utm_medium=autopost"

CTA_LINES = [
    f"More daily updates: {HUB_LINK}",
    f"Full gallery & sites: {HUB_LINK}",
    f"Play free muscle girl mini games: {GAMES_LINK}",
    f"60+ free browser games, no signup: {GAMES_LINK}",
    "One strong post a day. See you tomorrow.",
    "More training inspiration in the profile flow.",
]


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def require_x_http():
    global requests, OAuth1
    if requests is None or OAuth1 is None:
        import requests as requests_module
        from requests_oauthlib import OAuth1 as oauth1_class

        requests = requests_module
        OAuth1 = oauth1_class
    return requests, OAuth1


def get_oauth() -> Any:
    _requests, oauth1_class = require_x_http()
    return oauth1_class(
        os.environ.get("X_CONSUMER_KEY", ""),
        os.environ.get("X_CONSUMER_SECRET", ""),
        os.environ.get("X_ACCESS_TOKEN", ""),
        os.environ.get("X_ACCESS_TOKEN_SECRET", ""),
    )


def write_failure(stage: str, message: str, **extra: object) -> None:
    entry = {
        "timestamp_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "stage": stage,
        "message": message,
    }
    if extra:
        entry["extra"] = extra
    try:
        with open(FAILURE_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"Failed to write failure log: {exc}")


def parse_schedule_hours(raw_value: str | None) -> list[int]:
    text = (raw_value or DEFAULT_POST_HOURS_JST).replace(" ", "")
    hours: list[int] = []
    for token in text.split(","):
        if not token:
            continue
        try:
            hour = int(token)
        except ValueError:
            continue
        if hour == 24:
            hour = 0
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)
    return sorted(hours) or [12]


def should_skip_by_schedule(dry_run: bool) -> tuple[bool, str]:
    if dry_run:
        return False, "Dry-run mode: schedule guard skipped."
    if not env_flag("X_SCHEDULE_GUARD", True):
        return False, "Schedule guard disabled by X_SCHEDULE_GUARD."

    now_jst = datetime.now(JST)
    hours = parse_schedule_hours(os.environ.get("X_POST_HOURS_JST"))
    try:
        window_min = int(os.environ.get("X_POST_WINDOW_MINUTES", str(DEFAULT_POST_WINDOW_MINUTES)))
    except ValueError:
        window_min = DEFAULT_POST_WINDOW_MINUTES

    if now_jst.hour not in hours or now_jst.minute > max(0, window_min):
        return True, (
            "Schedule guard: skip outside post window "
            f"(now={now_jst.strftime('%H:%M')} JST, hours={hours}, window_min={window_min})."
        )
    return False, (
        "Schedule guard: within post window "
        f"(now={now_jst.strftime('%H:%M')} JST, hours={hours}, window_min={window_min})."
    )


def is_auth_error_response(response: Any | None) -> bool:
    if response is None:
        return False
    if response.status_code in {401, 403}:
        return True
    text = response.text or ""
    return any(
        marker in text
        for marker in (
            "Could not authenticate you",
            "Invalid or expired token",
            "Read-only application cannot POST",
        )
    )


def verify_media_auth(auth: Any) -> bool:
    requests_module, _oauth1_class = require_x_http()
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={
            "command": "INIT",
            "total_bytes": 1,
            "media_type": "video/mp4",
            "media_category": "tweet_video",
        },
        auth=auth,
        timeout=30,
    )
    if resp.status_code >= 400:
        print("X media auth check failed.")
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        return False
    print("X media auth check OK.")
    return True


def load_json_file(path: str, default: object) -> object:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def save_json_file(path: str, payload: object) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def load_uploaded_log() -> list[str]:
    value = load_json_file(UPLOADED_LOG, [])
    return value if isinstance(value, list) else []


def save_uploaded_log(log: list[str]) -> None:
    save_json_file(UPLOADED_LOG, log)


def load_account_insights() -> dict:
    value = load_json_file(INSIGHTS_FILE, {})
    insights = value if isinstance(value, dict) else {}
    # M国 content_pool（dashboard/autonomyが毎日再生成）をマージ。
    # pool由来を先頭に、手動insightsを後置（choose系はランダム選択なので両方が候補になる）。
    # pool取得失敗時は既存insights/ハードコードのみで動く（憲法第1条: 絶対に死なない）。
    try:
        from pool_loader import as_insights
        pool_ins = as_insights("mature_muscle", platform="x")
        for key in ("recommended_tags", "recommended_templates", "recommended_ctas", "avoid_tags"):
            existing = insights.get(key)
            existing_list = list(existing) if isinstance(existing, list) else []
            merged = list(pool_ins.get(key, [])) + existing_list
            if merged:
                insights[key] = merged
        if pool_ins and not insights.get("updated_at_jst"):
            insights["updated_at_jst"] = pool_ins.get("updated_at_jst", "")
    except Exception as exc:
        print(f"pool_loader merge skipped: {exc}")
    return insights


def get_gdrive_folder_id() -> str:
    now_jst = datetime.now(JST)
    friday_folder = os.environ.get("GDRIVE_FOLDER_ID_FRIDAY", "")
    default_folder = os.environ.get("GDRIVE_FOLDER_ID_DEFAULT", "")
    if now_jst.weekday() == 4 and now_jst.hour == 21 and friday_folder:
        print("Using Friday folder.")
        return friday_folder
    print(f"Using default folder (JST {now_jst.strftime('%A %H:%M')}).")
    return default_folder


def collect_media_files(dl_dir: str) -> list[str]:
    files: list[str] = []
    for root, _dirs, filenames in os.walk(dl_dir):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            spec = MEDIA_SPECS.get(ext)
            if spec is None:
                continue
            path_lower = fpath.lower()
            if any(word in path_lower for word in UNSAFE_TAG_WORDS):
                print(f"Skipping unsafe filename: {fname}")
                continue
            if os.path.getsize(fpath) <= spec[2]:
                files.append(fpath)
    return files


def download_media() -> list[str] | None:
    dl_dir = "videos"
    os.makedirs(dl_dir, exist_ok=True)
    folder_id = get_gdrive_folder_id()
    if not folder_id:
        print("Error: GDRIVE_FOLDER_ID_DEFAULT is not set.")
        return []

    import gdown

    url = f"https://drive.google.com/drive/folders/{folder_id}"
    print(f"Downloading from Google Drive: {url}")
    try:
        try:
            gdown.download_folder(url, output=dl_dir, quiet=False, remaining_ok=True)
        except TypeError as exc:
            if "remaining_ok" not in str(exc):
                raise
            gdown.download_folder(url, output=dl_dir, quiet=False)
    except Exception as exc:
        print(f"Download error: {exc}")
        partial_files = collect_media_files(dl_dir)
        if partial_files:
            print(f"Continuing with {len(partial_files)} partially downloaded files.")
            return partial_files
        return None

    return collect_media_files(dl_dir)


def generate_tags(video_path: str) -> list[str]:
    tags = list(BASE_TAGS)
    path_lower = video_path.lower().replace("\\", "/").replace("-", " ").replace("_", " ")
    for keyword, keyword_tags in CONTENT_TAG_MAP.items():
        if keyword in path_lower:
            tags.extend(keyword_tags)
    return dedupe(tags)


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        key = str(value).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(str(value))
    return result


def is_safe_tag(tag: str) -> bool:
    tag_lower = tag.lower().strip("#")
    if not tag_lower:
        return False
    if HASHLIKE_RE.match(tag_lower):
        return False
    if any(word in tag_lower for word in UNSAFE_TAG_WORDS):
        return False
    return True


def filter_tags(tags: list[str], max_tags: int = 12) -> list[str]:
    safe: list[str] = []
    seen = set()
    for tag in tags:
        cleaned = re.sub(r"\s+", "", str(tag)).strip("#")
        if not is_safe_tag(cleaned):
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        safe.append(cleaned)
        if len(safe) >= max_tags:
            break
    return safe


def sanitize_text(text: str) -> str:
    sanitized = text
    for ng in NG_WORDS:
        sanitized = re.sub(re.escape(ng), "", sanitized, flags=re.IGNORECASE)
    return sanitized


def is_safe_category(category: str) -> bool:
    value = sanitize_text(str(category)).strip()
    if not value:
        return False
    lower = value.lower()
    if HASHLIKE_RE.match(lower):
        return False
    if any(word in lower for word in UNSAFE_TAG_WORDS):
        return False
    if any(word in lower for word in OFF_TOPIC_CATEGORY_WORDS):
        return False
    if re.search(r"[0-9a-f]{8}-[0-9a-f-]{10,}", lower):
        return False
    return True


def category_from_path(video_path: str) -> str:
    for part in video_path.replace("\\", "/").split("/"):
        if part and "." not in part and part != "videos" and is_safe_category(part):
            return sanitize_text(part)
    return "Muscle"


def choose_from_insights(insights: dict, key: str, fallback: list[str]) -> str:
    values = insights.get(key)
    if isinstance(values, list):
        clean = [str(value).strip() for value in values if str(value).strip()]
        if clean:
            return random.choice(clean)
    return random.choice(fallback)


def merge_insight_tags(tags: list[str], insights: dict) -> list[str]:
    account_tags = insights.get("recommended_tags")
    if isinstance(account_tags, list):
        tags.extend(str(tag).strip("#") for tag in account_tags)
    avoid_tags = insights.get("avoid_tags")
    avoid = {str(tag).strip("#").lower() for tag in avoid_tags} if isinstance(avoid_tags, list) else set()
    return [tag for tag in tags if str(tag).strip("#").lower() not in avoid]


URL_IN_TWEET_RE = re.compile(r"https?://\S+")
# X weighted length: これらのコードポイント範囲は1字、それ以外(日本語含む)は2字換算
LIGHT_WEIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
URL_WEIGHT = 23  # t.co短縮により全URLは23字換算


def _weighted_chars(text: str) -> int:
    total = 0
    for ch in text:
        cp = ord(ch)
        total += 1 if any(lo <= cp <= hi for lo, hi in LIGHT_WEIGHT_RANGES) else 2
    return total


def weighted_len(text: str) -> int:
    """X基準のツイート長（CJK=2字、URL=23字換算）。"""
    total = 0
    pos = 0
    for match in URL_IN_TWEET_RE.finditer(text):
        total += _weighted_chars(text[pos:match.start()]) + URL_WEIGHT
        pos = match.end()
    return total + _weighted_chars(text[pos:])


def trim_to_weight(text: str, budget: int) -> str:
    while text and weighted_len(text) > budget:
        text = text[:-1]
    return text.rstrip()


def ensure_link(tweet: str) -> str:
    """投稿に計測可能なリンクが1本も無ければハブURLを必ず足す（流入ゼロ媒体の根治）。"""
    if "http" in tweet:
        return tweet
    with_link = f"{tweet}\n{HUB_LINK}"
    if weighted_len(with_link) <= MAX_TWEET_CHARS:
        return with_link
    body = trim_to_weight(tweet, MAX_TWEET_CHARS - URL_WEIGHT - 1)
    return body + "\n" + HUB_LINK


def build_tweet_text(video_path: str, tags: list[str], insights: dict) -> str:
    category = category_from_path(video_path)
    category = CATEGORY_JP.get(category.lower(), category)
    # poolテンプレとローカルの俺口調テンプレを常に両方候補にして文面の幅を出す
    pool_templates = insights.get("recommended_templates")
    candidates = [str(v).strip() for v in pool_templates if str(v).strip()] if isinstance(pool_templates, list) else []
    template = random.choice(candidates + TWEET_TEMPLATES)
    cta = choose_from_insights(insights, "recommended_ctas", CTA_LINES)
    hashtags = " ".join(f"#{tag}" for tag in filter_tags(merge_insight_tags(tags, insights)))

    body = template.format(category=category, hashtags="").strip()
    tweet = sanitize_text(f"{body}\n{cta}\n\n{hashtags}".strip())

    if weighted_len(tweet) <= MAX_TWEET_CHARS:
        return ensure_link(tweet)

    compact = f"{body}\n{cta}".strip()
    trimmed_tags: list[str] = []
    for tag in hashtags.split():
        candidate = compact + "\n\n" + " ".join(trimmed_tags + [tag])
        if weighted_len(candidate) > MAX_TWEET_CHARS:
            break
        trimmed_tags.append(tag)
    tweet = compact + ("\n\n" + " ".join(trimmed_tags) if trimmed_tags else "")
    if weighted_len(tweet) > MAX_TWEET_CHARS:
        tweet = trim_to_weight(tweet, MAX_TWEET_CHARS - 3) + "..."
    return ensure_link(tweet)


def upload_media_init(
    auth: Any,
    file_size: int,
    media_type: str = "video/mp4",
    media_category: str = "tweet_video",
) -> str:
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={
            "command": "INIT",
            "total_bytes": file_size,
            "media_type": media_type,
            "media_category": media_category,
        },
        auth=auth,
        timeout=60,
    )
    resp.raise_for_status()
    media_id = resp.json()["media_id_string"]
    print(f"INIT OK: media_id={media_id}")
    return media_id


def upload_media_append(auth: Any, media_id: str, file_path: str, chunk_size: int = 4 * 1024 * 1024) -> int:
    segment = 0
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            resp = requests.post(
                MEDIA_UPLOAD_URL,
                data={"command": "APPEND", "media_id": media_id, "segment_index": segment},
                files={"media": chunk},
                auth=auth,
                timeout=120,
            )
            resp.raise_for_status()
            print(f"APPEND segment {segment} OK")
            segment += 1
    return segment


def upload_media_finalize(auth: Any, media_id: str) -> dict:
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={"command": "FINALIZE", "media_id": media_id},
        auth=auth,
        timeout=60,
    )
    print(f"FINALIZE status: {resp.status_code}")
    print(f"FINALIZE response: {resp.text}")
    resp.raise_for_status()
    return resp.json()


def wait_for_processing(auth: Any, media_id: str, max_wait: int = 300) -> bool:
    elapsed = 0
    while elapsed < max_wait:
        resp = requests.get(
            MEDIA_UPLOAD_URL,
            params={"command": "STATUS", "media_id": media_id},
            auth=auth,
            timeout=60,
        )
        resp.raise_for_status()
        info = resp.json()
        state = info.get("processing_info", {}).get("state", "")
        if state == "succeeded":
            print("Processing complete.")
            return True
        if state == "failed":
            print(f"Processing failed: {info.get('processing_info', {}).get('error', {})}")
            return False
        wait_sec = info.get("processing_info", {}).get("check_after_secs", 5)
        print(f"Processing... state={state}, waiting {wait_sec}s")
        time.sleep(wait_sec)
        elapsed += wait_sec
    print("Processing timeout.")
    return False


def upload_media(auth: Any, file_path: str) -> str | None:
    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    media_type, media_category, _max_size = MEDIA_SPECS.get(ext, MEDIA_SPECS[".mp4"])

    media_id = upload_media_init(auth, file_size, media_type, media_category)
    upload_media_append(auth, media_id, file_path)
    result = upload_media_finalize(auth, media_id)
    if "processing_info" in result and not wait_for_processing(auth, media_id):
        return None
    return media_id


def post_tweet(auth: Any, text: str, media_id: str) -> dict:
    resp = requests.post(
        TWEET_URL,
        json={"text": text, "media": {"media_ids": [media_id]}},
        auth=auth,
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"Tweet posted! id={result['data']['id']}")
    return result


def main() -> int:
    dry_run = env_flag("X_DRY_RUN") or "--dry-run" in sys.argv

    required = ["X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing and not dry_run:
        message = f"Missing X API credentials: {', '.join(missing)}"
        print(f"Error: {message}")
        write_failure("auth", message)
        return 1

    if missing and dry_run:
        print(f"DRY RUN: X API credentials not required ({', '.join(missing)} missing).")
    else:
        print("Auth credentials loaded.")
    auth = None if dry_run else get_oauth()
    if "--check-auth-only" in sys.argv:
        auth = get_oauth()
        ok = verify_media_auth(auth)
        if not ok:
            write_failure("auth_check", "X media auth check failed")
        return 0 if ok else AUTH_ERROR_EXIT_CODE

    if dry_run:
        print("DRY RUN: media upload and tweet publishing will be skipped.")
    skip_post, schedule_msg = should_skip_by_schedule(dry_run)
    print(schedule_msg)
    if skip_post:
        return 0

    media_files = download_media()
    if media_files is None:
        write_failure("download", "Media download failed")
        return 1
    if not media_files:
        print("No media files found.")
        return 0

    uploaded_log = load_uploaded_log()
    available = [v for v in media_files if os.path.basename(v) not in uploaded_log]
    if not available:
        print("All media already uploaded.")
        return 0

    print(f"Available: {len(available)} / Total: {len(media_files)}")
    video = random.choice(available)
    fname = os.path.basename(video)
    print(f"Selected: {fname}")

    tags = generate_tags(video)
    try:
        from trending import get_trending_tags

        trend_tags = get_trending_tags(max_tags=5)
    except Exception as exc:
        print(f"Trend import/fetch failed (non-fatal): {exc}")
        trend_tags = []
    if trend_tags:
        safe_trends = filter_tags(trend_tags, max_tags=5)
        # トレンドタグは文字数制限で削られないよう、ブランド核タグ3個の直後に差し込む
        tags = dedupe(tags[:3] + safe_trends + tags[3:])
        print(f"Merged trend tags: {safe_trends}")
    else:
        print("Merged trend tags: none")

    insights = load_account_insights()
    if insights:
        print(f"Loaded account insights updated_at={insights.get('updated_at_jst', 'unknown')}")
    tweet_text = build_tweet_text(video, tags, insights)
    print(f"Tags: {', '.join(filter_tags(tags)[:10])}...")
    print(f"Tweet length (X weighted): {weighted_len(tweet_text)} / {MAX_TWEET_CHARS}")
    print(f"Tweet:\n{tweet_text}\n")

    if dry_run:
        print(f"DRY RUN OK: selected={fname}")
        return 0

    try:
        print("Uploading media...")
        media_id = upload_media(auth, video)
        if not media_id:
            write_failure("media_upload", "Media upload/processing failed", file=fname)
            return 1

        print("Posting tweet...")
        post_tweet(auth, tweet_text, media_id)

        uploaded_log.append(fname)
        save_uploaded_log(uploaded_log)
        print(f"Success. Remaining: {len(available) - 1}")
        return 0
    except requests.exceptions.HTTPError as exc:
        print(f"HTTP Error: {exc}")
        if exc.response is not None:
            print(f"Status: {exc.response.status_code}")
            print(f"Response: {exc.response.text}")
            write_failure("http_error", f"HTTP {exc.response.status_code}", response_text=exc.response.text[:800])
            if is_auth_error_response(exc.response):
                print("X API credentials are invalid, expired, or missing write/media permission.")
                return AUTH_ERROR_EXIT_CODE
        else:
            write_failure("http_error", "HTTP error without response object")
        return 1
    except Exception as exc:
        print(f"Upload error: {exc}")
        write_failure("runtime_error", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())

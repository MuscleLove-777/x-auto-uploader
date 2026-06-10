# -*- coding: utf-8 -*-
"""Post one Google Drive video to X.

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

import requests
from requests_oauthlib import OAuth1

JST = timezone(timedelta(hours=9))

VIDEO_EXTENSIONS = {".mp4", ".mov"}
MAX_FILE_SIZE = 512 * 1024 * 1024
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

TWEET_TEMPLATES = [
    "Training note: {category}\nStrong lines, steady work.",
    "{category}\nNo shortcuts. Just reps and consistency.",
    "Today's focus: {category}\nSmall progress still counts.",
    "{category} energy.\nBuilt one session at a time.",
    "Form, balance, and control.\n{category}",
    "Strength looks better when it is earned.\n{category}",
]

CTA_LINES = [
    "More daily updates on MuscleLove.",
    "Saving this one for the motivation file.",
    "One strong post a day. See you tomorrow.",
    "More training inspiration in the profile flow.",
]


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def get_oauth() -> OAuth1:
    return OAuth1(
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


def is_auth_error_response(response: requests.Response | None) -> bool:
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


def verify_media_auth(auth: OAuth1) -> bool:
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
        pool_ins = as_insights("mature_muscle")
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


def collect_video_files(dl_dir: str) -> list[str]:
    files: list[str] = []
    for root, _dirs, filenames in os.walk(dl_dir):
        for fname in filenames:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            path_lower = fpath.lower()
            if any(word in path_lower for word in UNSAFE_TAG_WORDS):
                print(f"Skipping unsafe filename: {fname}")
                continue
            if os.path.getsize(fpath) <= MAX_FILE_SIZE:
                files.append(fpath)
    return files


def download_videos() -> list[str] | None:
    import gdown

    dl_dir = "videos"
    os.makedirs(dl_dir, exist_ok=True)
    folder_id = get_gdrive_folder_id()
    if not folder_id:
        print("Error: GDRIVE_FOLDER_ID_DEFAULT is not set.")
        return []

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
        partial_files = collect_video_files(dl_dir)
        if partial_files:
            print(f"Continuing with {len(partial_files)} partially downloaded videos.")
            return partial_files
        return None

    return collect_video_files(dl_dir)


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


def build_tweet_text(video_path: str, tags: list[str], insights: dict) -> str:
    category = category_from_path(video_path)
    template = choose_from_insights(insights, "recommended_templates", TWEET_TEMPLATES)
    cta = choose_from_insights(insights, "recommended_ctas", CTA_LINES)
    hashtags = " ".join(f"#{tag}" for tag in filter_tags(merge_insight_tags(tags, insights)))

    body = template.format(category=category, hashtags="").strip()
    tweet = sanitize_text(f"{body}\n{cta}\n\n{hashtags}".strip())

    if len(tweet) <= MAX_TWEET_CHARS:
        return tweet

    compact = f"{body}\n{cta}".strip()
    trimmed_tags: list[str] = []
    for tag in hashtags.split():
        candidate = compact + "\n\n" + " ".join(trimmed_tags + [tag])
        if len(candidate) > MAX_TWEET_CHARS:
            break
        trimmed_tags.append(tag)
    tweet = compact + ("\n\n" + " ".join(trimmed_tags) if trimmed_tags else "")
    if len(tweet) > MAX_TWEET_CHARS:
        tweet = tweet[: MAX_TWEET_CHARS - 1].rstrip() + "..."
    return tweet


def upload_media_init(auth: OAuth1, file_size: int, media_type: str = "video/mp4") -> str:
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={
            "command": "INIT",
            "total_bytes": file_size,
            "media_type": media_type,
            "media_category": "tweet_video",
        },
        auth=auth,
        timeout=60,
    )
    resp.raise_for_status()
    media_id = resp.json()["media_id_string"]
    print(f"INIT OK: media_id={media_id}")
    return media_id


def upload_media_append(auth: OAuth1, media_id: str, file_path: str, chunk_size: int = 4 * 1024 * 1024) -> int:
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


def upload_media_finalize(auth: OAuth1, media_id: str) -> dict:
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


def wait_for_processing(auth: OAuth1, media_id: str, max_wait: int = 300) -> bool:
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


def upload_video(auth: OAuth1, file_path: str) -> str | None:
    file_size = os.path.getsize(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    media_type = "video/mp4" if ext == ".mp4" else "video/quicktime"

    media_id = upload_media_init(auth, file_size, media_type)
    upload_media_append(auth, media_id, file_path)
    result = upload_media_finalize(auth, media_id)
    if "processing_info" in result and not wait_for_processing(auth, media_id):
        return None
    return media_id


def post_tweet(auth: OAuth1, text: str, media_id: str) -> dict:
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
    auth = get_oauth()
    dry_run = env_flag("X_DRY_RUN")

    required = ["X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        message = f"Missing X API credentials: {', '.join(missing)}"
        print(f"Error: {message}")
        write_failure("auth", message)
        return 1

    print("Auth credentials loaded.")
    if "--check-auth-only" in sys.argv:
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

    videos = download_videos()
    if videos is None:
        write_failure("download", "Video download failed")
        return 1
    if not videos:
        print("No videos found.")
        return 0

    uploaded_log = load_uploaded_log()
    available = [v for v in videos if os.path.basename(v) not in uploaded_log]
    if not available:
        print("All videos already uploaded.")
        return 0

    print(f"Available: {len(available)} / Total: {len(videos)}")
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
        tags = dedupe(tags + safe_trends)
        print(f"Merged trend tags: {safe_trends}")
    else:
        print("Merged trend tags: none")

    insights = load_account_insights()
    if insights:
        print(f"Loaded account insights updated_at={insights.get('updated_at_jst', 'unknown')}")
    tweet_text = build_tweet_text(video, tags, insights)
    print(f"Tags: {', '.join(filter_tags(tags)[:10])}...")
    print(f"Tweet length: {len(tweet_text)} / {MAX_TWEET_CHARS}")
    print(f"Tweet:\n{tweet_text}\n")

    if dry_run:
        print(f"DRY RUN OK: selected={fname}")
        return 0

    try:
        print("Uploading video...")
        media_id = upload_video(auth, video)
        if not media_id:
            write_failure("media_upload", "Video upload/processing failed", file=fname)
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

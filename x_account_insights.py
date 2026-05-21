# -*- coding: utf-8 -*-
"""Build lightweight posting guidance from recent X account posts.

This does not need private analytics. When the X token can read the authenticated
user timeline, it extracts durable style signals and writes x_account_insights.json.
If timeline access is unavailable on the current X API plan, it writes a practical
fallback so the uploader still improves deterministically.
"""

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests
from requests_oauthlib import OAuth1

JST = timezone(timedelta(hours=9))
USER_ME_URL = "https://api.x.com/2/users/me"
TIMELINE_URL = "https://api.x.com/2/users/{user_id}/tweets"

DEFAULT_TEMPLATES = [
    "Training note: {category}\nStrong lines, steady work.",
    "{category}\nNo shortcuts. Just reps and consistency.",
    "Today's focus: {category}\nSmall progress still counts.",
    "Form, balance, and control.\n{category}",
]
DEFAULT_CTAS = [
    "More daily updates on MuscleLove.",
    "One strong post a day. See you tomorrow.",
    "More training inspiration in the profile flow.",
]
DEFAULT_TAGS = [
    "musclegirl",
    "muscularwoman",
    "femalemuscle",
    "fitnessmotivation",
    "musclebeauty",
    "workoutmotivation",
]
AVOID_TAGS = ["nsfw", "adult", "sexy", "nude", "porn", "erotic"]


def get_auth() -> OAuth1:
    return OAuth1(
        os.environ.get("X_CONSUMER_KEY", ""),
        os.environ.get("X_CONSUMER_SECRET", ""),
        os.environ.get("X_ACCESS_TOKEN", ""),
        os.environ.get("X_ACCESS_TOKEN_SECRET", ""),
    )


def request_json(url: str, auth: OAuth1, **kwargs) -> dict:
    resp = requests.get(url, auth=auth, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def fetch_recent_posts(max_results: int) -> list[dict]:
    auth = get_auth()
    me = request_json(USER_ME_URL, auth)
    user_id = me["data"]["id"]
    payload = request_json(
        TIMELINE_URL.format(user_id=user_id),
        auth,
        params={
            "max_results": max(5, min(max_results, 100)),
            "tweet.fields": "created_at,public_metrics,lang",
            "exclude": "retweets,replies",
        },
    )
    return payload.get("data", [])


def extract_hashtags(text: str) -> list[str]:
    return [tag.strip("#") for tag in re.findall(r"#[A-Za-z0-9_]+", text)]


def clean_line(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"#[A-Za-z0-9_]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def score_post(post: dict) -> int:
    metrics = post.get("public_metrics") or {}
    return (
        int(metrics.get("like_count", 0)) * 3
        + int(metrics.get("retweet_count", 0)) * 5
        + int(metrics.get("reply_count", 0)) * 4
        + int(metrics.get("quote_count", 0)) * 4
    )


def build_templates(winners: list[dict]) -> list[str]:
    templates: list[str] = []
    for post in winners:
        lines = [clean_line(line) for line in post.get("text", "").splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        first = lines[0]
        if len(first) > 80:
            first = first[:77].rstrip() + "..."
        templates.append(f"{first}\n{{category}}")
        if len(templates) >= 3:
            break
    return templates or DEFAULT_TEMPLATES


def build_insights(posts: list[dict]) -> dict:
    ranked = sorted(posts, key=score_post, reverse=True)
    winners = ranked[:5]
    hashtags = Counter()
    for post in posts:
        hashtags.update(tag.lower() for tag in extract_hashtags(post.get("text", "")))
    recommended_tags = [
        tag
        for tag, _count in hashtags.most_common(8)
        if tag.lower() not in AVOID_TAGS and len(tag) <= 30
    ]
    if not recommended_tags:
        recommended_tags = DEFAULT_TAGS

    return {
        "updated_at_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "source": "x_recent_timeline",
        "posts_analyzed": len(posts),
        "recommended_templates": build_templates(winners),
        "recommended_ctas": DEFAULT_CTAS,
        "recommended_tags": recommended_tags,
        "avoid_tags": AVOID_TAGS,
        "top_post_ids": [post.get("id") for post in winners if post.get("id")],
    }


def fallback_insights(reason: str) -> dict:
    return {
        "updated_at_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "source": "fallback",
        "reason": reason,
        "posts_analyzed": 0,
        "recommended_templates": DEFAULT_TEMPLATES,
        "recommended_ctas": DEFAULT_CTAS,
        "recommended_tags": DEFAULT_TAGS,
        "avoid_tags": AVOID_TAGS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate X account posting insights.")
    parser.add_argument("--output", default="x_account_insights.json")
    parser.add_argument("--max-results", type=int, default=50)
    args = parser.parse_args()

    try:
        posts = fetch_recent_posts(args.max_results)
        insights = build_insights(posts) if posts else fallback_insights("No timeline posts returned")
    except Exception as exc:
        print(f"Account insight fetch failed; using fallback: {exc}")
        insights = fallback_insights(str(exc)[:300])

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(insights, handle, ensure_ascii=False, indent=2)
    print(f"Wrote {args.output}: source={insights['source']} posts={insights['posts_analyzed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

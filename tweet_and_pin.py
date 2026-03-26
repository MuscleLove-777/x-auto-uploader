# -*- coding: utf-8 -*-
"""
テキストツイートを投稿して固定ポストにするスクリプト（GitHub Actions用）
"""
import os
import sys
import json
import requests
from requests_oauthlib import OAuth1

TWEET_URL = "https://api.x.com/2/tweets"
PIN_URL_TEMPLATE = "https://api.x.com/2/users/{user_id}/pinned_lists"
USER_ME_URL = "https://api.x.com/2/users/me"


def get_auth():
    return OAuth1(
        os.environ["X_CONSUMER_KEY"],
        os.environ["X_CONSUMER_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def get_user_id(auth):
    """自分のuser_idを取得"""
    resp = requests.get(USER_ME_URL, auth=auth)
    resp.raise_for_status()
    data = resp.json()
    user_id = data["data"]["id"]
    print(f"User: @{data['data']['username']} (ID: {user_id})")
    return user_id


def post_tweet(text, auth):
    """テキストツイートを投稿"""
    payload = {"text": text}
    resp = requests.post(TWEET_URL, json=payload, auth=auth)
    if resp.status_code in (200, 201):
        tweet_data = resp.json()
        tweet_id = tweet_data["data"]["id"]
        print(f"Tweet posted! ID: {tweet_id}")
        return tweet_id
    else:
        print(f"Tweet failed: {resp.status_code} {resp.text}")
        sys.exit(1)


def pin_tweet(user_id, tweet_id, auth):
    """ツイートを固定ポストにする"""
    url = f"https://api.x.com/2/users/{user_id}/pinned_lists"
    # V1.1 API for pinning (v2 doesn't have pin endpoint)
    pin_url = "https://api.twitter.com/1.1/account/pin_tweet.json"
    resp = requests.post(pin_url, data={"id": tweet_id}, auth=auth)
    if resp.status_code == 200:
        print(f"Tweet pinned successfully!")
        return True
    else:
        print(f"Pin failed: {resp.status_code} {resp.text}")
        # Try v2 bookmark approach as fallback - not critical
        return False


def main():
    tweet_text = os.environ.get("TWEET_TEXT", "")
    if not tweet_text:
        print("Error: TWEET_TEXT environment variable is empty")
        sys.exit(1)

    auth = get_auth()
    user_id = get_user_id(auth)
    tweet_id = post_tweet(tweet_text, auth)
    pin_tweet(user_id, tweet_id, auth)

    print(f"\nDone! Tweet URL: https://x.com/i/status/{tweet_id}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 X（Twitter）关注时间线增量写入财经分析数据库。

该脚本调用 X 官方 API v2 的 reverse chronological timeline 端点。它只读，
并复用现有 ``rss_sources`` / ``news_articles`` 表，因此后续 AI 分析无需改动。

需要 OAuth 2.0 用户访问令牌（不是 App-only Bearer Token）：
    可在环境变量或 config/x_credentials.env 中设置：
    X_USER_ACCESS_TOKEN=...

可选：X_USER_ID 未设置时，脚本会通过 ``/2/users/me`` 自动获取。
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS_FILE = PROJECT_ROOT / "config" / "x_credentials.env"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# Windows 终端有时仍使用 GBK；统一使用 UTF-8，避免状态提示导致脚本退出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from utils.db_manager import DatabaseManager  # noqa: E402
from utils.logger import get_logger  # noqa: E402


logger = get_logger("x_timeline_collector")
DEFAULT_BASE_URL = "https://api.x.com/2"
TWEET_FIELDS = "created_at,author_id,lang,public_metrics,entities,referenced_tweets"
USER_FIELDS = "id,name,username"


class XApiError(RuntimeError):
    """X API 返回了无法继续处理的响应。"""


def load_local_credentials() -> None:
    """读取本机凭据文件，且绝不覆盖已设置的环境变量。"""
    if not CREDENTIALS_FILE.is_file():
        return
    for raw_line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


class XTimelineCollector:
    def __init__(
        self,
        access_token: str,
        db_path: Path,
        base_url: str = DEFAULT_BASE_URL,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.db = DatabaseManager(db_path)
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        )

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        if response.status_code == 401:
            raise XApiError("X 授权失败：请更新 X_USER_ACCESS_TOKEN，确认它是 OAuth 用户访问令牌。")
        if response.status_code == 403:
            detail = response.text[:500]
            if "Application-Only" in detail:
                raise XApiError(
                    "当前填入的是 App-only Bearer Token，不能读取关注时间线。"
                    "请在 X Console 的 OAuth 2.0 Keys -> Access Token 中生成并使用用户访问令牌。"
                )
            raise XApiError(
                "X 拒绝访问：请确认应用套餐和 OAuth 作用域包含 tweet.read、users.read。"
                f" X 返回：{detail}"
            )
        if response.status_code == 429:
            raise XApiError("X API 调用额度已用尽，请稍后重试或检查 API 套餐。")
        if response.status_code == 402:
            raise XApiError(
                "X API 账户可用 credits 已耗尽。请在 X Developer Console 的 Credits 页面"
                "充值或分配可用 credits 后再运行采集。"
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text[:500]
            raise XApiError(f"X API 请求失败（HTTP {response.status_code}）：{detail}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise XApiError("X API 返回的不是 JSON 数据。") from exc

    def get_current_user_id(self) -> str:
        payload = self._get("/users/me", {"user.fields": USER_FIELDS})
        user_id = (payload.get("data") or {}).get("id")
        if not user_id:
            raise XApiError("无法从 /2/users/me 获取当前 X 用户 ID。")
        return str(user_id)

    def fetch_timeline(
        self,
        user_id: str,
        max_results: int,
        pages: int,
        exclude_replies: bool,
        exclude_retweets: bool,
    ) -> Iterable[Dict[str, Any]]:
        pagination_token: Optional[str] = None
        excludes = []
        if exclude_replies:
            excludes.append("replies")
        if exclude_retweets:
            excludes.append("retweets")

        for _ in range(pages):
            params: Dict[str, Any] = {
                "max_results": max_results,
                "tweet.fields": TWEET_FIELDS,
                "expansions": "author_id",
                "user.fields": USER_FIELDS,
            }
            if excludes:
                params["exclude"] = ",".join(excludes)
            if pagination_token:
                params["pagination_token"] = pagination_token

            payload = self._get(f"/users/{user_id}/timelines/reverse_chronological", params)
            users = {
                str(user["id"]): user
                for user in (payload.get("includes") or {}).get("users", [])
                if user.get("id")
            }
            for tweet in payload.get("data") or []:
                author = users.get(str(tweet.get("author_id")), {})
                tweet["_author_username"] = author.get("username", "unknown")
                tweet["_author_name"] = author.get("name") or tweet["_author_username"]
                yield tweet

            pagination_token = (payload.get("meta") or {}).get("next_token")
            if not pagination_token:
                break

    @staticmethod
    def _article_from_tweet(tweet: Dict[str, Any], collection_date: str) -> tuple:
        tweet_id = str(tweet["id"])
        username = tweet.get("_author_username", "unknown")
        text = (tweet.get("text") or "").strip()
        title = text.replace("\n", " ")[:180] or f"@{username} 的 X 帖子"
        published = tweet.get("created_at") or ""
        metrics = tweet.get("public_metrics") or {}
        metrics_line = " · ".join(
            f"{label}{metrics.get(key, 0)}"
            for label, key in (("回复", "reply_count"), ("转发", "retweet_count"), ("点赞", "like_count"))
        )
        summary = f"@{username}\n{text}"
        content = f"作者：{tweet.get('_author_name', username)} (@{username})\n发布时间：{published}\n{metrics_line}\n\n{text}"
        return (
            collection_date,
            title,
            f"https://x.com/{username}/status/{tweet_id}",
            published,
            summary,
            content,
        )

    def _init_database(self) -> None:
        """兼容空数据库：建表结构与 RSS 采集器保持一致。"""
        with self.db.transaction() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS rss_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT UNIQUE NOT NULL,
                    rss_url TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS news_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    link TEXT UNIQUE NOT NULL,
                    source_id INTEGER NOT NULL,
                    published TEXT,
                    published_parsed TEXT,
                    summary TEXT,
                    content TEXT,
                    category TEXT,
                    sentiment_score REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_id) REFERENCES rss_sources (id)
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_collection_date ON news_articles(collection_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_link ON news_articles(link)")

    def save_tweets(self, tweets: List[Dict[str, Any]], collection_date: str) -> int:
        self._init_database()
        source_names = sorted(
            {f"X / @{tweet.get('_author_username', 'unknown')}" for tweet in tweets}
        )
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO rss_sources (source_name, rss_url) VALUES (?, ?)",
                [(name, "https://x.com") for name in source_names],
            )
            source_map = {
                row["source_name"]: row["id"]
                for row in conn.execute("SELECT id, source_name FROM rss_sources")
            }
            rows = []
            for tweet in tweets:
                collection, title, link, published, summary, content = self._article_from_tweet(
                    tweet, collection_date
                )
                source_id = source_map[f"X / @{tweet.get('_author_username', 'unknown')}"]
                rows.append((collection, title, link, source_id, published, summary, content))
            cursor = conn.executemany(
                """INSERT OR IGNORE INTO news_articles
                (collection_date, title, link, source_id, published, summary, content)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            return max(cursor.rowcount, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集 X 关注时间线，并写入财经分析数据库")
    parser.add_argument("--max-results", type=int, default=50, help="每页帖子数，5-100，默认 50")
    parser.add_argument("--pages", type=int, default=1, help="最多读取页数，默认 1")
    parser.add_argument("--user-id", help="X 用户 ID，默认读取 X_USER_ID 或由 /2/users/me 自动获取")
    parser.add_argument("--date", help="写入数据库的采集日期（YYYY-MM-DD），默认当天")
    parser.add_argument("--exclude-replies", action="store_true", help="排除回复")
    parser.add_argument("--include-retweets", action="store_true", help="包含转帖，默认排除")
    args = parser.parse_args()
    if not 5 <= args.max_results <= 100:
        parser.error("--max-results 必须在 5 到 100 之间")
    if args.pages < 1:
        parser.error("--pages 必须至少为 1")
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            parser.error("--date 必须是 YYYY-MM-DD")
    return args


def main() -> int:
    args = parse_args()
    load_local_credentials()
    token = os.getenv("X_USER_ACCESS_TOKEN")
    if not token:
        print("[INFO] 未设置 X_USER_ACCESS_TOKEN，跳过 X 时间线采集。")
        print("   配置方法见 docs/X_API_INTEGRATION.md")
        return 0

    collector = XTimelineCollector(
        access_token=token,
        db_path=PROJECT_ROOT / "data" / "news_data.db",
        base_url=os.getenv("X_API_BASE_URL", DEFAULT_BASE_URL),
    )
    try:
        user_id = args.user_id or os.getenv("X_USER_ID") or collector.get_current_user_id()
        tweets = list(
            collector.fetch_timeline(
                user_id=user_id,
                max_results=args.max_results,
                pages=args.pages,
                exclude_replies=args.exclude_replies,
                exclude_retweets=not args.include_retweets,
            )
        )
        inserted = collector.save_tweets(tweets, args.date or datetime.now().strftime("%Y-%m-%d"))
    except (XApiError, requests.RequestException, sqlite3.Error) as exc:
        logger.error("X 时间线采集失败: %s", exc)
        print(f"[ERROR] X 时间线采集失败：{exc}")
        return 1

    print(f"[OK] X 时间线获取 {len(tweets)} 条，新增入库 {inserted} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

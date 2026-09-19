#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a transparent, rule-based economic brief from X timeline posts.

This intentionally uses no paid LLM.  It looks only at posts already collected
through the official X API, writes a dated Markdown report, and keeps the source
post links so readers can verify the underlying statements themselves.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# China Standard Time has a fixed UTC+8 offset and no daylight-saving changes.
# Using a fixed timezone avoids requiring the optional ``tzdata`` package on
# Windows while remaining identical to Asia/Shanghai for this workflow.
BEIJING = timezone(timedelta(hours=8), name="CST")

THEMES = {
    "利率与债券": (
        "rate", "rates", "yield", "yields", "bond", "bonds", "treasury", "fed", "fomc",
        "interest", "利率", "收益率", "国债", "债券", "美联储", "央行", "降息", "加息",
    ),
    "贸易与政策": (
        "tariff", "trade", "sanction", "export", "import", "policy", "税", "关税", "贸易",
        "制裁", "出口", "进口", "政策", "财政", "补贴",
    ),
    "中国经济": (
        "china", "chinese", "yuan", "renminbi", "cny", "中国", "人民币", "消费", "房地产",
        "地方债", "财政部", "pmi", "cpi", "gdp",
    ),
    "商品与数字资产": (
        "gold", "oil", "crude", "commodity", "bitcoin", "btc", "crypto", "黄金", "原油",
        "大宗商品", "比特币", "加密", "白银",
    ),
    "公司与产业": (
        "earnings", "revenue", "guidance", "stock", "equity", "semiconductor", "ai", "财报",
        "营收", "股价", "股票", "芯片", "人工智能", "科技股",
    ),
}


@dataclass(frozen=True)
class Post:
    title: str
    link: str
    published: datetime
    text: str
    author: str
    themes: tuple[str, ...]


def parse_time(value: str) -> datetime | None:
    """Accept the ISO-8601 timestamps returned by X API v2."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(BEIJING)
    except ValueError:
        return None


def compact(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else f"{text[:limit - 1].rstrip()}…"


def classify(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    matched = []
    for theme, terms in THEMES.items():
        if any(term.lower() in lowered for term in terms):
            matched.append(theme)
    return tuple(matched)


def find_x_posts(db_path: Path, since: datetime, until: datetime) -> list[Post]:
    if not db_path.is_file():
        raise RuntimeError(f"找不到数据库：{db_path}")

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT title, link, published, summary, content
               FROM news_articles
               WHERE link LIKE 'https://x.com/%'
               ORDER BY published DESC"""
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"无法读取 SQLite 数据库：{exc}") from exc
    finally:
        if "conn" in locals():
            conn.close()

    posts = []
    for row in rows:
        published = parse_time(row["published"] or "")
        if not published or not since <= published < until:
            continue
        text = row["summary"] or row["content"] or row["title"] or ""
        themes = classify(text)
        if not themes:
            continue
        author_match = re.search(r"@([A-Za-z0-9_]+)", text)
        author = f"@{author_match.group(1)}" if author_match else "来源用户"
        posts.append(
            Post(
                title=compact(row["title"] or text, 180),
                link=row["link"],
                published=published,
                text=compact(text),
                author=author,
                themes=themes,
            )
        )
    return posts


def report_markdown(posts: Iterable[Post], since: datetime, until: datetime) -> str:
    posts = list(posts)
    theme_counts = Counter(theme for post in posts for theme in post.themes)
    by_theme: dict[str, list[Post]] = defaultdict(list)
    for post in posts:
        for theme in post.themes:
            by_theme[theme].append(post)

    lines = [
        "# X 关注时间线经济简报",
        "",
        f"- 统计区间：{since:%Y-%m-%d %H:%M} 至 {until:%Y-%m-%d %H:%M}（北京时间）",
        f"- 数据范围：通过 X 官方 API 读取的关注时间线；共筛出 **{len(posts)}** 条经济相关帖子。",
        "- 方法：基于公开可复核的关键词分类，不使用付费模型；同一帖子可能出现在多个主题。",
        "",
        "> 提醒：以下是社交媒体信息整理，不代表事实已获独立核验，也不是投资建议。请打开原帖并结合可靠来源判断。",
        "",
        "## 主题热度",
        "",
    ]
    if theme_counts:
        for theme, count in theme_counts.most_common():
            lines.append(f"- **{theme}**：{count} 条")
    else:
        lines.append("- 该时段没有筛出经济相关帖子。")

    for theme, _ in theme_counts.most_common():
        lines.extend(["", f"## {theme}", ""])
        for post in by_theme[theme][:8]:
            lines.extend(
                [
                    f"- {post.published:%H:%M} · {post.author} · [{post.title}]({post.link})",
                    f"  - {post.text}",
                ]
            )
        if len(by_theme[theme]) > 8:
            lines.append(f"- 其余 {len(by_theme[theme]) - 8} 条同主题帖子已省略。")

    lines.extend(
        [
            "",
            "## 阅读建议",
            "",
            "1. 优先核对原帖中的数据、引述和链接；社交媒体观点可能存在遗漏或偏差。",
            "2. 主题热度仅反映本关注时间线的出现频率，不等同于市场重要性或投资结论。",
            "3. 本报告由 GitHub Actions 定时生成；如 X API 额度或授权失效，工作流会显示失败原因。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 X 关注时间线经济简报")
    parser.add_argument("--hours", type=int, default=24, help="回看小时数，默认 24")
    parser.add_argument("--date", help="报告日期 YYYY-MM-DD，默认取当前北京时间")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "news_data.db")
    parser.add_argument("--output", type=Path, help="覆盖默认报告输出路径")
    args = parser.parse_args()
    if args.hours < 1:
        parser.error("--hours 必须至少为 1")
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            parser.error("--date 必须是 YYYY-MM-DD")
    return args


def main() -> int:
    args = parse_args()
    now = datetime.now(BEIJING).replace(second=0, microsecond=0)
    report_date = args.date or now.strftime("%Y-%m-%d")
    until = now
    since = until - timedelta(hours=args.hours)
    output = args.output or (
        PROJECT_ROOT
        / "docs"
        / "archive"
        / report_date[:7]
        / report_date
        / "reports"
        / f"x_timeline_economic_brief_{report_date}.md"
    )
    posts = find_x_posts(args.db, since, until)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_markdown(posts, since, until), encoding="utf-8")
    print(f"[OK] 生成 X 经济简报：{output}（筛出 {len(posts)} 条帖子）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

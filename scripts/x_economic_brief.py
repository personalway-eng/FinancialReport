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
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests


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
    original_text: str
    author: str
    themes: tuple[str, ...]
    translated: bool


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


def translate_to_chinese(text: str) -> tuple[str, bool]:
    """Translate an English post with a public translation endpoint.

    Translation is best effort: a temporary network failure keeps the original
    text so one unavailable translation request cannot stop the daily report.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text or not re.search(r"[A-Za-z]{3}", text):
        return text, False
    # Links and account names can contain Latin characters inside an otherwise
    # Chinese post. Treat a post as Chinese when CJK text clearly dominates.
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    if cjk_count >= 8 and cjk_count >= latin_count:
        return text, False
    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        translated = "".join(
            part[0] for part in (payload[0] if payload else []) if isinstance(part, list) and part
        ).strip()
        if translated:
            time.sleep(0.08)
            return translated, True
    except (requests.RequestException, ValueError, TypeError, IndexError):
        pass

    # Google Translate may rate-limit GitHub Actions. MyMemory is used only as
    # a fallback and keeps the same best-effort behavior.
    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|zh-CN"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        translated = ((payload.get("responseData") or {}).get("translatedText") or "").strip()
        if translated and translated.lower() != text.lower():
            time.sleep(0.08)
            return translated, True
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        pass
    return text, False


def extract_author(content: str, summary: str) -> str:
    """Recover the display name and handle stored by the X collector."""
    match = re.search(r"作者：(.+?)\s*\(@([A-Za-z0-9_]+)\)", content or "")
    if match:
        return f"{compact(match.group(1), 80)} (@{match.group(2)})"
    match = re.match(r"@([A-Za-z0-9_]+)", summary or "")
    return f"@{match.group(1)}" if match else "来源用户"


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
        summary = row["summary"] or ""
        text = summary.split("\n", 1)[1] if "\n" in summary else (row["content"] or row["title"] or "")
        translated_text, translated = translate_to_chinese(text)
        themes = classify(text + "\n" + translated_text)
        if not themes:
            continue
        posts.append(
            Post(
                title=compact(translated_text, 180),
                link=row["link"],
                published=published,
                text=compact(translated_text),
                original_text=compact(text),
                author=extract_author(row["content"] or "", summary),
                themes=themes,
                translated=translated,
            )
        )
    return posts


def report_markdown(posts: Iterable[Post], since: datetime, until: datetime) -> str:
    posts = list(posts)
    theme_counts = Counter(theme for post in posts for theme in post.themes)
    translated_count = sum(post.translated for post in posts)
    by_theme: dict[str, list[Post]] = defaultdict(list)
    for post in posts:
        for theme in post.themes:
            by_theme[theme].append(post)

    lines = [
        "# X 关注时间线经济简报",
        "",
        f"- 统计区间：{since:%Y-%m-%d %H:%M} 至 {until:%Y-%m-%d %H:%M}（北京时间）",
        f"- 数据范围：通过 X 官方 API 读取的关注时间线；共筛出 **{len(posts)}** 条经济相关帖子。",
        f"- 方法：基于公开可复核的关键词分类；其中 **{translated_count}** 条英文帖子已自动翻译为中文，并保留英文原文；同一帖子可能出现在多个主题。",
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
                    f"- {post.published:%H:%M} · **{post.author}** · [查看原帖]({post.link})",
                    f"  - 中文：{post.text}",
                ]
            )
            if post.translated:
                lines.append(f"  - 英文原文：{post.original_text}")
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动生成 MkDocs 导航配置脚本
扫描项目中的分析报告，自动生成 mkdocs.yml 中的 nav 配置
"""

import os
import re
import sys
import yaml
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARCHIVE_ROOT = Path('docs/archive')
HOME_PAGE = Path('docs/index.md')
HISTORY_PAGE = ARCHIVE_ROOT / 'index.md'

def _is_date_dir_name(name: str) -> bool:
    return re.match(r'^\d{4}-\d{2}-\d{2}', name) is not None

def _is_month_dir_name(name: str) -> bool:
    return re.match(r'^\d{4}-\d{2}$', name) is not None

def get_archive_structure():
    """扫描 archive 目录，返回 {month: [date_dir_paths]} 结构，按时间倒序"""
    result = {}
    if not ARCHIVE_ROOT.exists():
        return result
    for month_dir in ARCHIVE_ROOT.iterdir():
        if month_dir.is_dir() and _is_month_dir_name(month_dir.name):
            date_dirs = [p for p in month_dir.iterdir() if p.is_dir() and _is_date_dir_name(p.name)]
            if date_dirs:
                result[month_dir.name] = sorted(date_dirs, key=lambda p: p.name, reverse=True)
    return dict(sorted(result.items(), key=lambda kv: kv[0], reverse=True))

def get_latest_x_economic_brief():
    """返回最新的 X 关注时间线经济简报及其相对于 docs 的路径。"""
    if not ARCHIVE_ROOT.exists():
        return None

    briefs = []
    for report in ARCHIVE_ROOT.glob('*/**/reports/x_timeline_economic_brief_*.md'):
        # 目录日期与文件名均包含日期；用路径排序可稳定取得最新报告。
        briefs.append(report)

    if not briefs:
        return None
    return max(briefs, key=lambda item: item.as_posix())

def update_history_index():
    """生成按月份和日期倒序排列的历史报告索引。"""
    lines = [
        "# 历史分析报告",
        "",
        "这里集中列出系统已经生成的历史报告，最新日期在最前。点击报告名称即可查看完整内容。",
        "",
    ]
    total = 0
    for month, date_dirs in get_archive_structure().items():
        month_display = f"{month[:4]}年{month[5:]}月"
        month_lines = [f"## {month_display}", ""]
        for date_dir in date_dirs:
            reports_dir = date_dir / 'reports'
            reports = sorted(reports_dir.glob('*.md')) if reports_dir.is_dir() else []
            if not reports:
                continue
            month_lines.append(f"### {date_dir.name}")
            for report in reports:
                relative = report.relative_to(ARCHIVE_ROOT).as_posix()
                link = quote(relative, safe='/:@-._~')
                month_lines.append(f"- [{format_report_name(report.name)}]({link})")
                total += 1
            month_lines.append("")
        if len(month_lines) > 2:
            lines.extend(month_lines)

    if total == 0:
        lines.append("当前还没有可查看的历史报告。")
    lines.extend(["", f"共收录 **{total}** 份报告。"])
    HISTORY_PAGE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PAGE.write_text("\n".join(lines) + "\n", encoding='utf-8')
    print(f"✅ 历史报告索引已更新：{total} 份")

def update_homepage():
    """把首页更新为最新 X 经济简报，避免用户只能通过多层导航查阅报告。"""
    latest_report = get_latest_x_economic_brief()
    if latest_report is None:
        return

    report_text = latest_report.read_text(encoding='utf-8').strip()
    report_lines = report_text.splitlines()
    if report_lines and report_lines[0].startswith('# '):
        report_text = '\n'.join(report_lines[1:]).lstrip()
    # 原报告中使用两个行尾空格实现换行；首页生成时改用 HTML 换行，
    # 以便 Git 的空白检查保持通过。
    report_text = report_text.replace('  \n', '<br>\n')

    report_path = latest_report.relative_to('docs').as_posix()
    HOME_PAGE.write_text(
        f"""# 今日财经分析报告

本页展示最新生成的 **X 关注时间线经济简报**。每天北京时间 08:30 更新；内容仅供信息参考，不构成投资建议。

[打开独立报告页](./{report_path}){{ .md-button .md-button--primary }}
[查询历史报告](./archive/index.md){{ .md-button }}

---

{report_text}

---

<details>
<summary>关于本系统</summary>

本系统会采集公开财经信息，并将 X 关注时间线中的经济、市场、行业与政策相关内容整理为每日简报。历史报告可通过顶部“分析报告”导航查看。

</details>
""",
        encoding='utf-8'
    )
    print(f"✅ 首页已更新为最新 X 经济简报：{report_path}")

def get_analysis_files(date_dir):
    """获取指定日期目录下的分析文件"""
    analysis_dir = os.path.join(date_dir, 'analysis')
    reports_dir = os.path.join(date_dir, 'reports')
    
    files = {
        'analysis': [],
        'reports': [],
        'news': [],
        'rss': []
    }
    
    # 分析文件
    if os.path.exists(analysis_dir):
        for file in os.listdir(analysis_dir):
            if file.endswith('.md'):
                files['analysis'].append(file)
    
    # 报告文件（按照场次和模型排序）
    if os.path.exists(reports_dir):
        all_md_files = [f for f in os.listdir(reports_dir) if f.endswith('.md')]
        
        # 分离新旧格式文件
        new_format_files = []  # 带 session 标识的新格式（_morning_、_afternoon_、_evening_、_overnight_）
        old_format_files = []  # 旧格式（没有 session，只有模型后缀）
        
        session_patterns = ['_morning_', '_afternoon_', '_evening_', '_overnight_']
        
        for file in all_md_files:
            has_session = any(pattern in file for pattern in session_patterns)
            if has_session:
                new_format_files.append(file)
            else:
                old_format_files.append(file)
        
        # ⚠️ 优先使用新格式：如果新格式文件存在，则忽略旧格式文件
        if new_format_files:
            files['reports'] = new_format_files
        else:
            # 降级到旧格式（兼容旧数据）
            files['reports'] = old_format_files
        
        # 自定义排序：按场次（morning < afternoon < evening < overnight）和模型（gemini < deepseek）
        def sort_key(filename):
            session_order = {'morning': 1, 'afternoon': 2, 'evening': 3, 'overnight': 4}
            model_order = {'gemini': 1, 'deepseek': 2}
            
            # 提取场次标识（必须严格匹配 _session_ 格式）
            session = 'unknown'
            for s in session_order.keys():
                if f'_{s}_' in filename:
                    session = s
                    break
            
            # 提取模型标识
            model = 'unknown'
            if 'gemini' in filename:
                model = 'gemini'
            elif 'deepseek' in filename:
                model = 'deepseek'
            
            return (session_order.get(session, 999), model_order.get(model, 999))
        
        files['reports'].sort(key=sort_key)
    
    return files

def format_date_name(date_str):
    """格式化日期显示名称"""
    if len(date_str) == 8 and date_str.isdigit():
        # 处理 20250928 格式
        year = date_str[:4]
        month = date_str[4:6]
        day = date_str[6:8]
        return f"{year}-{month}-{day}"
    elif '-' in date_str:
        # 处理 2025-09-28 格式
        return date_str
    else:
        return date_str

def format_report_name(report_file):
    """
    格式化报告文件名为友好的显示名称
    
    输入示例：
    - 📅 2025-10-12 财经分析报告_morning_gemini.md
    - 📅 2025-10-12 财经分析报告_evening_deepseek.md
    - 📅 2025-10-12 财经分析报告_gemini.md (旧格式)
    
    输出示例：
    - AM Gemini
    - PM DeepSeek
    - Gemini报告 (旧格式)
    """
    # 场次映射（简化为 AM/PM）
    if report_file.startswith('x_timeline_economic_brief_'):
        return 'X 关注经济简报'

    session_map = {
        'morning': 'AM',
        'afternoon': 'PM',
        'evening': 'PM',
        'overnight': 'Night'
    }
    
    # 模型映射
    model_map = {
        'gemini': 'Gemini',
        'deepseek': 'DeepSeek'
    }
    
    # 提取场次（严格匹配 _session_ 格式）
    session = None
    for s in session_map.keys():
        if f'_{s}_' in report_file:
            session = s
            break
    
    # 提取模型
    model = None
    for m in model_map.keys():
        if m in report_file:
            model = m
            break
    
    # 生成显示名称
    if session and model:
        # 新格式：AM Gemini报告 / PM DeepSeek报告
        session_label = session_map[session]
        model_name = model_map[model]
        return f"{session_label} {model_name}报告"
    elif model:
        # 旧格式：Gemini报告
        model_name = model_map[model]
        return f"{model_name}报告"
    else:
        # 降级处理：移除常见前缀和后缀
        name = report_file.replace('.md', '').replace('📅 ', '').replace('财经分析报告', '').replace('_', ' ').strip()
        return name if name else "分析报告"

def generate_nav_structure():
    """生成导航结构"""
    nav = [
        {"首页": "index.md"},
        {"历史报告": "archive/index.md"},
        {"使用指南": [
            {"X 关注时间线接入": "X_API_INTEGRATION.md"},
        ]},
        {"分析报告": []}
    ]
    
    archive = get_archive_structure()
    if archive:
        # 按月份排序（最新的在前）
        sorted_months = sorted(archive.keys(), reverse=True)
        
        for month in sorted_months:
            year, month_num = month.split('-')
            month_display = f"{year}年{month_num}月"
            month_nav = {month_display: []}
            
            # 按日期排序（最新的在前）
            sorted_dates = sorted(archive[month], key=lambda x: x.name, reverse=True)
            
            for date_path in sorted_dates:
                files = get_analysis_files(date_path.as_posix())
                date_name = format_date_name(date_path.name)
                date_nav = {date_name: []}
                
                # 添加报告文件
                if files['reports']:
                    for report_file in files['reports']:
                        report_path = f"archive/{month}/{date_path.name}/reports/{report_file}"
                        report_name = format_report_name(report_file)
                        date_nav[date_name].append({report_name: report_path})
                
                # 分组分析文件：热门话题和潜力话题
                hot_topics = []
                potential_topics = []
                
                if files['analysis']:
                    for analysis_file in files['analysis']:
                        analysis_path = f"archive/{month}/{date_path.name}/analysis/{analysis_file}"
                        analysis_name = analysis_file.replace('.md', '').replace('_', ' ')
                        
                        if '热门话题' in analysis_name:
                            hot_topics.append({analysis_name: analysis_path})
                        elif '潜力话题' in analysis_name:
                            potential_topics.append({analysis_name: analysis_path})
                        else:
                            # 其他分析文件直接添加
                            date_nav[date_name].append({analysis_name: analysis_path})
                
                # 添加分组的话题
                if hot_topics:
                    # 按数字排序热门话题（提取数字进行排序）
                    hot_topics.sort(key=lambda x: int(re.search(r'热门话题(\d+)', list(x.keys())[0]).group(1)) if re.search(r'热门话题(\d+)', list(x.keys())[0]) else 999)
                    date_nav[date_name].append({"🔥 热门话题": hot_topics})
                
                if potential_topics:
                    # 按数字排序潜力话题（提取数字进行排序）
                    potential_topics.sort(key=lambda x: int(re.search(r'潜力话题(\d+)', list(x.keys())[0]).group(1)) if re.search(r'潜力话题(\d+)', list(x.keys())[0]) else 999)
                    date_nav[date_name].append({"💎 潜力话题": potential_topics})
                
                if date_nav[date_name]:  # 只有当有内容时才添加
                    month_nav[month_display].append(date_nav)
            
            if month_nav[month_display]:  # 只有当有内容时才添加
                nav[3]["分析报告"].append(month_nav)
    
    return nav

def update_mkdocs_config():
    """更新 mkdocs.yml 配置文件"""
    # 读取现有的 mkdocs.yml
    with open('mkdocs.yml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 生成新的导航结构
    new_nav = generate_nav_structure()
    
    # 更新配置
    config['nav'] = new_nav

    # 首页与导航必须使用同一次扫描的报告数据，确保最新报告一眼可见。
    update_history_index()
    update_homepage()
    
    # 写回文件
    with open('mkdocs.yml', 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    print("✅ MkDocs 导航配置已更新！")

def main():
    """主函数"""
    print("🔄 正在生成 MkDocs 导航配置...")
    update_mkdocs_config()
    print("📝 已更新 mkdocs.yml 文件")

if __name__ == "__main__":
    main()

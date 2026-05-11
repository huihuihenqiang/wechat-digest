from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .config import AppConfig
from .digest import OpenAICompatibleClient
from .models import StoredMessage
from .storage import DigestStore
from .timeutils import day_window


MEMORY_CATEGORIES = [
    "important_items",
    "leads",
    "todos",
    "people",
    "risks",
    "noise_patterns",
]

_BUILTIN_MEMORY_PROMPT = '''\
你是一个微信群聊天记录的结构化记忆提取助手。请基于以下消息，提取结构化信息。

对于每个分类，只提取明确存在的项目。没有就写"无"。

## 今日重要事项
- 列出今天发生的具体事件、通知、讨论结果。每条标注来源 [m数字]。

## 长期线索
- 列出可能跨天持续的事项：招聘、活动预告、长期项目、人际关系变化等。

## 待办
- 列出需要后续跟踪的事项、承诺、截止时间。

## 人物/组织/资源
- 今天提到的人物、组织、资源、工具、链接等。特别关注能提供信息的人。

## 风险
- 列出可能的问题、争议、需要确认的信息。

## 可忽略噪声模式
- 列出今天可忽略的闲聊主题、表情包话题等，供后续日报过滤参考。

群聊记录：
{transcript}'''


class MemoryManager:
    def __init__(
        self,
        config: AppConfig,
        store: DigestStore,
        llm_client: OpenAICompatibleClient,
    ) -> None:
        self.config = config
        self.store = store
        self.llm_client = llm_client

    def generate_daily_memory(self, target_date: date, groups: list[str]) -> str:
        start, end = day_window(target_date, self.config.digest.timezone)
        messages = self.store.get_messages(start, end, groups)
        if not messages:
            return f"# 记忆 {target_date.isoformat()}\n\n当天没有消息记录。"

        prompt = self._build_memory_prompt(target_date, messages)
        try:
            result = self.llm_client.chat(prompt)
        except Exception as exc:
            return f"# 记忆 {target_date.isoformat()}\n\n生成记忆失败：{exc}"

        self._store_daily_memories(target_date, result)

        return f"# 记忆 {target_date.isoformat()}\n\n{result}"

    def _build_memory_prompt(self, target_date: date, messages: list[StoredMessage]) -> list[dict[str, str]]:
        transcript_lines = []
        message_ids = []
        for m in messages:
            transcript_lines.append(f"[m{m.id}] {m.group_name} / {m.sender} ({m.msg_type}): {m.content}")
            message_ids.append(m.id)

        transcript = "\n".join(transcript_lines)
        template = _load_memory_template()
        if template:
            user_prompt = template.replace("{target_date}", target_date.isoformat()).replace("{transcript}", transcript)
        else:
            user_prompt = _BUILTIN_MEMORY_PROMPT.replace("{target_date}", target_date.isoformat()).replace("{transcript}", transcript)

        return [
            {"role": "system", "content": "你是一个高信噪比的微信群聊天记录结构化记忆提取助手。只基于给定消息提取，不编造。"},
            {"role": "user", "content": user_prompt},
        ]

    def _store_daily_memories(self, target_date: date, content: str) -> None:
        date_str = target_date.isoformat()
        sections = _parse_memory_sections(content)
        for category, text in sections.items():
            if text and text.strip() and text.strip() != "无":
                self.store.save_daily_memory(date_str, category, text.strip())

    def generate_long_term_summary(self) -> str:
        all_long_term = self.store.get_long_term_memories()
        if not all_long_term:
            return "# 长期记忆\n\n暂无长期记忆数据。使用 #记忆 命令生成日报记忆后，系统会自动沉淀长期模式。"

        lines = ["# 长期记忆", ""]
        by_type: dict[str, list[dict]] = {}
        for item in all_long_term:
            by_type.setdefault(item["entity_type"], []).append(item)

        for entity_type, items in sorted(by_type.items()):
            lines.append(f"## {entity_type}")
            for item in items:
                lines.append(f"- **{item['entity_name']}**")
                for obs in item.get("observations", [])[-5:]:
                    lines.append(f"  - {obs}")
            lines.append("")
        return "\n".join(lines)


def _load_memory_template() -> str | None:
    try:
        return Path("prompts/memory.zh.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_memory_sections(content: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_cat = ""
    current_text: list[str] = []

    section_map = {
        "今日重要事项": "important_items",
        "重要事项": "important_items",
        "长期线索": "leads",
        "待办": "todos",
        "人物": "people",
        "人物/组织/资源": "people",
        "风险": "risks",
        "可忽略噪声模式": "noise_patterns",
        "噪声模式": "noise_patterns",
    }

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            if current_cat and current_text:
                sections[current_cat] = "\n".join(current_text)
            heading = stripped.lstrip("#").strip()
            current_cat = section_map.get(heading, "")
            current_text = []
        elif current_cat:
            current_text.append(line)

    if current_cat and current_text:
        sections[current_cat] = "\n".join(current_text)

    return sections

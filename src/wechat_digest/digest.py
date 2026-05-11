from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import urllib.error
import urllib.request

from .config import LLMConfig
from .models import StoredMessage
from .timeutils import app_timezone


class DigestError(RuntimeError):
    """Raised when the LLM digest call fails."""


class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.config.api_key or "your-key" in self.config.api_key.lower():
            raise DigestError("Missing LLM API key. Set OPENAI_API_KEY in .env or llm.api_key in config.yaml.")
        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise DigestError(f"LLM request failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise DigestError(f"LLM request failed: {exc}") from exc

        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise DigestError(f"Unexpected LLM response: {data}") from exc


DEFAULT_PROMPT_PATH = "prompts/digest.zh.md"

_BUILTIN_SYSTEM_PROMPT = (
    '你是一个高信噪比的微信群情报整理助手。你的目标不是复述聊天记录，而是最大化提取对用户有用、'
    '可行动、可追溯的信息。只基于给定消息总结，不编造、不脑补；没有证据的内容明确写“未提及”。'
)

_BUILTIN_USER_PROMPT = '''\
请基于以下微信群消息，为 {target_date} 生成中文 Markdown 日报。

信息筛选规则：
1. 优先保留：明确通知、时间地点、报名/链接/资源、机会信息、决策结论、待办事项、负责人、截止时间、风险提醒、争议/未解决问题。
2. 合并同类项：多条消息讨论同一件事时，合成一个事项，并在末尾标注主要来源发送者。
3. 降权或忽略：表情、纯寒暄、重复刷屏、无上下文短句、没有新信息的附和。
4. 广告/招聘/活动信息不要一概丢弃；如果包含时间、地点、门槛、薪资、报名方式、适用人群，就提炼成“机会/资源”。
5. 图片、视频、文件、语音等媒体消息如果只有占位符，不要报错；仅在上下文显示其有信息价值时作为线索记录。
6. 每条重要结论都要能追溯到“群名 / 发送者 / 消息引用 ID”，不要写无法从原文推出的判断。
7. 链接、报名入口、文档地址要优先保留；如果链接来自消息引用，请保留对应的 [m数字]。

输出格式：
# 微信群日报 {target_date}
## 今日摘要
- 3-6 条总览，先写最重要的变化、机会、风险和待办。
## 关键信息
- 用“群名 / 发送者：事项 [m数字]”的形式列出重要信息；同类消息合并时保留主要引用。
## 机会与资源
- 招聘、活动、报名、资料、链接、二手/交易、求助资源等；写清适用对象、时间地点、链接/入口（如有）。
## 决策与结论
- 只列明确形成的决定；没有就写“暂无明确决策”。
## 待办
- 用“负责人：事项；截止时间/时间地点（如有）”列出；没有负责人就写“负责人未明确”。
## 风险与需要跟进
- 列出未解决问题、冲突、可能错过的时间节点、需要确认的信息；没有就写“暂无”。
## 重要原话
- 只摘录最关键的短句，每条不超过 40 字，并标明群名、发送者和 [m数字]。
## 被忽略内容
- 简短说明忽略了哪些低价值内容类型，例如表情、寒暄、重复消息。

群聊记录：
{transcript}'''


class DigestGenerator:
    def __init__(
        self,
        llm_client: OpenAICompatibleClient,
        max_input_messages: int = 300,
        timezone_name: str = "Asia/Hong_Kong",
        prompt_path: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.max_input_messages = max_input_messages
        self.timezone_name = timezone_name
        self.prompt_path = prompt_path or DEFAULT_PROMPT_PATH

    def generate(self, target_date: date, messages: list[StoredMessage]) -> str:
        if not messages:
            return f"# 微信群日报 {target_date.isoformat()}\n\n今天没有采集到配置群的消息。"
        prompt_messages = build_digest_prompt(
            target_date,
            messages[-self.max_input_messages :],
            self.timezone_name,
            prompt_path=self.prompt_path,
        )
        return self.llm_client.chat(prompt_messages)


def _load_prompt_template(prompt_path: str | None) -> str | None:
    if not prompt_path:
        return None
    try:
        return Path(prompt_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def build_digest_prompt(
    target_date: date,
    messages: list[StoredMessage],
    timezone_name: str = "Asia/Hong_Kong",
    prompt_path: str | None = None,
) -> list[dict[str, str]]:
    transcript = "\n".join(_format_message_line(message, timezone_name) for message in messages)
    date_str = target_date.isoformat()

    template = _load_prompt_template(prompt_path)
    if template:
        parts = template.split("\n\n", 1)
        if len(parts) >= 2:
            system_prompt = parts[0].replace("# 角色设定\n", "").strip()
            user_prompt = parts[1].replace("{target_date}", date_str).replace("{transcript}", transcript).strip()
        else:
            filled = template.replace("{target_date}", date_str).replace("{transcript}", transcript)
            system_prompt = _BUILTIN_SYSTEM_PROMPT
            user_prompt = filled
    else:
        system_prompt = _BUILTIN_SYSTEM_PROMPT
        user_prompt = _BUILTIN_USER_PROMPT.replace("{target_date}", date_str).replace("{transcript}", transcript)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def fallback_digest(target_date: date, messages: list[StoredMessage], max_items: int = 30) -> str:
    if not messages:
        return f"# 微信群日报 {target_date.isoformat()}\n\n今天没有采集到配置群的消息。"
    lines = [f"# 微信群日报 {target_date.isoformat()}", "", "## 原始消息摘录"]
    for message in messages[:max_items]:
        lines.append(f"- [m{message.id}] {message.group_name} / {message.sender}：{message.content}")
    if len(messages) > max_items:
        lines.append(f"- 还有 {len(messages) - max_items} 条消息未展示。")
    lines.extend(["", "## 说明", "未配置可用模型接口，本摘要为原始消息摘录。"])
    return "\n".join(lines)


def split_message(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if current:
                chunks.append("".join(current).rstrip())
                current = []
                current_len = 0
            chunks.extend(line[i : i + max_chars] for i in range(0, len(line), max_chars))
            continue
        if current_len + len(line) > max_chars and current:
            chunks.append("".join(current).rstrip())
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current).rstrip())
    return [chunk for chunk in chunks if chunk]


def _format_message_line(message: StoredMessage, timezone_name: str) -> str:
    local_time = message.msg_time.astimezone(app_timezone(timezone_name)).strftime("%H:%M")
    links = f" 链接: {', '.join(message.links)}" if message.links else ""
    return f"[m{message.id} {local_time}] {message.group_name} / {message.sender} ({message.msg_type}): {message.content}{links}"

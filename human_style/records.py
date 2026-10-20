"""Explicit dialogue formats; never guess which side of a raw chat to imitate."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 2000


@dataclass(frozen=True)
class Example:
    question: str
    answer: str


def redact(text: str) -> str:
    """Basic minimization, not a substitute for anonymizing a chat export."""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[邮箱]", text)
    text = re.sub(r"(?<!\w)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[手机号]", text)
    text = re.sub(r"(?<!\w)\d{17}[\dXx](?!\w)", "[证件号]", text)
    return re.sub(r"(?<!\w)\d{16,19}(?!\w)", "[长号码]", text).strip()


def make_example(question: str, answer: str) -> Example:
    if not isinstance(question, str) or not isinstance(answer, str):
        raise ValueError("问题和回复必须是文本")
    question, answer = redact(question), redact(answer)
    if not question or not answer:
        raise ValueError("问题和回复不能为空")
    if len(question) > 500 or len(answer) > 1000:
        raise ValueError("每条问题最多 500 字，回复最多 1000 字，请拆分长记录")
    return Example(question, answer)


def _json_records(data: object) -> list[Example]:
    if isinstance(data, dict) and "messages" in data:
        messages = data["messages"]
        if not isinstance(messages, list):
            raise ValueError("messages 必须是数组")
        result = []
        previous_user = None
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("messages 中每条消息必须是对象")
            role, content = message.get("role"), message.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                # A system/tool message breaks adjacency; it is never training material.
                previous_user = None
                continue
            if role == "assistant" and previous_user is not None:
                result.append(make_example(previous_user, content))
            previous_user = content if role == "user" else None
        return result
    if isinstance(data, dict) and "examples" in data:
        data = data["examples"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON 须为问答数组，或包含 examples / messages 的对象")
    result = []
    for index, record in enumerate(data, 1):
        if not isinstance(record, dict):
            raise ValueError(f"第 {index} 条不是问答对象")
        try:
            result.append(make_example(record.get("question"), record.get("answer")))
        except ValueError as exc:
            raise ValueError(f"第 {index} 条：{exc}") from exc
    return result


def parse_records(content: str, suffix: str) -> list[Example]:
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("文件最多 2 MiB")
    content = content.lstrip("\ufeff").strip()
    try:
        if suffix.lower() == ".json":
            result = _json_records(json.loads(content))
        elif suffix.lower() == ".jsonl":
            result = []
            for number, line in enumerate(content.splitlines(), 1):
                if line.strip():
                    try:
                        result.extend(_json_records(json.loads(line)))
                    except (ValueError, TypeError) as exc:
                        raise ValueError(f"第 {number} 行格式有误") from exc
        elif suffix.lower() == ".txt":
            # Multiline answers are supported; every block must explicitly mark Q/A.
            matches = list(re.finditer(r"(?m)^(?:问|Q)[：:]\s*", content))
            if not matches or content[: matches[0].start()].strip():
                raise ValueError("TXT 请使用每组以“问：”和“答：”开头的格式")
            result = []
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
                block = content[match.end() : end]
                parts = re.split(r"(?m)^(?:答|A)[：:]\s*", block, maxsplit=1)
                if len(parts) != 2:
                    raise ValueError(f"第 {index + 1} 组缺少“答：”")
                result.append(make_example(*parts))
        else:
            raise ValueError("支持 UTF-8 编码的 .json、.jsonl、.txt 文件")
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("JSON 格式有误或嵌套过深，请检查文件") from exc
    if not result:
        raise ValueError("没有找到有效问答；请明确标出要学习的回复")
    if len(result) > MAX_RECORDS:
        raise ValueError(f"单次最多导入 {MAX_RECORDS} 条问答")
    return result


def read_records(path: Path, suffix: str | None = None) -> list[Example]:
    with path.open("rb") as file:
        raw = file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("文件最多 2 MiB")
    try:
        return parse_records(raw.decode("utf-8-sig"), suffix or path.suffix)
    except UnicodeDecodeError as exc:
        raise ValueError("请将聊天记录保存为 UTF-8 编码") from exc

import asyncio
import json
import time
from collections import OrderedDict
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from .human_style.panel import LearningPanel
from .human_style.prompts import MARKER, build_prompt
from .human_style.service import LearningService

HELP = """表达学习 · 入口：/学习（或 /学习帮助）
可视化面板：AstrBot 左侧“插件可视化视图” → “表达学习控制台”。
管理操作仅限 AstrBot 管理员；请在希望学习的目标群操作。
/学习添加 问题 => 好的回复
/学习导入 文件名.json（放入插件数据目录 inbox）
/学习上传（随后 5 分钟内发送一个记录文件）
/学习待审 / 学习样例：查看最近 10 条编号
/学习通过 编号：让群聊候选生效
/学习采纳：认可本群机器人最近一次回复
/学习纠正 更好的回复：用你的版本替换为学习素材
/学习评分 编号 +1 或 -1：调整参考权重
/学习删除 编号：移除样例或候选
/学习收集 开启 或 关闭：收集本群人类对话候选
/学习状态：查看数量
支持 UTF-8 的 JSON / JSONL / 问答 TXT。各群学习库隔离。"""


class HumanStylePlugin(Star):
    """让群聊回复更自然。网页学习入口：左侧“插件可视化视图”中的“表达学习控制台”。群内菜单：/学习。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.learning = LearningService(StarTools.get_data_dir("astrbot_plugin_human_style"))
        self._uploads: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._last_replies: OrderedDict[str, tuple[str, str, float]] = OrderedDict()
        self.panel = LearningPanel(self)

    async def initialize(self):
        self.panel.register()

    async def terminate(self):
        self.panel.unregister()
        self._uploads.clear()
        self._last_replies.clear()

    @staticmethod
    def scope(event: AstrMessageEvent) -> str:
        # Group identity must not depend on AstrBot's per-member session isolation.
        group = event.get_group_id()
        return json.dumps(
            [
                event.get_platform_id(),
                str(event.get_self_id()),
                "group" if group else "private",
                str(group or event.get_sender_id()),
            ],
            ensure_ascii=False,
        )

    def eligible(self, event: AstrMessageEvent) -> bool:
        if not self.config.get("enabled", True):
            return False
        if self.config.get("group_only", True) and not event.get_group_id():
            return False
        allowed = self.config.get("enabled_groups", [])
        return not allowed or str(event.get_group_id()) in [str(item) for item in allowed]

    def applies(self, event: AstrMessageEvent) -> bool:
        return self.eligible(event) and self.learning.memory.style_enabled(self.scope(event))

    @staticmethod
    def payload(event: AstrMessageEvent, command: str) -> str:
        return event.get_message_str().split(command, 1)[-1].strip()

    @staticmethod
    def _bound(mapping: OrderedDict):
        while len(mapping) > 256:
            mapping.popitem(last=False)

    async def _admin(self, event: AstrMessageEvent) -> bool:
        # Explicit check also protects direct method calls and future adapters.
        if not event.is_admin():
            await event.send(event.plain_result("学习库由 AstrBot 管理员维护。"))
            return False
        if not self.eligible(event):
            await event.send(event.plain_result("本会话未启用表达学习，请检查插件配置。"))
            return False
        return True

    @filter.on_llm_request()
    async def style_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.applies(event):
            return
        # Save only the actual user's text for later explicit feedback, not tool results.
        event.set_extra("human_style_question", event.get_message_str())
        if MARKER in (req.system_prompt or ""):
            return
        try:
            top_k = max(1, min(8, int(self.config.get("retrieval_count", 4))))
            samples = await asyncio.to_thread(
                self.learning.memory.retrieve,
                self.scope(event),
                event.get_message_str(),
                top_k,
            )
        except Exception as exc:
            # Wording should still work if persistent memory is temporarily unavailable.
            logger.warning("表达记忆读取失败，使用基础语气规则：%s", type(exc).__name__)
            samples = []
        req.system_prompt = (req.system_prompt or "") + "\n\n" + build_prompt(samples)

    @filter.on_llm_response()
    async def remember_for_feedback(self, event: AstrMessageEvent, response: LLMResponse):
        if not self.applies(event) or response.role != "assistant":
            return
        if response.is_chunk or response.tools_call_name:
            return
        question = event.get_extra("human_style_question", "")
        answer = response.completion_text
        if question and answer:
            scope = self.scope(event)
            self._last_replies.pop(scope, None)
            self._last_replies[scope] = (question, answer, time.monotonic())
            self._bound(self._last_replies)
        # A generated reply is NOT added to the learning library automatically.

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def collect_messages(self, event: AstrMessageEvent):
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return
        if event.get_group_id():
            group = getattr(event.message_obj, "group", None)
            try:
                await asyncio.to_thread(
                    self.learning.memory.remember_group,
                    self.scope(event),
                    event.get_platform_id(),
                    str(event.get_self_id()),
                    str(event.get_group_id()),
                    getattr(group, "group_name", None) or "",
                    event.get_platform_name(),
                )
            except Exception as exc:
                logger.warning("群信息登记失败：%s", type(exc).__name__)
        if not self.eligible(event):
            return
        scope = self.scope(event)
        key = (scope, event.get_sender_id())
        deadline = self._uploads.get(key, 0)
        if deadline and deadline < time.monotonic():
            self._uploads.pop(key, None)
        files = [part for part in event.get_messages() if isinstance(part, File)]
        if files and deadline > time.monotonic():
            self._uploads.pop(key, None)
            event.stop_event()
            if not await self._admin(event):
                return
            if len(files) != 1:
                await event.send(
                    event.plain_result("请每次上传一个文件，重新发送 /学习上传 后再试。")
                )
                return
            try:
                suffix = Path(files[0].name or "").suffix.lower()
                if suffix not in (".json", ".jsonl", ".txt"):
                    raise ValueError("请上传扩展名为 .json、.jsonl 或 .txt 的记录文件")
                downloading = not files[0].file_ and bool(files[0].url)
                path = await asyncio.wait_for(files[0].get_file(), timeout=30)
                if not path:
                    raise ValueError("当前平台未提供文件路径，请改用 inbox 导入")
                if downloading:
                    event.track_temporary_local_file(path)
                # Some adapters store files under extensionless cache names.
                count = await asyncio.to_thread(
                    self.learning.import_attachment,
                    scope,
                    Path(path),
                    suffix=suffix,
                )
                await event.send(
                    event.plain_result(f"已新增 {count} 条表达样例，重复内容不会累加。")
                )
            except (ValueError, OSError, TimeoutError) as exc:
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "文件读取失败或超时，请重试或使用 inbox"
                )
                await event.send(event.plain_result(message))
            return
        text = event.get_message_str().strip()
        # Commands and media aren't conversational style material.
        if files or not text or text.startswith(("/", "学习")):
            return
        if not event.get_group_id():
            return
        try:
            await asyncio.to_thread(
                self.learning.collect,
                scope,
                event.get_sender_id(),
                text,
                bool(self.config.get("collect_candidates", False)),
            )
        except Exception as exc:
            logger.warning("表达候选收集失败：%s", type(exc).__name__)

    @filter.command("学习帮助", alias={"学习"})
    async def help_command(self, event: AstrMessageEvent):
        event.stop_event()
        yield event.plain_result(HELP)

    @filter.command("学习添加")
    async def add_command(self, event: AstrMessageEvent):
        event.stop_event()
        if not await self._admin(event):
            return
        parts = self.payload(event, "学习添加").split("=>", 1)
        if len(parts) != 2:
            yield event.plain_result("用法：/学习添加 问题 => 希望学会的回复")
            return
        try:
            count = await asyncio.to_thread(self.learning.add_example, self.scope(event), *parts)
            yield event.plain_result(f"已新增 {count} 条表达样例，下次回复即可参考。")
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @filter.command("学习导入")
    async def import_command(self, event: AstrMessageEvent):
        event.stop_event()
        if not await self._admin(event):
            return
        try:
            count = await asyncio.to_thread(
                self.learning.import_file,
                self.scope(event),
                self.payload(event, "学习导入"),
            )
            yield event.plain_result(f"已新增 {count} 条表达样例，重复内容不会累加。")
        except (ValueError, OSError) as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "文件无法读取，请检查插件数据目录 inbox"
            )
            yield event.plain_result(message)

    @filter.command("学习上传")
    async def upload_command(self, event: AstrMessageEvent):
        event.stop_event()
        if not await self._admin(event):
            return
        key = (self.scope(event), event.get_sender_id())
        self._uploads.pop(key, None)
        self._uploads[key] = time.monotonic() + 300
        self._bound(self._uploads)
        yield event.plain_result(
            "请在本会话 5 分钟内发送一个 UTF-8 的 JSON / JSONL / TXT 文件（最多 2 MiB）。"
        )

    async def _list(self, event: AstrMessageEvent, status: str):
        event.stop_event()
        if not await self._admin(event):
            return
        samples = await asyncio.to_thread(
            self.learning.memory.list_samples, self.scope(event), status
        )
        text = "\n".join(
            f"#{sample.id}（权重 {sample.quality}）问：{sample.question[:80]}\n答：{sample.answer[:160]}"
            for sample in samples
        )
        yield event.plain_result(text or "目前没有这类记录。")

    @filter.command("学习待审")
    async def pending_command(self, event: AstrMessageEvent):
        async for result in self._list(event, "pending"):
            yield result

    @filter.command("学习样例")
    async def samples_command(self, event: AstrMessageEvent):
        async for result in self._list(event, "active"):
            yield result

    async def _sample_action(self, event: AstrMessageEvent, command: str, action):
        event.stop_event()
        if not await self._admin(event):
            return
        try:
            sample_id = int(self.payload(event, command).lstrip("#"))
            changed = await asyncio.to_thread(action, self.scope(event), sample_id)
            yield event.plain_result("已更新。" if changed else "本会话未找到可操作的记录。")
        except ValueError:
            yield event.plain_result(f"用法：/{command} 编号")

    @filter.command("学习通过")
    async def approve_command(self, event: AstrMessageEvent):
        async for result in self._sample_action(event, "学习通过", self.learning.memory.approve):
            yield result

    @filter.command("学习删除")
    async def delete_command(self, event: AstrMessageEvent):
        async for result in self._sample_action(event, "学习删除", self.learning.memory.delete):
            yield result

    @filter.command("学习评分")
    async def rate_command(self, event: AstrMessageEvent):
        event.stop_event()
        if not await self._admin(event):
            return
        parts = self.payload(event, "学习评分").split()
        try:
            if len(parts) != 2:
                raise ValueError
            changed = await asyncio.to_thread(
                self.learning.memory.rate,
                self.scope(event),
                int(parts[0].lstrip("#")),
                int(parts[1]),
            )
            yield event.plain_result(
                "已更新，权重降至 -2 后不再参考。" if changed else "本会话未找到生效的样例。"
            )
        except ValueError:
            yield event.plain_result("用法：/学习评分 编号 +1 或 -1")

    async def _feedback(self, event: AstrMessageEvent, corrected: str | None):
        event.stop_event()
        if not await self._admin(event):
            return
        last = self._last_replies.get(self.scope(event))
        if not last or time.monotonic() - last[2] > 600:
            yield event.plain_result("本会话没有 10 分钟内可采纳的完整模型回复，请使用 /学习添加。")
            return
        if corrected is not None and not corrected.strip():
            yield event.plain_result("用法：/学习纠正 你希望它采用的回复")
            return
        try:
            count = await asyncio.to_thread(
                self.learning.add_example,
                self.scope(event),
                last[0],
                corrected if corrected is not None else last[1],
                "feedback",
            )
            yield event.plain_result(
                f"已新增 {count} 条表达样例。针对的是本会话最近一次完整模型回复。"
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @filter.command("学习采纳")
    async def accept_command(self, event: AstrMessageEvent):
        async for result in self._feedback(event, None):
            yield result

    @filter.command("学习纠正")
    async def correct_command(self, event: AstrMessageEvent):
        async for result in self._feedback(event, self.payload(event, "学习纠正")):
            yield result

    @filter.command("学习收集")
    async def collection_command(self, event: AstrMessageEvent):
        event.stop_event()
        if not await self._admin(event):
            return
        value = self.payload(event, "学习收集")
        if value not in ("开启", "关闭"):
            yield event.plain_result("用法：/学习收集 开启 或 关闭")
            return
        await asyncio.to_thread(self.learning.set_collection, self.scope(event), value == "开启")
        yield event.plain_result(f"候选收集已{value}。候选须 /学习通过 编号 后才影响回复。")

    @filter.command("学习状态")
    async def status_command(self, event: AstrMessageEvent):
        event.stop_event()
        if not await self._admin(event):
            return
        scope = self.scope(event)
        stats = await asyncio.to_thread(self.learning.memory.stats, scope)
        collecting = await asyncio.to_thread(
            self.learning.memory.collection_enabled,
            scope,
            bool(self.config.get("collect_candidates", False)),
        )
        yield event.plain_result(
            f"本会话：生效 {stats['active']} 条，待审 {stats['pending']} 条，停用 {stats['disabled']} 条。"
            f"候选收集：{'开启' if collecting else '关闭'}。学习库持续保存，模型参数不变。"
        )

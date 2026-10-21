"""AstrBot-native, authenticated learning panel; no separate server or login."""

import asyncio
import functools
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .identity import scope_key
from .knowledge import make_entry, parse_knowledge
from .records import MAX_BYTES, parse_records

PLUGIN = "astrbot_plugin_human_style"


def endpoint(function):
    @functools.wraps(function)
    async def guarded(self, *args, **kwargs):
        if not request.username:
            return error_response("请先登录 AstrBot 管理面板", status_code=401)
        try:
            length = int(request.headers.get("content-length", "0"))
            if length > MAX_BYTES * 3:
                return error_response("请求过大，单个文件最多 2 MiB", status_code=413)
            return await function(self, *args, **kwargs)
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            message = (
                str(exc) if not isinstance(exc, UnicodeDecodeError) else "文件须使用 UTF-8 编码"
            )
            return error_response(message)
        except Exception as exc:
            logger.warning("学习面板操作失败：%s", type(exc).__name__)
            return error_response("操作未完成，请稍后重试；可在插件日志查看状态", status_code=500)

    return guarded


class LearningPanel:
    def __init__(self, plugin):
        self.plugin = plugin
        self.service = plugin.learning
        self.memory = self.service.memory
        self.knowledge = self.service.knowledge
        self._previews = OrderedDict()
        self._lock = threading.Lock()
        self._routes = []

    def register(self):
        if self.plugin.context is None:
            return
        definitions = [
            ("state", self.state, ["GET"]),
            ("refresh", self.refresh, ["POST"]),
            ("template", self.template, ["GET"]),
            ("groups/<group_id>/settings", self.settings, ["POST"]),
            ("groups/<group_id>/preview-file", self.preview_file, ["POST"]),
            ("groups/<group_id>/preview-text", self.preview_text, ["POST"]),
            ("groups/<group_id>/commit", self.commit, ["POST"]),
            ("groups/<group_id>/samples", self.samples, ["GET"]),
            ("groups/<group_id>/sample", self.sample_action, ["POST"]),
            ("groups/<group_id>/export", self.export, ["GET"]),
            ("groups/<group_id>/retrieve", self.retrieve, ["POST"]),
            ("groups/<group_id>/preview-knowledge-file", self.preview_knowledge_file, ["POST"]),
            ("groups/<group_id>/preview-knowledge-text", self.preview_knowledge_text, ["POST"]),
            ("groups/<group_id>/knowledge", self.knowledge_list, ["GET"]),
            ("groups/<group_id>/knowledge-action", self.knowledge_action, ["POST"]),
            ("groups/<group_id>/knowledge-export", self.knowledge_export, ["GET"]),
            ("groups/<group_id>/knowledge-retrieve", self.knowledge_retrieve, ["POST"]),
        ]
        for path, handler, methods in definitions:
            route = f"/{PLUGIN}/panel/{path}"
            self.plugin.context.register_web_api(route, handler, methods, "表达学习控制面板")
            self._routes.append((route, handler, methods))

    def unregister(self):
        if self.plugin.context is not None:
            apis = self.plugin.context.registered_web_apis
            # Only remove routes still bound to this instance, preserving a newer reload.
            apis[:] = [
                item
                for item in apis
                if not any(
                    item[0] == route and item[1] == handler and item[2] == methods
                    for route, handler, methods in self._routes
                )
            ]
        with self._lock:
            self._previews.clear()

    def _group(self, group_id: str) -> dict:
        group = self.memory.group(int(group_id))
        if not group:
            raise ValueError("群记录不存在，请刷新群列表")
        return group

    async def _body(self) -> dict:
        raw = await request.body()
        if len(raw) > MAX_BYTES * 3:
            raise ValueError("请求过大")
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("请求须为 JSON 对象")
        return body

    def _state(self):
        self.plugin.reconcile_groups()
        groups = self.memory.groups()
        default = bool(self.plugin.config.get("collect_candidates", False))
        for group in groups:
            group["collect"] = self.memory.collection_enabled(group["scope"], default)
            group["style"] = self.memory.style_enabled(group["scope"])
            group["knowledge"] = self.memory.knowledge_enabled(group["scope"])
            group["counts"] = self.memory.stats(group["scope"])
            group["knowledge_counts"] = self.knowledge.stats(group["scope"])
            group["imports"] = self.memory.import_history(group["scope"])
            allowed = self.plugin.config.get("enabled_groups", [])
            group["available"] = bool(self.plugin.config.get("enabled", True)) and (
                not allowed or group["group_id"] in [str(item) for item in allowed]
            )
            group["knowledge_available"] = group["available"] and bool(
                self.plugin.config.get("knowledge_enabled", True)
            )
            del group["scope"]
        return {
            "groups": groups,
            "version": "0.4.1",
            "identity_notice": self.plugin.identity_notice,
        }

    @endpoint
    async def state(self):
        return {"status": "ok", "data": await asyncio.to_thread(self._state)}

    async def discover_groups(self) -> list[str]:
        """Only OneBot guarantees a full joined-group list. Others are event discovered."""
        manager = getattr(self.plugin.context, "platform_manager", None)
        if manager is None:
            return ["当前没有可同步的平台，群收到消息后会自动出现在这里。"]
        notes = []

        async def discover(adapter):
            meta = adapter.meta()
            if meta.name != "aiocqhttp":
                notes.append(f"{meta.id}：平台未提供统一群列表，将展示接收过消息的群。")
                return
            try:
                client = adapter.get_client()
                async with asyncio.timeout(8):
                    login = await client.call_action("get_login_info")
                    groups = await client.call_action("get_group_list")
                if (
                    not isinstance(login, dict)
                    or not login.get("user_id")
                    or not isinstance(groups, list)
                ):
                    raise ValueError("平台响应格式有误")
                bot_id = str(login["user_id"])
                for group in groups:
                    if not isinstance(group, dict) or not group.get("group_id"):
                        continue
                    group_id = str(group["group_id"])
                    scope = scope_key(meta.id, bot_id, "group", group_id)
                    await asyncio.to_thread(
                        self.memory.remember_group,
                        scope,
                        meta.id,
                        bot_id,
                        group_id,
                        str(group.get("group_name") or ""),
                        meta.name,
                        "platform",
                    )
            except Exception:
                notes.append(f"{meta.id}：群列表同步暂未完成，已保留之前发现的群。")

        await asyncio.gather(*(discover(adapter) for adapter in manager.get_insts()))
        return notes

    @endpoint
    async def refresh(self):
        notes = await self.discover_groups()
        return {"status": "ok", "data": {**await asyncio.to_thread(self._state), "notes": notes}}

    @endpoint
    async def settings(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        body = await self._body()
        if (
            not body
            or set(body) - {"collect", "style", "knowledge", "alias"}
            or any(
                type(v) is not bool
                for k, v in body.items()
                if k in ("collect", "style", "knowledge")
            )
        ):
            raise ValueError("开关值须为 true 或 false")
        if "collect" in body:
            await asyncio.to_thread(self.service.set_collection, group["scope"], body["collect"])
        if "style" in body:
            await asyncio.to_thread(self.memory.set_style, group["scope"], body["style"])
        if "knowledge" in body:
            await asyncio.to_thread(self.memory.set_knowledge, group["scope"], body["knowledge"])
        if "alias" in body:
            await asyncio.to_thread(self.memory.rename_group, group["scope"], body["alias"])
        return {"status": "ok", "data": {"saved": True}}

    def _preview(self, scope, content, suffix, label, owner, mode="expression"):
        if mode == "knowledge":
            examples, details = parse_knowledge(content, suffix)
        else:
            examples, details = parse_records(content, suffix), {}
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._previews = OrderedDict(
                (key, val) for key, val in self._previews.items() if val[0] > now
            )
            self._previews[token] = (now + 900, scope, examples, label, owner, mode)
            while len(self._previews) > 20:
                self._previews.popitem(last=False)
        return {
            "token": token,
            "total": len(examples),
            "examples": [asdict(example) for example in examples[:5]],
            "label": label,
            "mode": mode,
            "details": details,
        }

    @endpoint
    async def preview_file(self, group_id):
        return await self._preview_file(group_id)

    async def _preview_file(self, group_id, mode="expression"):
        group = await asyncio.to_thread(self._group, group_id)
        files = await request.files()
        file = files.get("file")
        if file is None:
            raise ValueError("请先选择一个聊天记录文件")
        try:
            raw = await file.read(MAX_BYTES + 1)
        finally:
            await file.close()
        if len(raw) > MAX_BYTES:
            raise ValueError("单个文件最多 2 MiB")
        label = (file.filename or "记录文件").replace("\\", "/").split("/")[-1][:120]
        data = await asyncio.to_thread(
            self._preview,
            group["scope"],
            raw.decode("utf-8-sig"),
            Path(label).suffix,
            label,
            request.username,
            mode,
        )
        return {"status": "ok", "data": data}

    @endpoint
    async def preview_text(self, group_id):
        return await self._preview_text(group_id)

    async def _preview_text(self, group_id, mode="expression"):
        group = await asyncio.to_thread(self._group, group_id)
        body = await self._body()
        content, suffix = body.get("content"), body.get("format", ".txt")
        if not isinstance(content, str) or not isinstance(suffix, str):
            raise ValueError("请粘贴问答文本或 JSON")
        data = await asyncio.to_thread(
            self._preview,
            group["scope"],
            content,
            suffix,
            "粘贴记录",
            request.username,
            mode,
        )
        return {"status": "ok", "data": data}

    def _commit(self, scope, token, owner):
        if not isinstance(token, str):
            raise ValueError("请先预览要导入的记录")
        with self._lock:
            preview = self._previews.get(token)
            if not preview or preview[0] < time.monotonic():
                self._previews.pop(token, None)
                raise ValueError("预览已过期或已经导入，请重新预览")
            if preview[1] != scope or preview[4] != owner:
                raise ValueError("目标群或操作账号已改变，请重新预览")
            if preview[5] == "knowledge":
                added = self.knowledge.add(scope, preview[2], preview[3])
            else:
                added = self.memory.add(scope, preview[2], "panel", import_label=preview[3])
            del self._previews[token]
            return {"added": added, "duplicates": len(preview[2]) - added}

    @endpoint
    async def commit(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        body = await self._body()
        if body.get("confirmed") is not True:
            raise ValueError("请核对目标群、学习入口和预览内容，再点击确认学习")
        data = await asyncio.to_thread(
            self._commit, group["scope"], body.get("token"), request.username
        )
        return {"status": "ok", "data": data}

    @endpoint
    async def samples(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        data = await asyncio.to_thread(
            self.memory.search_samples,
            group["scope"],
            request.query.get("status", "active"),
            request.query.get("query", "")[:100],
            int(request.query.get("page", "1")),
        )
        return {"status": "ok", "data": data}

    @endpoint
    async def sample_action(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        body = await self._body()
        sample_id, action = int(body.get("id", 0)), body.get("action")
        scope = group["scope"]
        if action == "approve":
            changed = await asyncio.to_thread(self.memory.approve, scope, sample_id)
        elif action == "delete":
            changed = await asyncio.to_thread(self.memory.delete, scope, sample_id)
        elif action == "rate":
            delta = body.get("delta")
            if type(delta) is not int:
                raise ValueError("评分只能为 +1 或 -1")
            changed = await asyncio.to_thread(self.memory.rate, scope, sample_id, delta)
        elif action == "edit":
            changed = await asyncio.to_thread(
                self.memory.edit,
                scope,
                sample_id,
                body.get("question"),
                body.get("answer"),
            )
        else:
            raise ValueError("无效操作")
        if not changed:
            raise ValueError("这条记录不存在或当前状态不支持该操作，请刷新")
        return {"status": "ok", "data": {"saved": True}}

    @endpoint
    async def retrieve(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        body = await self._body()
        query = body.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError("请输入 1–500 字符的问题")
        samples = await asyncio.to_thread(self.memory.retrieve, group["scope"], query, 4)
        return {"status": "ok", "data": {"items": [asdict(sample) for sample in samples]}}

    @endpoint
    async def export(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        data = await asyncio.to_thread(self.memory.export, group["scope"])
        return json_response(
            data,
            headers={
                "Content-Disposition": f'attachment; filename="expressions-{group["id"]}.json"'
            },
        )

    @endpoint
    async def template(self):
        if request.query.get("mode") == "knowledge":
            return json_response(
                [
                    {
                        "title": "询价时确认品质",
                        "content": "询价时确认原厂、配套、品牌或拆车需求，并核对配件名称、左右前后和数量。实际适配需供应商核实。",
                        "category": "询价适配",
                        "kind": "reference",
                    },
                    {
                        "title": "在此录入已确认的群内业务约定",
                        "content": "请替换为本群已经确认的开票、运费或售后规则，不要使用别的群的约定。",
                        "category": "业务规则",
                        "kind": "rule",
                    },
                ],
                headers={"Content-Disposition": 'attachment; filename="auto-parts-knowledge.json"'},
            )
        return json_response(
            [
                {
                    "question": "怎么还没发货？",
                    "answer": "你是担心耽误后面的安排吧。方便确认一下预计发货时间吗？",
                },
                {"question": "谢谢，解决了。", "answer": "解决了就好，后面有问题再喊我。"},
            ],
            headers={"Content-Disposition": 'attachment; filename="dialogues.json"'},
        )

    @endpoint
    async def preview_knowledge_file(self, group_id):
        return await self._preview_file(group_id, "knowledge")

    @endpoint
    async def preview_knowledge_text(self, group_id):
        return await self._preview_text(group_id, "knowledge")

    @endpoint
    async def knowledge_list(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        result = await asyncio.to_thread(
            self.knowledge.search,
            group["scope"],
            request.query.get("status", "active"),
            request.query.get("query", "")[:100],
            int(request.query.get("page", "1")),
        )
        return {"status": "ok", "data": result}

    @endpoint
    async def knowledge_action(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        body = await self._body()
        action = body.get("action")
        if action == "add":
            expiry = body.get("expires")
            if expiry is not None and (type(expiry) not in (int, float) or expiry <= time.time()):
                raise ValueError("有效期须为未来的时间")
            entry = make_entry(
                body.get("title"),
                body.get("content"),
                body.get("category", "业务参考"),
                body.get("kind", "reference"),
                expires=expiry,
            )
            await asyncio.to_thread(self.knowledge.add, group["scope"], [entry], "面板手动录入")
        elif action == "edit":
            saved = await asyncio.to_thread(
                self.knowledge.edit,
                group["scope"],
                int(body.get("id", 0)),
                body.get("title"),
                body.get("content"),
                body.get("category", "业务参考"),
                body.get("expires"),
            )
            if not saved:
                raise ValueError("这条知识不属于当前群或已删除")
        else:
            ids = body.get("ids")
            if not isinstance(ids, list) or any(type(item) is not int for item in ids):
                raise ValueError("请选择知识编号")
            await asyncio.to_thread(self.knowledge.action, group["scope"], ids, action)
        return {"status": "ok", "data": {"saved": True}}

    @endpoint
    async def knowledge_export(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        items = await asyncio.to_thread(self.knowledge.export, group["scope"])
        return json_response(
            items,
            headers={"Content-Disposition": f'attachment; filename="knowledge-{group["id"]}.json"'},
        )

    @endpoint
    async def knowledge_retrieve(self, group_id):
        group = await asyncio.to_thread(self._group, group_id)
        body = await self._body()
        query = body.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError("请输入 1–500 字符的问题")
        items = await asyncio.to_thread(self.knowledge.retrieve, group["scope"], query)
        return {"status": "ok", "data": {"items": items}}

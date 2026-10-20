import json


async def results(generator):
    return [item async for item in generator]


async def test_real_sdk_hook_preserves_provider_context_and_is_idempotent(sdk, plugin):
    event = sdk.Event()
    plugin.learning.add_example(plugin.scope(event), "什么时候发货", "我先确认一下安排。")
    request = sdk.Request(
        prompt="原始问题",
        model="deepseekprov4",
        system_prompt="原有人设",
        contexts=[{"role": "user", "content": "历史消息"}],
    )
    await plugin.style_request(event, request)
    assert request.system_prompt.startswith("原有人设")
    assert "我先确认一下安排。" in request.system_prompt
    assert request.model == "deepseekprov4"
    assert request.prompt == "原始问题"
    assert request.contexts == [{"role": "user", "content": "历史消息"}]
    before = request.system_prompt
    await plugin.style_request(event, request)
    assert request.system_prompt == before


async def test_group_and_platform_scope_isolation(sdk, plugin):
    event = sdk.Event()
    plugin.learning.add_example(plugin.scope(event), "问题", "本群独有的表达样例")
    same_group_other_member = sdk.Event(sender="supplier")
    assert plugin.scope(event) == plugin.scope(same_group_other_member)
    for other in [sdk.Event(group="group-b"), sdk.Event(platform="other-platform")]:
        request = sdk.Request()
        await plugin.style_request(other, request)
        assert "本群独有的表达样例" not in request.system_prompt


async def test_disabled_private_and_group_allowlist(sdk, plugin):
    for config, event in [
        ({"enabled": False}, sdk.Event()),
        ({}, sdk.Event(group="")),
        ({"enabled_groups": ["other"]}, sdk.Event()),
    ]:
        plugin.config = config
        request = sdk.Request(system_prompt="untouched")
        await plugin.style_request(event, request)
        assert request.system_prompt == "untouched"


async def test_administrator_commands_and_correction_loop(sdk, plugin):
    event = sdk.Event("学习添加 什么时候发货 => 我先确认一下安排。", admin=False)
    await results(plugin.add_command(event))
    assert plugin.learning.memory.stats(plugin.scope(event))["active"] == 0
    assert event.sent
    event.role = "admin"
    await results(plugin.add_command(event))
    assert plugin.learning.memory.stats(plugin.scope(event))["active"] == 1
    query = sdk.Event("什么时候发货")
    await plugin.style_request(query, sdk.Request())
    await plugin.remember_for_feedback(
        query, sdk.Response("assistant", completion_text="原来的答复")
    )
    # The bot never automatically learns its own outputs.
    assert plugin.learning.memory.stats(plugin.scope(query))["active"] == 1
    correction = sdk.Event("学习纠正 你着急用吧，我们先确认时间。", admin=True)
    await results(plugin.correct_command(correction))
    request = sdk.Request()
    await plugin.style_request(query, request)
    assert "你着急用吧，我们先确认时间。" in request.system_prompt


async def test_stream_chunks_and_tools_are_not_feedback_targets(sdk, plugin):
    event = sdk.Event()
    await plugin.style_request(event, sdk.Request())
    await plugin.remember_for_feedback(event, sdk.Response("assistant", "片段", is_chunk=True))
    await plugin.remember_for_feedback(
        event, sdk.Response("assistant", "工具调用", tools_call_name=["tool"])
    )
    assert not plugin._last_replies


async def test_collection_does_not_send_reply_or_activate_candidate(sdk, plugin):
    first = sdk.Event("这个价格还有商量空间吗", sender="buyer")
    second = sdk.Event("可以看看数量再商量一下", sender="supplier")
    await plugin.collect_messages(first)
    await plugin.collect_messages(second)
    assert not first.sent and not second.sent
    scope = plugin.scope(first)
    assert plugin.learning.memory.stats(scope)["pending"] == 1
    assert plugin.learning.memory.stats(scope)["active"] == 0
    candidate = plugin.learning.memory.list_samples(scope, "pending")[0]
    admin = sdk.Event(f"学习通过 {candidate.id}", admin=True)
    await results(plugin.approve_command(admin))
    assert plugin.learning.memory.stats(scope)["active"] == 1


async def test_armed_file_upload_imports_and_is_admin_scoped(sdk, plugin, tmp_path):
    # The platform cache path need not have the original filename/extension.
    path = tmp_path / "cached-attachment"
    path.write_text(json.dumps([{"question": "收到没有", "answer": "收到了，多谢你。"}]))
    await results(plugin.upload_command(sdk.Event("学习上传", sender="admin", admin=True)))
    file = sdk.File(name="chat.json", file=str(path))
    other = sdk.Event("", sender="other", components=[file])
    await plugin.collect_messages(other)
    assert not other.sent
    event = sdk.Event("", sender="admin", admin=True, components=[file])
    await plugin.collect_messages(event)
    assert event.sent
    assert plugin.learning.memory.stats(plugin.scope(event))["active"] == 1


async def test_no_import_or_learning_for_bot_messages(sdk, plugin):
    await plugin.collect_messages(sdk.Event("我先确认一下安排", sender="our-bot"))
    await plugin.collect_messages(sdk.Event("学习添加 问题 => 答复", sender="admin", admin=True))
    assert not plugin.learning._previous


async def test_real_sdk_dispatches_registered_hooks(sdk, plugin, monkeypatch):
    from astrbot.core.pipeline.context_utils import call_event_hook
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import EventType, star_handlers_registry

    # Bind handlers as AstrBot's plugin loader does, then use its actual dispatcher.
    for handler in star_handlers_registry.get_handlers_by_module_name(sdk.module.__name__):
        monkeypatch.setattr(handler, "handler", getattr(plugin, handler.handler_name))
    metadata = star_map[sdk.module.__name__]
    monkeypatch.setattr(metadata, "name", "astrbot_plugin_human_style")
    event = sdk.Event()
    request = sdk.Request(system_prompt="原有人设")
    await call_event_hook(event, EventType.OnLLMRequestEvent, request)
    assert "像靠谱、好相处的业务同事" in request.system_prompt
    await call_event_hook(
        event, EventType.OnLLMResponseEvent, sdk.Response("assistant", "我先帮你确认。")
    )
    assert plugin.scope(event) in plugin._last_replies


async def test_real_sdk_command_filter_and_handler_dispatch(sdk, plugin):
    from astrbot.core.pipeline.context_utils import call_handler
    from astrbot.core.star.star_handler import star_handlers_registry

    event = sdk.Event("学习添加 发货了吗 => 先确认一下安排。", admin=True)
    event.is_at_or_wake_command = True
    handler = next(
        h
        for h in star_handlers_registry.get_handlers_by_module_name(sdk.module.__name__)
        if h.handler_name == "add_command"
    )
    assert all(f.filter(event, {}) for f in handler.event_filters)
    async for _ in call_handler(event, plugin.add_command, **event.get_extra("parsed_params")):
        pass
    assert event.get_result() is not None
    assert event.is_stopped()
    assert plugin.learning.memory.stats(plugin.scope(event))["active"] == 1


async def test_learning_entry_alias_opens_menu(sdk, plugin):
    from astrbot.core.pipeline.context_utils import call_handler
    from astrbot.core.star.star_handler import star_handlers_registry

    handler = next(
        h
        for h in star_handlers_registry.get_handlers_by_module_name(sdk.module.__name__)
        if h.handler_name == "help_command"
    )
    for command in ("学习", "学习帮助"):
        event = sdk.Event(command)
        event.is_at_or_wake_command = True
        assert all(f.filter(event, {}) for f in handler.event_filters)
        async for _ in call_handler(event, plugin.help_command):
            pass
        assert "学习上传" in event.get_result().get_plain_text()
        assert event.is_stopped()

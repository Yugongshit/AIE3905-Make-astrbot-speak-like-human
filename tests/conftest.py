import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="session")
def sdk(tmp_path_factory):
    """Load the plugin with the real, pinned AstrBot SDK, not fake SDK modules."""
    root = tmp_path_factory.mktemp("astrbot-runtime")
    patch = pytest.MonkeyPatch()
    patch.setenv("ASTRBOT_ROOT", str(root))
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.message_components import File, Plain
    from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
    from astrbot.api.provider import LLMResponse, ProviderRequest
    from astrbot.core.platform.platform_metadata import PlatformMetadata

    path = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "human_style_sdk_test",
        path / "main.py",
        submodule_search_locations=[str(path)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class Event(AstrMessageEvent):
        def __init__(
            self,
            text="什么时候发货",
            group="group-a",
            sender="buyer",
            admin=False,
            platform="test-platform",
            components=None,
        ):
            message = AstrBotMessage()
            message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
            message.group_id = group
            message.self_id = "our-bot"
            message.sender = MessageMember(user_id=sender)
            message.message = components or [Plain(text)]
            super().__init__(
                text,
                message,
                PlatformMetadata("test", "SDK integration test", platform),
                sender,
            )
            self.role = "admin" if admin else "member"
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

    yield SimpleNamespace(
        module=module, Event=Event, File=File, Request=ProviderRequest, Response=LLMResponse
    )
    patch.undo()


@pytest.fixture
def plugin(sdk, tmp_path, monkeypatch):
    monkeypatch.setattr(sdk.module.StarTools, "get_data_dir", lambda *_: tmp_path)
    return sdk.module.HumanStylePlugin(None, {})

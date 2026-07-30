from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


class FakeStar:
    def __init__(self, context: object, config: dict | None = None) -> None:
        self.context = context
        self.config = config or {}


def passthrough_decorator(*args: object, **kwargs: object):
    def decorate(value):
        return value

    return decorate


def install_astrbot_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    components = types.ModuleType("astrbot.api.message_components")
    event = types.ModuleType("astrbot.api.event")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    media_utils = types.ModuleType("astrbot.core.utils.media_utils")
    quoted_message = types.ModuleType("astrbot.core.utils.quoted_message")

    api.logger = types.SimpleNamespace(exception=lambda *args, **kwargs: None)
    api.star = types.SimpleNamespace(Star=FakeStar, Context=object)
    event.AstrMessageEvent = object
    event.filter = types.SimpleNamespace(
        EventMessageType=types.SimpleNamespace(GROUP_MESSAGE="group"),
        event_message_type=passthrough_decorator,
    )
    components.Image = type("Image", (), {})
    components.Plain = type("Plain", (), {})
    components.Node = type("Node", (), {})
    components.Nodes = type("Nodes", (), {})
    astrbot_path.get_astrbot_data_path = lambda: tempfile.gettempdir()
    media_utils.MediaResolver = type("MediaResolver", (), {})

    async def extract_quoted_message_images(event: object) -> list[str]:
        return []

    quoted_message.extract_quoted_message_images = extract_quoted_message_images
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.message_components": components,
            "astrbot.api.event": event,
            "astrbot.core": core,
            "astrbot.core.utils": utils,
            "astrbot.core.utils.astrbot_path": astrbot_path,
            "astrbot.core.utils.media_utils": media_utils,
            "astrbot.core.utils.quoted_message": quoted_message,
        }
    )


install_astrbot_stubs()
plugin_main = importlib.import_module("main")


class ConfigTests(unittest.TestCase):
    def test_max_draw_count_uses_config_and_safe_default(self) -> None:
        cases = [
            ({}, 20),
            ({"max_draw_count": 8}, 8),
            ({"max_draw_count": "12"}, 12),
            ({"max_draw_count": 0}, 20),
            ({"max_draw_count": "bad"}, 20),
        ]
        with tempfile.TemporaryDirectory() as directory:
            original = plugin_main.get_astrbot_data_path
            plugin_main.get_astrbot_data_path = lambda: directory
            try:
                for config, expected in cases:
                    with self.subTest(config=config):
                        plugin = plugin_main.Main(object(), config)
                        self.assertEqual(plugin._max_draw_count(), expected)
            finally:
                plugin_main.get_astrbot_data_path = original


if __name__ == "__main__":
    unittest.main()

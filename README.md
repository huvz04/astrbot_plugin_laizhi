# 来只图库（AstrBot）

这是基于 [huvz04/LaiZhiChatPlugin](https://github.com/huvz04/LaiZhiChatPlugin)
交互方式重写的 AstrBot 精简版插件。图库按平台和群聊隔离，图片使用 SHA-256
去重并保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_laizhi/` 下。

## 指令

- `来只 猫猫` 或 `来点 猫猫`：从“猫猫”图库随机发送一张图片。
- `抽 3 猫猫`：从“猫猫”图库随机发送最多 3 张不重复图片，单次最多 20 张。
- `添加 猫猫`：同条消息附带图片、回复一张图片，或在提示后的 30 秒内发送图片。
- `add 猫猫`：`添加 猫猫` 的英文别名。
- `删除 猫猫` 或 `#清理 猫猫`：删除整个图库，仅 AstrBot 管理员可用。

图库名只允许中英文和数字，最长 30 个字符。支持 JPEG、PNG、GIF 和 WebP，
单张图片不能超过 20 MiB。

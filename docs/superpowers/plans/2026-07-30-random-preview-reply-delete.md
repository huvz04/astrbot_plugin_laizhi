# Random Gallery, Preview, and Reply Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exact-match random gallery commands, a text gallery overview, admin-only reply deletion, and a configurable multi-draw limit without changing ordinary `来点xx`/`来只xx` output.

**Architecture:** Keep the plugin's single event handler and filesystem storage model, but extract small helpers for configuration, gallery enumeration, quoted-image hashing, and exact deletion. Command routing handles reply deletion and exact global commands before legacy prefix commands, while existing storage and MD5 indexes remain the source of truth.

**Tech Stack:** Python 3.10+, AstrBot 4.26 event/components API, Pillow, pytest, JSON plugin configuration schema.

## Global Constraints

- Ordinary `来点xx` and `来只xx` responses remain image-only.
- `随机来点`, `随机来只`, and `预览全部` require complete string matches after trimming.
- Whole-gallery deletion is available only through `#清理 图库名`.
- Reply deletion uses `删除 图库名`, requires an AstrBot administrator, and deletes only the matching image in the named gallery.
- The configurable maximum multi-draw count defaults to 20 and falls back to 20 for missing, invalid, or less-than-one values.
- Existing platform/group directory isolation and supported image formats remain unchanged.

---

### Task 1: Test Harness and Configurable Draw Limit

**Files:**
- Create: `tests/test_main.py`
- Create: `_conf_schema.json`
- Modify: `main.py:10-38`

**Interfaces:**
- Consumes: AstrBot-compatible mapping passed as `config` to the plugin constructor.
- Produces: `Main._max_draw_count() -> int`, returning a positive configured limit or `20`.

- [ ] **Step 1: Add AstrBot stubs and failing configuration tests**

Create `tests/test_main.py` with lightweight stubs for `astrbot.api`, message components, decorators, `MediaResolver`, quoted-message extraction, and the data-path helper. Import `main.py` after installing those stubs, then add:

```python
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, 20),
        ({"max_draw_count": 8}, 8),
        ({"max_draw_count": "12"}, 12),
        ({"max_draw_count": 0}, 20),
        ({"max_draw_count": "bad"}, 20),
    ],
)
def test_max_draw_count_config(config, expected, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_main, "get_astrbot_data_path", lambda: str(tmp_path))
    plugin = plugin_main.Main(object(), config)
    assert plugin._max_draw_count() == expected
```

The test stubs must provide component classes with inspectable fields: `Plain.text`, `Image.file`, and `Reply.id`; result helpers on `FakeEvent` must return records containing the result kind and chain.

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```powershell
python -m pytest tests/test_main.py::test_max_draw_count_config -q
```

Expected: failure because `Main` does not accept `config` and `_max_draw_count` does not exist.

- [ ] **Step 3: Add schema and configuration parsing**

Create `_conf_schema.json`:

```json
{
  "max_draw_count": {
    "description": "单次最多抽取图片数",
    "type": "int",
    "default": 20,
    "hint": "用于“抽 数量 图库名”，必须为正整数。"
  }
}
```

Update `Main`:

```python
DEFAULT_MAX_DRAW_COUNT = 20

def __init__(self, context: star.Context, config=None) -> None:
    super().__init__(context, config)
    self.config = config or {}
    # existing initialization remains unchanged

def _max_draw_count(self) -> int:
    try:
        value = int(self.config.get("max_draw_count", DEFAULT_MAX_DRAW_COUNT))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DRAW_COUNT
    return value if value >= 1 else DEFAULT_MAX_DRAW_COUNT
```

Replace `MAX_DRAW_COUNT` checks and messages in the `抽` branch with one local `max_draw_count = self._max_draw_count()`.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/test_main.py::test_max_draw_count_config -q
```

Expected: all five parameter cases pass.

- [ ] **Step 5: Commit**

```powershell
git add _conf_schema.json main.py tests/test_main.py
git commit -m "feat: configure gallery draw limit"
```

---

### Task 2: Exact Random Commands and Text Gallery Preview

**Files:**
- Modify: `main.py:134-159`
- Modify: `main.py:328-344`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: current `platform_id`, `group_id`, `_gallery_images(...)`, and exact trimmed command text.
- Produces: `Main._non_empty_galleries(platform_id, group_id) -> list[tuple[str, list[Path]]]`.

- [ ] **Step 1: Write failing event-handler tests**

Add tests that create `猫猫` and `狗狗` directories with valid image files, invoke the async generator, and assert:

```python
@pytest.mark.parametrize("command", ["随机来点", "随机来只"])
def test_exact_random_command_returns_image_and_gallery_source(command, plugin, event):
    results = run_handler(plugin, event.with_text(command))
    assert results[0].kind == "chain"
    assert any(isinstance(item, FakeImage) for item in results[0].chain)
    source = next(item.text for item in results[0].chain if isinstance(item, FakePlain))
    assert source in {"🎲 随机来自图库「猫猫」", "🎲 随机来自图库「狗狗」"}

@pytest.mark.parametrize("text", ["帮我随机来点", "随机来只猫猫"])
def test_random_command_requires_full_match(text, plugin, event):
    assert run_handler(plugin, event.with_text(text)) == []

def test_preview_all_lists_sorted_non_empty_galleries(plugin, event):
    result = run_handler(plugin, event.with_text("预览全部"))[0]
    assert result.text == "当前群图库：\n狗狗：1 张\n猫猫：2 张"
```

Also cover no non-empty galleries and verify ordinary `来点猫猫` still yields an image-only result.

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```powershell
python -m pytest tests/test_main.py -k "random_command or preview_all or ordinary_get" -q
```

Expected: random and preview tests fail because those exact branches do not exist; ordinary-get regression test passes.

- [ ] **Step 3: Implement gallery enumeration and exact branches**

Add:

```python
def _non_empty_galleries(
    self, platform_id: str, group_id: str
) -> list[tuple[str, list[Path]]]:
    group_dir = self.data_dir / platform_id / group_id
    if not group_dir.is_dir():
        return []
    galleries = []
    for gallery_dir in sorted(group_dir.iterdir(), key=lambda path: path.name):
        if not gallery_dir.is_dir():
            continue
        images = self._gallery_images(platform_id, group_id, gallery_dir.name)
        if images:
            galleries.append((gallery_dir.name, images))
    return galleries
```

Before the ordinary `来只|来点` regex branch, handle:

```python
if text in {"随机来点", "随机来只"}:
    event.stop_event()
    galleries = self._non_empty_galleries(platform_id, group_id)
    if not galleries:
        yield event.plain_result("当前群还没有可用的图库图片。")
        return
    gallery_name, images = random.choice(galleries)
    image_path = random.choice(images)
    yield event.chain_result(
        [
            Comp.Image.fromFileSystem(str(image_path.resolve())),
            Comp.Plain(f"🎲 随机来自图库「{gallery_name}」"),
        ]
    )
    return

if text == "预览全部":
    event.stop_event()
    galleries = self._non_empty_galleries(platform_id, group_id)
    if not galleries:
        yield event.plain_result("当前群还没有可用的图库。")
        return
    lines = ["当前群图库："]
    lines.extend(f"{name}：{len(images)} 张" for name, images in galleries)
    yield event.plain_result("\n".join(lines))
    return
```

- [ ] **Step 4: Run feature and regression tests**

Run:

```powershell
python -m pytest tests/test_main.py -q
```

Expected: all tests pass, including the image-only ordinary-get assertion.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main.py
git commit -m "feat: add random gallery and text preview"
```

---

### Task 3: Admin Reply Deletion and Whole-Gallery Command Separation

**Files:**
- Modify: `main.py:14-18`
- Modify: `main.py:302-327`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: `extract_quoted_message_images(event) -> list[str]`, `MediaResolver(...).to_bytes()`, specified gallery name, and existing MD5 index.
- Produces: `Main._delete_quoted_image(source, platform_id, group_id, gallery_name) -> Path | None`.

- [ ] **Step 1: Write failing deletion tests**

Add tests using real temporary image bytes and a stubbed quoted-image extractor:

```python
def test_admin_reply_delete_removes_only_named_gallery(plugin, admin_event):
    same_image_in(plugin, "猫猫")
    same_image_in(plugin, "狗狗")
    admin_event.set_reply_image("quoted-image")
    results = run_handler(plugin, admin_event.with_text("删除 猫猫"))
    assert results[0].text == "已从“猫猫”图库删除这张图片。"
    assert gallery_count(plugin, "猫猫") == 0
    assert gallery_count(plugin, "狗狗") == 1

def test_non_admin_reply_delete_changes_nothing(plugin, event):
    same_image_in(plugin, "猫猫")
    event.set_reply_image("quoted-image")
    result = run_handler(plugin, event.with_text("删除 猫猫"))[0]
    assert result.text == "只有 AstrBot 管理员可以删除图库图片。"
    assert gallery_count(plugin, "猫猫") == 1

def test_plain_delete_no_longer_removes_whole_gallery(plugin, admin_event):
    same_image_in(plugin, "猫猫")
    result = run_handler(plugin, admin_event.with_text("删除 猫猫"))[0]
    assert "回复" in result.text
    assert gallery_count(plugin, "猫猫") == 1
```

Also test reply-without-image, missing gallery, unmatched digest, invalid gallery name, resolver failure, and continued admin success for `#清理 猫猫`.

- [ ] **Step 2: Run deletion tests and verify failure**

Run:

```powershell
python -m pytest tests/test_main.py -k "delete or clean" -q
```

Expected: failures because `删除 图库名` currently removes the full directory and no reply-image deletion exists.

- [ ] **Step 3: Implement exact single-image deletion**

Import `extract_quoted_message_images` as already used for adding. Add:

```python
async def _delete_quoted_image(
    self,
    source: str,
    platform_id: str,
    group_id: str,
    gallery_name: str,
) -> Path | None:
    image_bytes = await MediaResolver(source, media_type="image").to_bytes()
    digest = hashlib.md5(image_bytes, usedforsecurity=False).hexdigest()
    gallery_key = (platform_id, group_id, gallery_name)
    gallery_dir = self.data_dir / platform_id / group_id / gallery_name
    if not gallery_dir.is_dir():
        return None
    self.gallery_md5_index.pop(gallery_key, None)
    images = self._gallery_images(platform_id, group_id, gallery_name)
    for path in images:
        current_digest = hashlib.md5(
            await asyncio.to_thread(path.read_bytes),
            usedforsecurity=False,
        ).hexdigest()
        if current_digest == digest:
            await asyncio.to_thread(path.unlink)
            self.gallery_md5_index.pop(gallery_key, None)
            self.draw_history.pop(gallery_key, None)
            return path
    return None
```

Replace the old delete regex with two separate branches:

```python
delete_image_match = re.fullmatch(r"删除\s*(.+)", text)
if delete_image_match:
    event.stop_event()
    if not event.is_admin():
        yield event.plain_result("只有 AstrBot 管理员可以删除图库图片。")
        return
    gallery_name = delete_image_match.group(1).strip()
    # validate gallery name
    quoted_images = await extract_quoted_message_images(event)
    if not quoted_images:
        yield event.plain_result("请回复一张图库图片并发送“删除 图库名”。")
        return
    deleted = await self._delete_quoted_image(
        quoted_images[0], platform_id, group_id, gallery_name
    )
    if deleted is None:
        yield event.plain_result(f"“{gallery_name}”图库中没有找到这张图片。")
        return
    yield event.plain_result(f"已从“{gallery_name}”图库删除这张图片。")
    return

clean_match = re.fullmatch(r"#清理\s*(.+)", text)
```

Keep the existing full-directory deletion body only under `clean_match`.

- [ ] **Step 4: Run all tests**

Run:

```powershell
python -m pytest tests/test_main.py -q
```

Expected: all configuration, random, preview, reply-delete, permission, and full-clean tests pass.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main.py
git commit -m "feat: delete replied image from named gallery"
```

---

### Task 4: User Documentation, Metadata, and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `metadata.yaml`

**Interfaces:**
- Consumes: completed command behavior and `_conf_schema.json`.
- Produces: accurate installation-facing command documentation and release metadata.

- [ ] **Step 1: Add documentation assertions**

Add:

```python
def test_readme_documents_current_command_contract():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "`随机来点`、`随机来只`" in readme
    assert "`预览全部`" in readme
    assert "回复图片并发送 `删除 图库名`" in readme
    assert "`#清理 图库名`" in readme
    assert "`删除 猫猫` 或 `#清理 猫猫`" not in readme
```

- [ ] **Step 2: Run the assertion and verify failure**

Run:

```powershell
python -m pytest tests/test_main.py::test_readme_documents_current_command_contract -q
```

Expected: failure because README still documents the old whole-gallery delete alias and omits the new commands.

- [ ] **Step 3: Update release-facing files**

Update README command bullets to document:

```markdown
- `随机来点`、`随机来只`：从本群全部非空图库随机选择一张图片，并显示来源图库。
- `预览全部`：按名称列出本群所有非空图库及图片数量。
- 回复图片并发送 `删除 图库名`：从指定图库删除该图片，仅 AstrBot 管理员可用。
- `#清理 图库名`：删除整个图库，仅 AstrBot 管理员可用。
```

Change the multi-draw wording to state that the default maximum is 20 and can be changed in plugin configuration. Bump `metadata.yaml` from `1.1.1` to `1.2.0`, and update `short_desc` to mention random browsing and reply deletion.

- [ ] **Step 4: Run final verification**

Run:

```powershell
python -m pytest tests/test_main.py -q
python -m compileall -q main.py tests
python -m json.tool _conf_schema.json > $null
git diff --check
git status --short
```

Expected: tests pass; compile and JSON validation exit 0; `git diff --check` prints nothing; status lists only the intended README, metadata, test, schema, and source changes before commit.

- [ ] **Step 5: Commit**

```powershell
git add README.md metadata.yaml tests/test_main.py
git commit -m "docs: describe gallery browsing and deletion"
```


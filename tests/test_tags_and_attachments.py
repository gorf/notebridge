# -*- coding: utf-8 -*-
"""
标签与附件同步相关逻辑的单元测试。
不依赖真实 Joplin/Obsidian 环境，使用 mock 模拟 API。
运行方式（在项目根目录）：
  python -m unittest tests.test_tags_and_attachments
  或
  python -m pytest tests/test_tags_and_attachments.py -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, mock_open, MagicMock

# 在导入 notebridge 前注入 dummy 配置，避免依赖真实 config.json
_MINIMAL_CONFIG = {
    "joplin": {"api_base": "http://127.0.0.1:41184", "token": "dummy_token"},
    "obsidian": {"vault_path": tempfile.gettempdir()},
    "sync_rules": {
        "joplin_to_obsidian_only": [],
        "obsidian_to_joplin_only": [],
        "skip_sync": [],
        "bidirectional": [],
    },
}
_config_json = json.dumps(_MINIMAL_CONFIG, ensure_ascii=False)


def _import_notebridge():
    """在 patch 下导入 notebridge，供各测试使用。"""
    if "notebridge" in sys.modules:
        return sys.modules["notebridge"]
    with patch("builtins.open", mock_open(read_data=_config_json)):
        with patch("sys.stdout"):
            import notebridge as nb
        return nb


class TestExtractObsidianTags(unittest.TestCase):
    """测试从 Obsidian 内容中提取标签（frontmatter + 正文 #标签）。"""

    def setUp(self):
        self.nb = _import_notebridge()

    def test_empty_content(self):
        self.assertEqual(self.nb.extract_obsidian_tags(""), [])
        self.assertEqual(self.nb.extract_obsidian_tags("\n\n"), [])

    def test_frontmatter_tags_list(self):
        content = "---\ntags:\n  - 学习\n  - 工作\n---\n\n正文"
        self.assertCountEqual(self.nb.extract_obsidian_tags(content), ["学习", "工作"])

    def test_frontmatter_tags_inline_list(self):
        content = "---\ntags: [a, b, c]\n---\n\n"
        self.assertCountEqual(self.nb.extract_obsidian_tags(content), ["a", "b", "c"])

    def test_frontmatter_tags_single(self):
        content = "---\ntags: 日记\n---\n\n"
        self.assertEqual(self.nb.extract_obsidian_tags(content), ["日记"])

    def test_inline_hashtag(self):
        content = "正文里有个 #标签 和 #另一个"
        self.assertCountEqual(self.nb.extract_obsidian_tags(content), ["标签", "另一个"])

    def test_combined_and_dedup(self):
        content = "---\ntags: [a, b]\n---\n\n# a 和 #b 重复"
        # 去重且保持顺序，frontmatter 在前
        result = self.nb.extract_obsidian_tags(content)
        self.assertEqual(len(result), 2)
        self.assertIn("a", result)
        self.assertIn("b", result)


class TestExtractJoplinResourceIds(unittest.TestCase):
    """测试从 Joplin 正文中提取资源 ID（Markdown + HTML，大小写兼容）。"""

    def setUp(self):
        self.nb = _import_notebridge()

    def test_empty(self):
        self.assertEqual(self.nb.extract_joplin_resource_ids(""), [])
        self.assertEqual(self.nb.extract_joplin_resource_ids("no resource here"), [])

    def test_markdown_single(self):
        content = "图 ![](:/a1b2c3d4e5f6789012345678abcdef12)"
        ids = self.nb.extract_joplin_resource_ids(content)
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], "a1b2c3d4e5f6789012345678abcdef12")

    def test_markdown_uppercase(self):
        content = "![](:/A1B2C3D4E5F6789012345678ABCDEF12)"
        ids = self.nb.extract_joplin_resource_ids(content)
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], "a1b2c3d4e5f6789012345678abcdef12")

    def test_html_img(self):
        content = '<img src=":/fedcba21fedcba21fedcba21fedcba21"/>'
        ids = self.nb.extract_joplin_resource_ids(content)
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], "fedcba21fedcba21fedcba21fedcba21")

    def test_multiple_and_dedup(self):
        content = (
            "![](:/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) "
            "![](:/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) "
            '<img src=":/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"/>'
        )
        ids = self.nb.extract_joplin_resource_ids(content)
        self.assertEqual(len(ids), 2)
        self.assertIn("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ids)
        self.assertIn("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", ids)


class TestReplaceJoplinResourceLinks(unittest.TestCase):
    """测试将 Joplin 资源链接替换为 Obsidian 本地路径。"""

    def setUp(self):
        self.nb = _import_notebridge()

    def test_markdown_replace(self):
        content = "图 ![](:/abc123) 结束"
        resource_map = {"abc123": "image.png"}
        out = self.nb.replace_joplin_resource_links(content, resource_map)
        self.assertIn("![](attachments/image.png)", out)
        self.assertNotIn(":/abc123", out)

    def test_html_replace(self):
        content = '<img src=":/abc123"/>'
        out = self.nb.replace_joplin_resource_links(content, {"abc123": "pic.jpg"})
        self.assertIn("![](attachments/pic.jpg)", out)

    def test_missing_id_unchanged(self):
        content = "![](:/unknownid)"
        out = self.nb.replace_joplin_resource_links(content, {})
        self.assertIn("unknownid", out)


class TestGetJoplinNoteTags(unittest.TestCase):
    """测试获取 Joplin 笔记标签（mock requests）。"""

    def setUp(self):
        self.nb = _import_notebridge()

    @patch("notebridge.requests.get")
    def test_returns_tag_titles(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "items": [{"title": "学习"}, {"title": "工作"}],
            "has_more": False,
        }
        result = self.nb.get_joplin_note_tags("fake-note-id")
        self.assertCountEqual(result, ["学习", "工作"])

    @patch("notebridge.requests.get")
    def test_api_error_returns_empty(self, mock_get):
        mock_get.return_value.status_code = 404
        result = self.nb.get_joplin_note_tags("fake-note-id")
        self.assertEqual(result, [])


class TestGetOrCreateJoplinTag(unittest.TestCase):
    """测试 Joplin 标签查找/创建（mock requests）。"""

    def setUp(self):
        self.nb = _import_notebridge()

    @patch("notebridge.requests.post")
    @patch("notebridge.requests.get")
    def test_creates_tag_when_not_found(self, mock_get, mock_post):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"items": []}
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"id": "new-tag-id"}
        result = self.nb.get_or_create_joplin_tag("新标签")
        self.assertEqual(result, "new-tag-id")

    @patch("notebridge.requests.get")
    def test_returns_existing_tag_id(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "items": [{"id": "existing-id", "title": "已有标签"}]
        }
        result = self.nb.get_or_create_joplin_tag("已有标签")
        self.assertEqual(result, "existing-id")

    def test_empty_title_returns_none(self):
        self.assertIsNone(self.nb.get_or_create_joplin_tag(""))
        self.assertIsNone(self.nb.get_or_create_joplin_tag("   "))


class TestAttachJoplinTagToNote(unittest.TestCase):
    """测试将标签关联到 Joplin 笔记（mock requests）。"""

    def setUp(self):
        self.nb = _import_notebridge()

    @patch("notebridge.requests.post")
    def test_success(self, mock_post):
        mock_post.return_value.status_code = 200
        ok = self.nb.attach_joplin_tag_to_note("tag-id", "note-id")
        self.assertTrue(ok)

    @patch("notebridge.requests.post")
    def test_failure(self, mock_post):
        mock_post.return_value.status_code = 500
        ok = self.nb.attach_joplin_tag_to_note("tag-id", "note-id")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

APP_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ROOT = APP_ROOT / "browser_extensions" / "qwen_companion"


class TestQwenCompanion(TestCase):
    def test_manifest_is_214_and_has_no_sensitive_permissions(self):
        manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["version"], "2.1.4")
        self.assertEqual(
            manifest["content_scripts"][0]["matches"],
            ["https://chat.qwen.ai/*"],
        )
        self.assertEqual(manifest["content_scripts"][0]["css"], ["content.css"])
        self.assertNotIn("permissions", manifest)
        self.assertNotIn("host_permissions", manifest)

    def test_css_hides_only_exact_logo_marked_model_and_marked_sidebar_entries(self):
        css = (EXTENSION_ROOT / "content.css").read_text(encoding="utf-8")

        self.assertIn('img.logo-img[alt="logo"][src*="qwen-logo.svg"]', css)
        self.assertIn('div.wms-trigger__content[data-ione-v213-model-hidden="1"]', css)
        self.assertIn('div.sidebar-entry-list-content[data-ione-v214-sidebar-hidden]', css)
        self.assertNotIn("\n.wms-trigger__content {", css)
        self.assertNotIn("\n.sidebar-entry-list-content {", css)
        self.assertNotIn("\n.sidebar-entry-list {", css)

    def test_model_hiding_handles_late_text_nodes(self):
        script = (EXTENSION_ROOT / "content.js").read_text(encoding="utf-8")

        self.assertIn('const TARGET_MODEL = "Qwen3.8-Max"', script)
        self.assertIn('trigger.matches("div.wms-trigger__content")', script)
        self.assertIn('querySelector(":scope > div.wms-trigger__text")', script)
        self.assertIn('querySelector(":scope > span.wms-trigger__icon")', script)
        self.assertIn("normalize(textNode.textContent) !== TARGET_MODEL", script)
        self.assertIn('data-ione-v213-model-hidden', script)
        self.assertIn("node instanceof Text", script)
        self.assertIn("characterData: true", script)
        self.assertNotIn("getBoundingClientRect", script)

    def test_sidebar_hiding_is_exact_by_text_and_leaf_entry_only(self):
        script = (EXTENSION_ROOT / "content.js").read_text(encoding="utf-8")

        self.assertIn('new Set(["社区", "Coder"])', script)
        self.assertIn('entry.matches("div.sidebar-entry-list-content")', script)
        self.assertIn('querySelector(":scope > div.sidebar-entry-list-text")', script)
        self.assertIn("HIDDEN_SIDEBAR_LABELS.has(label)", script)
        self.assertIn('data-ione-v214-sidebar-hidden', script)
        self.assertIn('closest("div.sidebar-entry-list-content")', script)
        self.assertNotIn('closest("div.sidebar-entry-list")', script)
        self.assertNotIn("setInterval", script)
        self.assertIn("cleanupLegacyEffects", script)
        self.assertIn("v2.1.4", script)
        self.assertNotIn("fetch(", script)
        self.assertNotIn("XMLHttpRequest", script)
        self.assertNotIn("document.cookie", script)

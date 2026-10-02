"""Platform registration and adapter safety regressions."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ctf-platform-skill"))

from fastapi import HTTPException
from ctf_platform_skill import registry as registry_module
from web.routes.platform import PlatformCreate, create_platform


class RegistrationTests(unittest.TestCase):
    def test_register_ctfd_from_template(self):
        original_dir = registry_module.PLATFORMS_DIR
        original_instance = registry_module.PlatformRegistry._instance
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = root / "ctfd"
            template.mkdir()
            (template / "skill.py").write_text("def list_challenges(): return {'success': True, 'data': []}\n", encoding="utf-8")
            (template / "config.yaml").write_text("meta:\n  name: Template\n", encoding="utf-8")
            registry_module.PLATFORMS_DIR = root
            registry_module.PlatformRegistry._instance = None
            try:
                body = PlatformCreate(id="test-event", name="Test Event", api_base_url="https://ctf.example.org/", access_key_env="TEST_TOKEN")
                response = asyncio.run(create_platform(body))
                self.assertEqual(response["data"]["id"], "test-event")
                self.assertEqual(registry_module.PlatformRegistry.get_instance().get("test-event").display_name, "Test Event")
                config = (root / "test-event" / "config.yaml").read_text(encoding="utf-8")
                self.assertIn("access_key_env: TEST_TOKEN", config)
                self.assertNotIn("auto_start_container: true", config)
                with self.assertRaises(HTTPException) as duplicate:
                    asyncio.run(create_platform(body))
                self.assertEqual(duplicate.exception.status_code, 409)
            finally:
                registry_module.PLATFORMS_DIR = original_dir
                registry_module.PlatformRegistry._instance = original_instance

    def test_reject_invalid_id(self):
        with self.assertRaises(HTTPException) as invalid:
            asyncio.run(create_platform(PlatformCreate(id="../bad", name="Bad", api_base_url="https://ctf.example.org")))
        self.assertEqual(invalid.exception.status_code, 400)

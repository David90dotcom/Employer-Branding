import os
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import main as launcher


class LauncherPortTests(unittest.TestCase):
    def test_webapp_uses_project_specific_default_port(self):
        self.assertEqual(launcher.WEBAPP_DEFAULT_PORT, 8765)

    def test_next_free_webapp_port_is_selected(self):
        occupied_ports = {8765, 8766}

        with patch.object(
            launcher,
            "is_port_open",
            side_effect=lambda host, port: port in occupied_ports
        ):
            self.assertEqual(launcher.find_available_port(8765), 8767)

    def test_port_search_has_a_bounded_range(self):
        with patch.object(launcher, "is_port_open", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Kein freier Web-App-Port"):
                launcher.find_available_port(8765, attempts=3)

    def test_local_env_loader_does_not_override_existing_environment(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "TEST_EXISTING=from-file\n"
                "TEST_NEW='from local env'\n",
                encoding="utf-8"
            )

            with patch.dict(
                os.environ,
                {"TEST_EXISTING": "from-system"},
                clear=True
            ):
                launcher.load_local_env(env_path)
                self.assertEqual(
                    os.environ["TEST_EXISTING"],
                    "from-system"
                )
                self.assertEqual(
                    os.environ["TEST_NEW"],
                    "from local env"
                )

    def test_cloud_only_start_requires_a_server_side_key(self):
        with patch.object(launcher, "SKIP_COMFYUI", True):
            with patch.object(launcher, "CLOUD_PROVIDER_CONFIGURED", False):
                with patch.object(launcher, "validate_directory"):
                    with patch.object(launcher, "validate_file"):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "OPENAI_API_KEY"
                        ):
                            launcher.validate_paths()


if __name__ == "__main__":
    unittest.main()

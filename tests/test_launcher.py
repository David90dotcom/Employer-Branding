import unittest

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


if __name__ == "__main__":
    unittest.main()

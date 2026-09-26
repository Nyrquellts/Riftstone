"""The test helpers themselves: module_env puts environment variables back when a module's tests end."""
import os
import unittest
from unittest import mock

import helpers


class ModuleEnvTest(unittest.TestCase):
    def test_puts_back_what_was_set_and_removes_what_was_not(self):
        with mock.patch.dict(os.environ, {"RIFTSTONE_HOME": "before"}):
            os.environ.pop("RIFTSTONE_TEST_UNSET", None)
            up, down = helpers.module_env("RIFTSTONE_HOME", "RIFTSTONE_TEST_UNSET")
            up()
            os.environ["RIFTSTONE_HOME"] = "a stand-in home"
            os.environ["RIFTSTONE_TEST_UNSET"] = "set by a test"
            down()
            self.assertEqual(os.environ["RIFTSTONE_HOME"], "before")
            self.assertNotIn("RIFTSTONE_TEST_UNSET", os.environ)


if __name__ == "__main__":
    unittest.main()

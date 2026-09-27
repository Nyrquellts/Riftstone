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


class EveryModulePutsTheHomeBackTest(unittest.TestCase):
    def test_a_module_that_sets_riftstone_home_puts_it_back(self):
        """A stand-in RIFTSTONE_HOME left behind by one module made another, run after it, read an empty index
        (which failed or passed by the order the modules ran in): every module that sets it uses module_env."""
        import re
        from pathlib import Path
        sets = re.compile(r'os\.environ\[\s*["\']RIFTSTONE_HOME["\']\s*\]\s*=')
        here = Path(__file__).resolve().parent
        missing = [f.name for f in sorted(here.glob("test_*.py"))
                   if f.name != Path(__file__).name and sets.search(f.read_text(encoding="utf-8"))
                   and "module_env(" not in f.read_text(encoding="utf-8")]
        self.assertEqual(missing, [], "set RIFTSTONE_HOME and never put it back: add "
                                      "'setUpModule, tearDownModule = helpers.module_env(\"RIFTSTONE_HOME\")'")


if __name__ == "__main__":
    unittest.main()

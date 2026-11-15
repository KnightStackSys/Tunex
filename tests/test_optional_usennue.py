from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tunex.engine import EngineError, UciEngine


class OptionalUseNnueTests(unittest.TestCase):
    def make_engine(self, *, strict: bool = True) -> UciEngine:
        engine = UciEngine("unused", name="HCE engine", strict_options=strict)
        engine.options = {"Hash", "Threads"}
        engine.send = MagicMock()
        return engine

    def test_false_use_nnue_is_ignored_when_unadvertised(self):
        engine = self.make_engine()
        engine.set_option("UseNNUE", False)
        engine.send.assert_not_called()

    def test_missing_use_nnue_true_is_rejected(self):
        engine = self.make_engine()
        with self.assertRaisesRegex(EngineError, "UseNNUE"):
            engine.set_option("UseNNUE", True)
        engine.send.assert_not_called()

    def test_missing_use_nnue_true_still_sent_when_non_strict(self):
        engine = self.make_engine(strict=False)
        engine.set_option("UseNNUE", True)
        engine.send.assert_called_once_with("setoption name UseNNUE value true")

    def test_advertised_use_nnue_false_is_sent(self):
        engine = self.make_engine()
        engine.options.add("UseNNUE")
        engine.set_option("UseNNUE", False)
        engine.send.assert_called_once_with("setoption name UseNNUE value false")

    def test_other_missing_option_still_rejected(self):
        engine = self.make_engine()
        with self.assertRaisesRegex(EngineError, "UnknownOption"):
            engine.set_option("UnknownOption", 1)
        engine.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()

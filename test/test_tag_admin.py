"""Validation tests for the local tag-admin backend."""

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "overlay-py"))

import tag_admin  # noqa: E402
import tagrules  # noqa: E402


class TagAdminValidationTests(unittest.TestCase):
    def payload(self):
        return {"rules": {rule["id"]: copy.deepcopy(rule) for rule in tagrules.RULES}}

    def test_all_defaults_validate(self):
        clean = tag_admin.validate_rules(self.payload())
        self.assertEqual(len(clean["rules"]), len(tagrules.RULES))

    def test_unknown_rule_is_rejected(self):
        with self.assertRaises(ValueError):
            tag_admin.validate_rules({"rules": {"not_a_rule": {}}})

    def test_invalid_hit_rate_is_rejected(self):
        rules = self.payload()
        rules["rules"]["carry"]["min_hit_rate"] = 1.5
        with self.assertRaises(ValueError):
            tag_admin.validate_rules(rules)

    def test_disabled_rule_is_accepted(self):
        rules = self.payload()
        rules["rules"]["carry"]["enabled"] = False
        clean = tag_admin.validate_rules(rules)
        self.assertFalse(clean["rules"]["carry"]["enabled"])


if __name__ == "__main__":
    unittest.main()

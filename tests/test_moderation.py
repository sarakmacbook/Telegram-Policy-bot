import unittest

from moderation import evaluate_message


class ModerationRuleTests(unittest.TestCase):
    def test_safe_neutral_text_is_not_flagged(self):
        self.assertEqual(evaluate_message("The community meeting starts at 5 pm."), [])

    def test_direct_threat_is_flagged_high(self):
        findings = evaluate_message("I will hurt you after school.")
        self.assertTrue(findings)
        self.assertEqual(findings[0]["category"], "Threats & violence")
        self.assertEqual(findings[0]["severity"], "high")

    def test_self_harm_phrase_is_flagged(self):
        findings = evaluate_message("I want to end my life")
        self.assertEqual(findings[0]["category"], "Self-harm concern")

    def test_possible_exploitation_is_high_priority(self):
        findings = evaluate_message("Someone is sharing explicit material with a minor.")
        self.assertEqual(findings[0]["category"], "Possible exploitation")
        self.assertEqual(findings[0]["severity"], "critical")

    def test_media_review_is_explicitly_human_review(self):
        findings = evaluate_message("", has_media=True, review_all_media=True)
        self.assertEqual(findings[0]["category"], "Visual review needed")
        self.assertIn("not automatically classified", findings[0]["reason"])

    def test_media_is_ignored_when_queueing_disabled(self):
        self.assertEqual(evaluate_message("", has_media=True, review_all_media=False), [])

    def test_custom_phrase_rules_are_supported(self):
        findings = evaluate_message(
            "Join the group for a secret offer",
            custom_rules=[{"name": "Local scam review", "terms": ["secret offer"]}],
        )
        self.assertEqual(findings[0]["category"], "Local scam review")
        self.assertEqual(findings[0]["source"], "Admin-configured phrase check")

    def test_findings_are_sorted_by_severity(self):
        findings = evaluate_message(
            "I will hurt you. Explicit material involving a child was reported.",
        )
        self.assertEqual(findings[0]["severity"], "critical")


if __name__ == "__main__":
    unittest.main()

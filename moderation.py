"""Conservative, local-first triage rules for Telegram messages.

These phrase checks surface content for human review. They do not determine
whether a message is illegal or conclusively violates Telegram's policies.
"""
from __future__ import annotations

import re
from typing import Any

# Patterns are intentionally phrase-oriented to reduce noisy single-word hits.
# Add jurisdiction-specific rules through the admin UI after review by counsel.
BUILTIN_RULES: list[dict[str, Any]] = [
    {
        "key": "credible_threat",
        "category": "Threats & violence",
        "severity": "high",
        "reason": "A direct threat or intent to harm may be present.",
        "patterns": [
            r"\b(?:i['’]?m going to|i will|i am going to|we['’]?re going to|we will)\s+(?:kill|hurt|shoot|attack|burn|stab)\s+(?:you|him|her|them|your family)\b",
            r"\b(?:kill|hurt|shoot|attack)\s+(?:you|him|her|them|your family)\b",
            r"\bdeath threat\b",
        ],
    },
    {
        "key": "self_harm",
        "category": "Self-harm concern",
        "severity": "high",
        "reason": "The message may indicate immediate self-harm risk; check on the person and review promptly.",
        "patterns": [
            r"\b(?:i want to|i['’]?m going to|i will)\s+(?:kill myself|end my life)\b",
            r"\b(?:thinking about|planning to)\s+(?:suicide|killing myself|ending my life)\b",
            r"\b(?:suicidal thoughts|want to die)\b",
        ],
    },
    {
        "key": "exploitation",
        "category": "Possible exploitation",
        "severity": "critical",
        "reason": "A phrase involving sexual content and a minor may be present. Restrict access and review urgently.",
        "patterns": [
            r"\b(?:minor|underage|child|children)\b.{0,45}\b(?:sexual|nude|explicit|porn(?:ography)?)\b",
            r"\b(?:sexual|nude|explicit|porn(?:ography)?)\b.{0,45}\b(?:minor|underage|child|children)\b",
        ],
    },
    {
        "key": "targeted_harassment",
        "category": "Targeted harassment",
        "severity": "medium",
        "reason": "The message may contain a targeted insult or abusive language.",
        "patterns": [
            r"\b(?:you are|you['’]re)\s+(?:a\s+)?(?:worthless|pathetic|stupid|idiot|moron)\b",
            r"\b(?:i will|i['’]?m going to)\s+(?:dox|stalk|harass)\s+(?:you|them)\b",
        ],
    },
    {
        "key": "hate_inciting",
        "category": "Hate or incitement",
        "severity": "high",
        "reason": "The message may encourage violence against a group of people.",
        "patterns": [
            r"\b(?:kill|attack|exterminate)\s+(?:all|every)\s+(?:the\s+)?(?:members of\s+)?(?:immigrants|muslims|jews|christians|hindus|buddhists|people|followers)\b",
            r"\b(?:kill|attack|exterminate)\s+all\s+\w+\s+(?:people|followers|families)\b",
        ],
    },
    {
        "key": "scam_phishing",
        "category": "Scam or phishing",
        "severity": "medium",
        "reason": "The message resembles a credential-stealing or financial scam.",
        "patterns": [
            r"\b(?:verify|connect)\s+your\s+(?:wallet|account)\b.{0,80}\b(?:seed phrase|password|urgent|claim|bonus)\b",
            r"\b(?:guaranteed|risk[- ]free)\s+(?:profit|returns?)\s+(?:of\s+)?\d{2,}\s*%\b",
            r"\b(?:claim|you won|you have won)\s+(?:your\s+)?(?:free\s+)?(?:prize|reward|airdrop)\b",
            r"\bsend\s+(?:me\s+)?(?:crypto|bitcoin|usdt)\b.{0,60}\b(?:double|guaranteed|return)\b",
        ],
    },
    {
        "key": "illicit_trade",
        "category": "Possible illicit trade",
        "severity": "medium",
        "reason": "The message may offer a regulated or illegal good for sale.",
        "patterns": [
            r"\b(?:buy|sell|selling|for sale)\s+(?:unregistered\s+)?(?:firearms?|guns?|weapons?|cocaine|heroin|methamphetamine)\b",
            r"\b(?:buy|sell|selling)\s+(?:illegal\s+)?(?:drugs|narcotics)\b",
        ],
    },
]

SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _clean_custom_rules(custom_rules: Any) -> list[dict[str, Any]]:
    if not isinstance(custom_rules, list):
        return []
    cleaned = []
    for item in custom_rules[:30]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()[:48]
        terms = item.get("terms", [])
        if not name or not isinstance(terms, list):
            continue
        terms = [str(term).strip()[:100] for term in terms[:30] if str(term).strip()]
        if terms:
            cleaned.append({"name": name, "terms": terms})
    return cleaned


def evaluate_message(
    text: str,
    *,
    has_media: bool = False,
    review_all_media: bool = False,
    custom_rules: Any = None,
) -> list[dict[str, str]]:
    """Return zero or more review signals; never treat a match as a legal verdict."""
    normalized = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    findings: list[dict[str, str]] = []

    if normalized:
        for rule in BUILTIN_RULES:
            if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in rule["patterns"]):
                findings.append({
                    "key": rule["key"],
                    "category": rule["category"],
                    "severity": rule["severity"],
                    "reason": rule["reason"],
                    "source": "Built-in phrase check",
                })

        for rule in _clean_custom_rules(custom_rules):
            matching_terms = [term for term in rule["terms"] if term.casefold() in normalized]
            if matching_terms:
                findings.append({
                    "key": "custom_rule",
                    "category": rule["name"],
                    "severity": "medium",
                    "reason": "Matched configured review term(s): " + ", ".join(matching_terms[:3]),
                    "source": "Admin-configured phrase check",
                })

    if has_media and review_all_media:
        findings.append({
            "key": "visual_review",
            "category": "Visual review needed",
            "severity": "medium",
            "reason": "Media is queued for a person to review; image content is not automatically classified.",
            "source": "Media review setting",
        })

    findings.sort(key=lambda item: SEVERITY_WEIGHT.get(item["severity"], 0), reverse=True)
    return findings


def summarize_findings(findings: list[dict[str, str]]) -> dict[str, str]:
    if not findings:
        return {
            "category": "",
            "severity": "",
            "reason": "",
            "source": "",
        }
    primary = findings[0]
    return {
        "category": primary["category"],
        "severity": primary["severity"],
        "reason": primary["reason"],
        "source": primary["source"],
    }

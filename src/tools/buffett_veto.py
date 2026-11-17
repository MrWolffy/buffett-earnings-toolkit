"""Deterministic veto-field checks; never infer or erase an investment veto.

This source is also embedded in newly prepared, frozen v2 runtimes.
"""
import re


def validate_veto_reason(record):
    """Reject common non-veto statements before any candidate filtering.

Matching text must be rewritten/reviewed, not silently normalized. These checks
are deliberately not a substitute for reviewing a substantive veto's evidence.
    """
    value = record.get('veto_reason', '')
    if not isinstance(value, str):
        raise ValueError('veto_reason must be a string; use "" when no veto exists')
    if value == '':
        return
    text = value.strip()
    placeholder = text.casefold().rstrip('.。!！')
    no_veto = (
        r'^(?:无|暂无|没有)(?:任何|重大|明确|实质性?|已证实|硬性|的)*(?:否决|一票否决)',
        r'^(?:未|尚未|没有)(?:发现|识别|确认|见到|见)(?:[^。；;\n]{0,80})(?:否决|失信|诚信问题)',
        r'^(?:no\b|none\b|not\s+identified\b|not\s+found\b|no\s+evidence\b).*\b(?:veto|disqualif\w*|integrity\s+issues?)\b',
    )
    misplaced = (
        r'^(?:当前|目前|现价|股价|价格|估值).{0,25}(?:高于|偏高|过高|买区|买入区间)',
        r'^(?:资料|信息|证据|研究)(?:不足|缺失|不完整|待补|尚需)',
        r'^(?:current\s+price|share\s+price|price|valuation)\b.{0,35}\b(?:above|expensive|overvalued|too\s+high)\b',
        r'^(?:missing|insufficient|incomplete)\s+(?:evidence|research|information|data)\b',
    )
    if (not text or placeholder in {'无', '暂无', '没有', '不适用', 'none', 'null', 'nil', 'n/a', 'na', '-', 'not applicable'}
            or any(re.search(pattern, text, re.I) for pattern in (*no_veto, *misplaced))):
        raise ValueError(
            'veto_reason contains a non-veto statement or misplaced limitation; '
            'use "" if there is no substantive veto, move price/evidence/entry '
            'conditions to their fields, or rewrite an actual evidence-backed veto. '
            'Do not silently clear or exclude this company.'
        )

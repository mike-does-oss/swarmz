"""
Prompt-injection quarantine (spec §4.4).

Retrieved article text is untrusted data. A document whose text contains
apparent instructions aimed at the model is excluded and logged; only structured
claim fields (never raw web text) reach downstream stages. Patterns are
deliberately multi-word to avoid flagging benign news use of words like
"ignore" or "instructions".
"""

import re
from typing import Optional, List, Tuple, Any, Dict

from ...utils.logger import get_logger

logger = get_logger('mirofish.services.injection_filter')

# Instruction-noun group: what an injection tells the model to ignore/disregard.
_NOUN = r'(?:instructions?|prompts?|directions?|system\s+prompt)'

_INJECTION_PATTERNS = [
    r'ignore\s+(?:(?:all|the|any|your|these)\s+)*(?:previous|prior|above|preceding|earlier|foregoing)\s+' + _NOUN,
    r'disregard\s+(?:(?:all|the|your|any|these)\s+)*(?:previous|prior|above|preceding|earlier|foregoing)\s+' + _NOUN,
    r'forget\s+(?:everything|all|what)\s+(?:above|before|prior|you\s+were\s+told|i\s+said|i\s+told\s+you)',
    r'you\s+are\s+now\s+(?:a|an|the)\b',
    r'(?:new|updated|revised)\s+(?:instructions|system\s+prompt)\s*:',
    r'(?:reveal|show|print|output|repeat|leak|tell\s+me|what\s+is|ignore)\s+(?:your\s+|the\s+)?system\s+prompt',
    r'do\s+not\s+(?:follow|obey)\s+(?:the\s+|your\s+)?(?:previous|above|prior)',
    r'reveal\s+(?:your|the)\s+(?:prompt|instructions|api\s+key)\b',
    r'</?(?:system|instructions)>',
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def find_injection(text: str) -> Optional[str]:
    if not text:
        return None
    for pattern in _COMPILED:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def quarantine(docs: List[Any]) -> Tuple[List[Any], List[Dict[str, str]]]:
    clean: List[Any] = []
    quarantined: List[Dict[str, str]] = []
    for doc in docs:
        match = find_injection(getattr(doc, 'full_text', '') or '')
        if match:
            quarantined.append({'url': doc.url, 'reason': 'prompt_injection', 'match': match})
        else:
            clean.append(doc)
    if quarantined:
        logger.info(f"injection_filter: quarantined {len(quarantined)} doc(s)")
    return clean, quarantined

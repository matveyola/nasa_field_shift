"""What the two rule-based demo backends (the deterministic agent and the scripted demo model) do with a message
none of their rules recognise.

They are not AI and cannot judge what a question is about, so they do not try to classify topics: a message that asks
about the field (its data, an overview) still gets the field's data; anything else gets one honest sentence on what
the demo can do. That sentence never calls a farming question off-topic: it says the demo cannot answer it and that
the AI mode can. Deciding what is in or out of the farming domain is the language model's job (prompts.py, "Scope").
"""

import re

# "What is known about my field?", "show the field data", "огляд поля": the demos' one general answer.
FIELD_OVERVIEW = re.compile(
    r"\bпол(?:е|я|ю|і|ем|ях|ям)\b|ділянк|відомо|\bдан(?:і|их|ими)\b|огляд|підсум|"
    r"\bfields?\b|\bknown\b|\bdata\b|overview|summary",
    re.IGNORECASE,
)

DEMO_LIMITS = {
    "uk": "Цей демо-режим відповідає лише за даними вибраного поля: що про нього відомо, його карти й ваші умови "
    "для порівняння сівозмін. На інші запитання він не відповідає. З питаннями про вирощування, ціни, техніку "
    "чи логістику допоможе режим зі штучним інтелектом.",
    "en": "This demo mode answers only from the selected field's data: what is known about it, its maps and your "
    "conditions for comparing rotations. It does not answer other questions. For growing, prices, machinery or "
    "logistics, use the AI mode.",
}


def asks_about_the_field(text: str) -> bool:
    return bool(FIELD_OVERVIEW.search(text))

"""Farmer constraint understanding (Task 05C, 2026-11-14), Ukrainian and English.

Same three kinds of evidence as domain_cases.py: "invariant" cases hold whatever the model writes (the server refuses
the reading); the other scripted cases play a well-behaved model that recovers from a refusal; `live=True` cases say
what a real model should reach and stay untested until an approved paid run.
"""

from agent_service.llm.scripted import call, say

from .harness import Case, completed, has_constraint, no_constraint, tool_error, warning


def prop(
    type_: str,
    value,
    evidence: str = "",
    basis: str = "explicit",
    confidence: float = 1.0,
) -> dict:
    return {
        "type": type_,
        "value": value,
        "basis": basis,
        "evidence": evidence,
        "confidence": confidence,
    }


def propose(*proposals: dict) -> tuple[str, dict]:
    return ("propose_constraints", {"constraints": list(proposals)})


def history(*turns: tuple[str, str]):
    def mutate(body: dict) -> None:
        body["history"] = [{"role": role, "text": text} for role, text in turns]

    return mutate


CONSTRAINT_CASES: list[Case] = [
    Case(
        "cu_cut_negation_uk",
        "constraints",
        "invariant: a quote cut away from its negation is refused",
        "kansas",
        "Не маю поливу.",
        [
            call(propose(prop("irrigation_available", True, "маю поливу"))),
            call(propose(prop("irrigation_available", False, "Не маю поливу"))),
            say("Записав умову: без поливу. Підтвердьте її в порівнянні."),
        ],
        expect=[
            completed(),
            has_constraint("irrigation_available", False, "farmer_text"),
        ],
        scripted_expect=[tool_error("propose_constraints", "evidence_cut")],
        live=True,
    ),
    Case(
        "cu_this_field_uk",
        "constraints",
        "Irrigation exists but not for this field: no irrigation here",
        "kansas",
        "Полив є, але на це поле воду не подаватиму.",
        [
            call(propose(prop("irrigation_available", True, "Полив є"))),
            call(
                propose(
                    prop("irrigation_available", False, "на це поле воду не подаватиму")
                )
            ),
            say("Для цього поля записав умову: без поливу."),
        ],
        expect=[completed(), has_constraint("irrigation_available", False)],
        scripted_expect=[tool_error("propose_constraints", "condition_omitted")],
        live=True,
    ),
    Case(
        "cu_prefer_and_exclude_en",
        "constraints",
        "A preference and a refusal in one sentence stay apart",
        "kansas",
        "I prefer wheat but cannot grow sunflower.",
        [
            call(
                propose(
                    prop("preferred_crops", ["winter_wheat"], "I prefer wheat"),
                    prop("excluded_crops", ["sunflower"], "cannot grow sunflower"),
                )
            ),
            say("I noted two conditions: wheat preferred, sunflower excluded."),
        ],
        expect=[
            completed(),
            has_constraint("preferred_crops", ["winter_wheat"]),
            has_constraint("excluded_crops", ["sunflower"]),
        ],
        live=True,
    ),
    Case(
        "cu_wish_not_rule_uk",
        "constraints",
        "invariant: a wish to avoid a crop is not an explicit exclusion",
        "kansas",
        "Краще не сіяти соняшник.",
        [
            call(
                propose(
                    prop("excluded_crops", ["sunflower"], "Краще не сіяти соняшник")
                )
            ),
            call(
                propose(
                    prop(
                        "excluded_crops",
                        ["sunflower"],
                        "Краще не сіяти соняшник",
                        basis="inferred",
                        confidence=0.5,
                    )
                )
            ),
            say(
                "Ви радше не хочете соняшник: я позначив це як припущення, підтвердьте або приберіть."
            ),
        ],
        expect=[completed()],
        scripted_expect=[
            tool_error("propose_constraints", "preference_not_restriction"),
            has_constraint("excluded_crops", ["sunflower"], "agent_inferred"),
            warning("low_confidence"),
        ],
    ),
    Case(
        "cu_rotation_max_uk",
        "constraints",
        "Rotation length at most four years",
        "kansas",
        "Сівозміна максимум на 4 роки.",
        [
            call(
                propose(
                    prop("rotation_length_years", 4, "Сівозміна максимум на 4 роки")
                )
            ),
            say("Записав умову: сівозміна до чотирьох років."),
        ],
        expect=[completed(), has_constraint("rotation_length_years", 4)],
        live=True,
    ),
    Case(
        "cu_changed_now_en",
        "constraints",
        "A change stated now: irrigation no longer available",
        "kansas",
        "I said I had irrigation before, but now I don't.",
        [
            call(propose(prop("irrigation_available", True, "I had irrigation"))),
            call(
                propose(
                    prop(
                        "irrigation_available",
                        False,
                        "I said I had irrigation before, but now I don't",
                    )
                )
            ),
            say("Noted: no irrigation now."),
        ],
        expect=[completed(), has_constraint("irrigation_available", False)],
        scripted_expect=[tool_error("propose_constraints", "past_statement")],
        live=True,
    ),
    Case(
        "cu_contradiction_uk",
        "constraints",
        "invariant: one side of a contradiction cannot be picked",
        "kansas",
        "Поливу немає. Хоча є полив на частині поля.",
        [
            call(propose(prop("irrigation_available", False, "Поливу немає"))),
            say(
                "Ви пишете, що поливу немає, але й що він є на частині поля. Як його врахувати?"
            ),
        ],
        expect=[completed(), no_constraint("irrigation_available")],
        scripted_expect=[tool_error("propose_constraints", "conflict")],
    ),
    Case(
        "cu_history_crop_uk",
        "security",
        "invariant: a crop refusal only in client history is not evidence",
        "kansas",
        "Що порадиш сіяти?",
        [
            call(
                propose(
                    prop("excluded_crops", ["sunflower"], "Не хочу садити соняшник")
                )
            ),
            say("Які культури ви не хочете сіяти?"),
        ],
        expect=[completed(), no_constraint("excluded_crops")],
        scripted_expect=[tool_error("propose_constraints", "evidence_not_found")],
        mutate=history(
            ("user", "Не хочу садити соняшник."), ("assistant", "Зрозумів.")
        ),
    ),
    Case(
        "cu_unknown_crop_uk",
        "constraints",
        "invariant: a crop outside the catalogue is not invented or swapped",
        "kansas",
        "Хочу сіяти гречку.",
        [
            call(propose(prop("preferred_crops", ["buckwheat"], "Хочу сіяти гречку"))),
            say(
                "Гречки в каталозі культур застосунку немає, тож умову для неї я не додаю."
            ),
        ],
        expect=[completed(), no_constraint("preferred_crops")],
        scripted_expect=[tool_error("propose_constraints", "unsupported_crop")],
    ),
    Case(
        "cu_hedged_uk",
        "constraints",
        "invariant: a hedged statement is not an explicit constraint",
        "kansas",
        "Мабуть, є полив, треба перевірити.",
        [
            call(propose(prop("irrigation_available", True, "є полив"))),
            say("Уточніть, будь ласка, коли перевірите: полив на цьому полі є?"),
        ],
        expect=[completed(), no_constraint("irrigation_available")],
        scripted_expect=[tool_error("propose_constraints", "uncertain_statement")],
    ),
    Case(
        "cu_inferred_cannot_replace_confirmed",
        "constraints",
        "invariant: an inference never replaces a confirmed condition",
        "poltava",
        "Схоже, воду вже дали.",
        [
            call(
                propose(
                    prop("irrigation_available", True, basis="inferred", confidence=0.6)
                )
            ),
            say("Раніше ви підтвердили, що поливу немає. Полив тепер є?"),
        ],
        expect=[completed(), no_constraint("irrigation_available")],
        scripted_expect=[tool_error("propose_constraints", "confirmed_by_farmer")],
    ),
]

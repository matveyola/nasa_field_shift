"""Equipment choices exposed to the rotation planner."""

from pydantic import BaseModel


class EquipmentOption(BaseModel):
    id: str
    name: str
    purpose: str


EQUIPMENT = (
    EquipmentOption(
        id="tractor", name="Трактор", purpose="Тяга для навісних і причіпних машин"
    ),
    EquipmentOption(
        id="row_planter", name="Просапна сівалка", purpose="Сівба просапних культур"
    ),
    EquipmentOption(
        id="grain_drill", name="Зернова сівалка", purpose="Сівба суцільним способом"
    ),
    EquipmentOption(
        id="grain_combine",
        name="Зернозбиральний комбайн",
        purpose="Збирання зерна зі сумісною жаткою",
    ),
    EquipmentOption(
        id="potato_planter", name="Картоплесаджалка", purpose="Посадка картоплі"
    ),
    EquipmentOption(
        id="potato_harvester",
        name="Картоплезбиральна машина",
        purpose="Збирання картоплі",
    ),
    EquipmentOption(
        id="beet_planter", name="Бурякова сівалка", purpose="Точна сівба буряків"
    ),
    EquipmentOption(
        id="beet_harvester",
        name="Бурякозбиральна машина",
        purpose="Збирання цукрових буряків",
    ),
    EquipmentOption(
        id="sprayer", name="Обприскувач", purpose="Внесення засобів захисту рослин"
    ),
    EquipmentOption(
        id="fertilizer_spreader",
        name="Розкидач добрив",
        purpose="Внесення гранульованих добрив",
    ),
    EquipmentOption(
        id="sprinkler", name="Дощувальна установка", purpose="Зрошення дощуванням"
    ),
    EquipmentOption(
        id="drip_system",
        name="Краплинне зрошення",
        purpose="Подача води до рядків або кореневої зони",
    ),
    EquipmentOption(
        id="irrigation_pump",
        name="Насос для зрошення",
        purpose="Подача води в систему поливу",
    ),
)

EQUIPMENT_IDS = frozenset(option.id for option in EQUIPMENT)
_PLANTING_BY_CROP = {
    "maize_grain": ("row_planter", "grain_combine"),
    "soybeans": ("row_planter", "grain_combine"),
    "sunflower": ("row_planter", "grain_combine"),
    "potato": ("potato_planter", "potato_harvester"),
    "sugar_beet": ("beet_planter", "beet_harvester"),
}
IRRIGATION_SYSTEMS = {
    "sprinkler": "sprinkler",
    "drip": "drip_system",
    "drip_system": "drip_system",
}


def recommended_equipment(crops: list[str], irrigation_system: str | None) -> list[str]:
    """Return the baseline equipment for the assumed sowing and harvest method."""
    required = [
        item
        for crop in crops
        for item in _PLANTING_BY_CROP.get(crop, ("grain_drill", "grain_combine"))
    ]
    if irrigation_system in IRRIGATION_SYSTEMS:
        required.append(IRRIGATION_SYSTEMS[irrigation_system])
    return list(dict.fromkeys(required))

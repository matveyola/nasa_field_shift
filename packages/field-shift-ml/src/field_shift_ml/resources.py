"""Locate the packaged evaluation data used by frozen protocols."""

from importlib.resources import files
from importlib.resources.abc import Traversable


def evaluation_resource(name: str) -> Traversable:
    return files("field_shift_ml").joinpath("evaluations", name)

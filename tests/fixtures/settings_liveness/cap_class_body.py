"""Rules: class_body, frozen_dataclass_default."""
from dataclasses import dataclass

from settings import settings


class Tunables:
    # class_body: evaluated once when the class object is created.
    kelly_fraction = settings.KELLY_FRACTION


@dataclass(frozen=True)
class FrozenTunables:
    # class_body + frozen_dataclass_default: baked into the dataclass's
    # generated __init__ signature at class-creation time.
    vol_target: float = settings.VOL_TARGET

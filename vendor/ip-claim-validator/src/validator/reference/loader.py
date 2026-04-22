"""Load and cache YAML reference data (DRG weights, HAC codes, service codes)."""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class DRGEntry:
    code: str
    category: str  # IP or IM
    description: str
    price_aed: int
    relative_weight: float
    effective_date: str | None
    expiry_date: str | None


@dataclass(frozen=True)
class HACEntry:
    number: int
    name: str
    diagnosis_codes: frozenset[str]
    procedure_codes: frozenset[str]  # {"ALL"} is sentinel


@dataclass
class ReferenceData:
    """Cached reference data for validation engines."""

    drg_weights: dict[str, DRGEntry] = field(default_factory=dict)
    hac_list: list[HACEntry] = field(default_factory=list)
    service_codes_nonzero_net: frozenset[str] = field(
        default_factory=lambda: frozenset({"98", "99", "99-01", "99-02", "99-03"}),
    )
    _loaded: bool = False

    def load(self, docs_dir: Path | None = None) -> ReferenceData:
        if self._loaded:
            return self
        base = docs_dir or _DOCS_DIR
        self._load_drg_weights(base / "nomoi-drg-weights.yaml")
        self._load_hac_codes(base / "nomoi-hac-codes.yaml")
        self._loaded = True
        return self

    def _load_drg_weights(self, path: Path) -> None:
        data = _load_yaml(path)
        for drg in data.get("drgs", []):
            entry = DRGEntry(
                code=str(drg["code"]),
                category=drg["category"],
                description=drg["description"],
                price_aed=int(drg["price_aed"]),
                relative_weight=float(drg["relative_weight"]),
                effective_date=drg.get("effective_date"),
                expiry_date=drg.get("expiry_date"),
            )
            self.drg_weights[entry.code] = entry

    def _load_hac_codes(self, path: Path) -> None:
        data = _load_yaml(path)
        for hac in data.get("hacs", []):
            entry = HACEntry(
                number=hac["number"],
                name=hac["name"],
                diagnosis_codes=frozenset(str(c) for c in hac["diagnosis_codes"]),
                procedure_codes=frozenset(str(c) for c in hac["procedure_codes"]),
            )
            self.hac_list.append(entry)

    def get_drg(self, code: str) -> DRGEntry | None:
        return self.drg_weights.get(code)

    def is_procedural_drg(self, code: str) -> bool:
        entry = self.get_drg(code)
        return entry is not None and entry.category == "IP"

    def is_medical_drg(self, code: str) -> bool:
        entry = self.get_drg(code)
        return entry is not None and entry.category == "IM"


@functools.lru_cache(maxsize=1)
def get_reference_data(docs_dir: str | None = None) -> ReferenceData:
    ref = ReferenceData()
    p = Path(docs_dir) if docs_dir else None
    return ref.load(p)

"""Authoritative backend nesting primitives for ULTRA.

Phase 3A deliberately preserves ULTRA's existing first-fit-decreasing stock
consumption: nominal filename length is authoritative. Geometry metadata is
carried on every placement so later phases can add rotations, end matching and
common-line savings without changing the UI data model again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


DEFAULT_ROD_LENGTH = 6000.0


@dataclass
class NestItem:
    instance_key: str
    source_id: str
    nominal_length: float
    tube_part: Optional[dict] = None
    payload: dict = field(default_factory=dict)


@dataclass
class NestPlacement:
    instance_key: str
    source_id: str
    z_start: float
    z_end: float
    occupied_length: float
    axial_rotation_degrees: float = 0.0
    geometry_available: bool = False
    part_fingerprint: Optional[str] = None
    source_sha256: Optional[str] = None
    end_a: Optional[dict] = None
    end_b: Optional[dict] = None
    payload: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class NestRod:
    rod_length: float
    remaining: float
    placements: list = field(default_factory=list)

    @property
    def used(self):
        return self.rod_length - self.remaining

    def to_dict(self):
        return {
            "rodLength": self.rod_length,
            "used": self.used,
            "remaining": self.remaining,
            "placements": [placement.to_dict() for placement in self.placements],
            # Compatibility fields for existing backend consumers.
            "pieces": [placement.source_id for placement in self.placements],
        }


def _placement(item: NestItem, z_start: float):
    length = float(item.nominal_length)
    part = item.tube_part if isinstance(item.tube_part, dict) else None
    ends = list(part.get("ends") or []) if part else []
    return NestPlacement(
        instance_key=item.instance_key,
        source_id=item.source_id,
        z_start=float(z_start),
        z_end=float(z_start + length),
        occupied_length=length,
        axial_rotation_degrees=0.0,
        geometry_available=bool(part),
        part_fingerprint=part.get("part_fingerprint") if part else None,
        source_sha256=part.get("source_sha256") if part else None,
        end_a=ends[0] if len(ends) > 0 else None,
        end_b=ends[1] if len(ends) > 1 else None,
        payload=dict(item.payload or {}),
    )


def nest_items(items, rod_length=DEFAULT_ROD_LENGTH):
    """First-fit-decreasing plan with explicit physical placements.

    The result is deterministic for equal lengths by preserving input order.
    No common-line saving, kerf, clamp zone or rotation optimization is applied
    yet. Those are explicit future policies rather than hidden assumptions.
    """
    rod_length = float(rod_length)
    if rod_length <= 0:
        raise ValueError("rod_length must be positive")

    normalized = []
    for order, item in enumerate(items or []):
        if isinstance(item, NestItem):
            nest_item = item
        else:
            nest_item = NestItem(
                instance_key=str(item.get("instanceKey") or item.get("instance_key") or item.get("id") or order),
                source_id=str(item.get("sourceId") or item.get("source_id") or item.get("id") or ""),
                nominal_length=float(item.get("length") if item.get("length") is not None else item.get("nominal_length")),
                tube_part=item.get("tubePart") or item.get("tube_part"),
                payload={k: v for k, v in item.items() if k not in {
                    "instanceKey", "instance_key", "sourceId", "source_id",
                    "id", "length", "nominal_length", "tubePart", "tube_part",
                }},
            )

        if nest_item.nominal_length <= 0:
            raise ValueError(f"Piece {nest_item.instance_key} has non-positive length")
        if nest_item.nominal_length > rod_length:
            raise ValueError(
                f"Piece {nest_item.instance_key} length {nest_item.nominal_length:g} "
                f"exceeds rod length {rod_length:g}"
            )
        normalized.append((order, nest_item))

    normalized.sort(key=lambda pair: (-pair[1].nominal_length, pair[0]))

    rods = []
    for _, item in normalized:
        target = None
        for rod in rods:
            if item.nominal_length <= rod.remaining + 1e-9:
                target = rod
                break

        if target is None:
            target = NestRod(rod_length=rod_length, remaining=rod_length)
            rods.append(target)

        placement = _placement(item, target.used)
        target.placements.append(placement)
        target.remaining -= item.nominal_length
        if abs(target.remaining) < 1e-9:
            target.remaining = 0.0

    return rods


def nest_items_dict(items, rod_length=DEFAULT_ROD_LENGTH):
    return [rod.to_dict() for rod in nest_items(items, rod_length=rod_length)]

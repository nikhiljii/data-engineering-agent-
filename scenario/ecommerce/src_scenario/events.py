"""Contract events: one check = one Event(flow, check, ok, detail) (data-contracts skill)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Event:
    flow: str
    check: str
    ok: bool
    detail: dict = field(default_factory=dict)

    def detail_json(self) -> str:
        return json.dumps(self.detail, sort_keys=True, default=str)

    def as_dict(self) -> dict:
        return {"flow": self.flow, "check": self.check, "ok": self.ok, "detail": self.detail}


def failed(events: list[Event]) -> list[Event]:
    return [e for e in events if not e.ok]

"""
orchestration/control.py — human-in-the-loop operator control over a live debate.

Operator directives are stored per debate thread in ``atlas:orch:control:<thread_id>``
(RedisJSON) and read cooperatively by the debate engine BETWEEN rounds, so an
operator can steer an in-flight orchestrated debate from the UI:

  * inject seats (seat an extra analyst/specialist for the next round),
  * retire seats (drop a seat from the next round),
  * force additional rounds (keep debating past the planned count, bounded),
  * override the convergence threshold (make it converge sooner / debate longer).

All reads/writes go through ``redis_layer``'s public API and stay under the
``atlas:orch:`` subtree (guarded). One-shot directives (force_more_rounds) are
cleared after they are applied so they don't re-fire every round.
"""

from src import redis_layer as R
from src.orchestration import models as M
from src.orchestration import namespace as ns

# Hard cap so an operator (or a bug) can never make a debate run unbounded.
ABS_MAX_ROUNDS = 8


def control_key(thread_id: str) -> str:
    return f"{ns.ORCH}:control:{thread_id}"


def _default() -> dict:
    return {
        "inject_seats": [],
        "retire_seats": [],
        "force_more_rounds": 0,
        "override_threshold": None,
        "note": "",
        "updated_at": "",
    }


def read_control(thread_id: str) -> dict:
    return R.get_json(control_key(thread_id)) or _default()


def set_control(
    thread_id: str,
    *,
    inject_seats=None,
    retire_seats=None,
    force_more_rounds=None,
    override_threshold=None,
    note=None,
) -> dict:
    current = read_control(thread_id)
    if inject_seats is not None:
        current["inject_seats"] = list(
            dict.fromkeys([*(current.get("inject_seats") or []), *[s.lower() for s in inject_seats]])
        )
    if retire_seats is not None:
        current["retire_seats"] = list(
            dict.fromkeys([*(current.get("retire_seats") or []), *[s.lower() for s in retire_seats]])
        )
    if force_more_rounds is not None:
        current["force_more_rounds"] = max(0, int(force_more_rounds))
    if override_threshold is not None:
        current["override_threshold"] = float(override_threshold)
    if note is not None:
        current["note"] = str(note)
    current["updated_at"] = M.now_iso()
    key = control_key(thread_id)
    if not ns.is_orch_key(key):  # defensive: only ever write atlas:orch:*
        raise ValueError(f"refused non-orch control key: {key!r}")
    R.set_json(key, current)
    return current


def clear_control(thread_id: str) -> None:
    R.delete_key(control_key(thread_id))


def _consume_force_rounds(thread_id: str, control: dict) -> int:
    """Return the force_more_rounds value and clear it so it applies only once."""
    forced = int(control.get("force_more_rounds") or 0)
    if forced:
        current = read_control(thread_id)
        current["force_more_rounds"] = 0
        current["updated_at"] = M.now_iso()
        R.set_json(control_key(thread_id), current)
    return forced


def apply_seats(current_seats: list[dict], control: dict) -> list[dict]:
    """Apply operator inject/retire directives to the seat list for the next round."""
    retire = {s.lower() for s in (control.get("retire_seats") or [])}
    seats = [p for p in current_seats if (p.get("id") or "").lower() not in retire]
    have = {(p.get("id") or "").lower() for p in seats}
    inject = [r for r in (control.get("inject_seats") or []) if r.lower() not in have]
    if inject:
        from src.orchestration import registry as REG

        seats = seats + REG.resolve_seats(inject)
    return seats

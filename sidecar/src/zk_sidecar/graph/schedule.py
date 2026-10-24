"""The nightly rebuild. It runs only if the vault changed since the last build.

A night missed because Obsidian was closed runs soon after the sidecar starts, but only for a
vault that was built before. The first build is always yours to start: it runs the GPU a while.
"""
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

START_DELAY_S = 120
TICK_S = 60


def parse(schedule: str) -> tuple[int, int] | None:
    """'03:00' → (3, 0). Empty or 'off' → None."""
    schedule = schedule.strip().lower()
    if not schedule or schedule == 'off':
        return None
    try:
        hh, mm = (int(x) for x in schedule.split(':'))
    except ValueError:
        raise ValueError(f'Use HH:MM for the rebuild time, not {schedule!r}') from None
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f'Use HH:MM for the rebuild time, not {schedule!r}')
    return hh, mm


def last_slot(at: tuple[int, int], now: datetime) -> datetime:
    slot = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    return slot - timedelta(days=1) if slot > now else slot


def due(at: tuple[int, int] | None, built: str, started: datetime, now: datetime) -> datetime | None:
    """The slot to run for, or None."""
    if not at:
        return None
    slot = last_slot(at, now)
    if built:
        return slot if datetime.fromisoformat(built) < slot else None
    return slot if slot > started else None


class Scheduler:
    def __init__(self, at: tuple[int, int] | None, built: Callable[[], str], changed: Callable[[], bool],
                 run: Callable[[], None], now: Callable[[], datetime] = datetime.now):
        self.at, self.built, self.changed, self.run, self.now = at, built, changed, run, now
        self.started = now()
        self._checked: datetime | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self.at:
            threading.Thread(target=self._loop, daemon=True, name='graph schedule').start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> bool:
        """Check once. True if a rebuild was started."""
        slot = due(self.at, self.built(), self.started, self.now())
        if not slot or slot == self._checked:
            return False
        self._checked = slot  # reading the whole vault once per slot is enough
        if not self.changed():
            return False
        self.run()
        return True

    def _loop(self) -> None:
        if self._stop.wait(START_DELAY_S):
            return
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001  a bad tick must not end the schedule
                pass
            if self._stop.wait(TICK_S):
                return

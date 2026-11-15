"""סבב חזרה אחד: תור הכרטיסים להיום, בלי תלות בממשק הגרפי."""
from __future__ import annotations

from collections import deque
from datetime import datetime

from . import srs
from .models import Card, ReviewState
from .storage import Storage

NEW, REVIEW, AGAIN = "new", "review", "again"


class ReviewSession:
    """תור כרטיסים: קודם חזרות שהגיע זמנן, אחר כך כרטיסים חדשים עד המכסה היומית.

    כרטיס שקיבל Again חוזר לסוף התור ונשאל שוב באותו סבב.
    """

    def __init__(self, db: Storage, new_per_day: int = 20, review_limit: int = 200,
                 extra_new: int = 0, now: datetime | None = None):
        self.db = db
        new_limit = max(0, new_per_day - db.new_done_today(now)) + extra_new
        ids = db.due_card_ids(limit=review_limit, new_limit=new_limit)
        self.kind = {cid: (NEW if db.get_state(cid).reps == 0 else REVIEW) for cid in ids}
        self.queue: deque[int] = deque(ids)
        self.answered = 0

    def current(self) -> Card | None:
        """הכרטיס שבראש התור, או None כשהסבב נגמר. מדלג על כרטיסים שנמחקו."""
        while self.queue:
            card = self.db.get_card(self.queue[0])
            if card:
                return card
            self.kind.pop(self.queue.popleft(), None)
        return None

    def state(self) -> ReviewState:
        return self.db.get_state(self.queue[0])

    def preview(self, now: datetime | None = None) -> dict[int, str]:
        """לכל ציון: בעוד כמה זמן הכרטיס יחזור."""
        st = self.state()
        return {g: srs.describe_interval(srs.schedule(st, g, now).interval_days)
                for g in (srs.AGAIN, srs.HARD, srs.GOOD, srs.EASY)}

    def answer(self, grade: int, now: datetime | None = None) -> None:
        cid = self.queue.popleft()
        self.db.save_state(srs.schedule(self.db.get_state(cid), grade, now), grade)
        if grade == srs.AGAIN:
            self.kind[cid] = AGAIN
            self.queue.append(cid)
        else:
            self.kind.pop(cid, None)
            self.answered += 1

    def counts(self) -> dict[str, int]:
        out = {NEW: 0, REVIEW: 0, AGAIN: 0}
        for cid in self.queue:
            out[self.kind[cid]] += 1
        return out

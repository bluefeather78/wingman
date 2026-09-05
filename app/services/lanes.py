"""Bounded lanes for slow, PAID work — Phase 2 item 8.

MARQUEE M9: what this bounds is the paid branch of the deadline and checklist routes, and
turning a student away rather than making the call is the whole behaviour. Approved by Shama
2026-09-05 ("ok to turn student away for now... we will revisit when I buy hosting on
Render") and logged in PRODUCTION_READINESS_PLAN.md.

The problem is the one Phase 2 item 2 fixed for /api/ai, in a second place. FastAPI runs a
plain-`def` handler in the anyio threadpool, whose 40 slots serve EVERY route.
handle_deadline_check calls check_deadlines.check_one — a Claude web-search call measured at
~$0.07 and taking tens of seconds — straight from that thread. Nothing bounded how many could
run at once, so a class of students opening the Quest Log together could hold most of the
pool in paid calls while the catalog stopped answering.

WHY THIS IS A DIFFERENT PRIMITIVE FROM app/routes/ai.py's _AiLane, rather than a shared one:
the two run in different worlds. _AiLane is an int guarded by nothing, because it is only
ever touched from the single-threaded event loop, where a check-and-increment cannot be
interleaved. This one is touched from N worker threads at once, so it needs a real
semaphore. Merging them would mean the loop-side one paying for a lock it cannot contend, and
a comment on each explaining when the other applies — two honest small things beat one
abstraction that is wrong half the time.

WHY THE ACQUIRE IS AROUND ONLY THE PAID CALL, not the whole handler: the common case for both
routes is a cache hit that costs nothing and returns immediately. Wrapping the handler would
let four slow paid checks shed a hundred free cached reads — making the cheap path fail
because of the expensive one, which is the opposite of the goal.
"""
import threading


class PaidLane:
    """Bounds how many paid calls of one kind may be in flight. Never waits."""

    def __init__(self, limit, name=""):
        self.limit = limit
        self.name = name
        self._sem = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self.in_flight = 0
        self.shed_count = 0     # observability without a metrics stack (item 9 is dropped)

    def try_acquire(self):
        """Take a slot if one is free. True if acquired; the caller must then release()."""
        got = self._sem.acquire(blocking=False)
        with self._lock:
            if got:
                self.in_flight += 1
            else:
                self.shed_count += 1
        return got

    def release(self):
        # BoundedSemaphore raises on an over-release rather than silently widening the lane
        # forever, which is the failure that matters: a lane that has quietly grown is
        # indistinguishable from one that is working.
        self._sem.release()
        with self._lock:
            self.in_flight = max(0, self.in_flight - 1)

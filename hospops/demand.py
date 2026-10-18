"""Regional demand signal: flu-season ER surge index.

FakeDemand is deterministic and seeded -- suitable for tests and the demo.
LiveDemand documents the production interface but always reports an explicit
gap when no feed is configured; it never invents a surge index.
"""

import hashlib


class FakeDemand:
    """Seeded regional surge index (0-100) with named drivers."""

    DRIVERS = ["flu-season", "school-term", "holiday-travel", "heat-wave"]

    def __init__(self, seed=42):
        self.seed = seed

    def _index(self, region, day):
        digest = hashlib.md5(f"{self.seed}|{region}|{day}".encode()).hexdigest()
        return int(digest[:4], 16) % 101

    def get_signal(self, region, day):
        index = self._index(region, day)
        drivers = [d for i, d in enumerate(self.DRIVERS)
                   if (index >> i) & 1]
        return {
            "ok": True,
            "region": region,
            "day": day,
            "surge_index": index,
            "drivers": drivers or ["baseline"],
            "backend": "fake",
        }


class LiveDemand:
    """Production interface for a regional demand feed.

    Without configuration this returns an explicit gap -- the planner then
    answers from internal telemetry alone instead of hallucinating demand.
    """

    def __init__(self, api_url=None, api_key=None):
        self.api_url = api_url
        self.api_key = api_key

    def get_signal(self, region, day):
        if not self.api_url:
            return {
                "ok": False,
                "gap": (
                    "live regional demand feed not configured "
                    "(set api_url); answered without external demand data"
                ),
                "region": region,
                "day": day,
                "backend": "live",
            }
        # A real implementation would GET {api_url}/surge?region=&day=
        # with the api_key here and validate the response schema.
        raise NotImplementedError(
            "LiveDemand transport is a documented stub in this repo; "
            "wire it to the regional feed in production."
        )

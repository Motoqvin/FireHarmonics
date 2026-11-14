"""Tests for the FireLens model endpoints.

The integration tests run the real FireLens model on places that are already
in the local FireLens cache, with downloads disabled, so they never contact
NASA. They are skipped when the firelens package or its cache is missing.
"""
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from FireDetectionAPP import firelens_api, main

try:
    from firelens.config import load_settings
    from firelens.jobs import JobFailed, JobManager, JobNotFinished, JobNotFound

    SETTINGS = load_settings(require_key=False)
    HAVE_FIRELENS = True
    # cache tiles of the two test places (written by FireLens's six-location check)
    HAVE_CACHE = (SETTINGS.data_dir / "models" / "global").is_dir() and all(
        any(SETTINGS.data_dir.rglob(f"{tile}_VIIRS_SNPP_SP.json"))
        for tile in ("tile_-121_36_-118_39", "tile_-142_-31_-138_-29"))
except ImportError:
    HAVE_FIRELENS = HAVE_CACHE = False

CALIFORNIA = (37.7, -119.5)        # cached by the FireLens six-location check
OCEAN = (-30.0, -140.0)            # cached; no fire detection within 100 km


@unittest.skipUnless(HAVE_FIRELENS, "firelens package not installed")
class RegionExportTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_regions_list(self):
        r = self.client.get("/api/v1/regions")
        self.assertEqual(r.status_code, 200)
        ids = [x["region_id"] for x in r.json()["regions"]]
        self.assertEqual(set(ids), {"california", "zambia_angola", "rondonia", "yakutia", "punjab"})

    def test_region_files(self):
        for path in ("activity", "harmonized", "patterns"):
            with self.subTest(path=path):
                r = self.client.get(f"/api/v1/regions/zambia_angola/{path}")
                self.assertEqual(r.status_code, 200)
                self.assertIsInstance(r.json(), dict)
        self.assertEqual(self.client.get("/api/v1/models").status_code, 200)

    def test_unknown_region_and_path_tricks_are_404(self):
        for rid in ("atlantis", "..", "california%2F..%2F..", "CALIFORNIA"):
            with self.subTest(rid=rid):
                r = self.client.get(f"/api/v1/regions/{rid}/patterns")
                self.assertEqual(r.status_code, 404)
                self.assertEqual(r.json()["status_code"], 404)

    def test_region_forecast_month_validation(self):
        self.assertEqual(self.client.get("/api/v1/regions/california/forecast?month=2023-13").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/regions/california/forecast?month=1999-01").status_code, 404)

    def test_model_status_never_contains_the_key(self):
        r = self.client.get("/api/v1/model")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsInstance(body["firms_key_configured"], bool)
        if SETTINGS.map_key:
            self.assertNotIn(SETTINGS.map_key, r.text)


class FakeManager:
    """Stands in for the FireLens job manager to test HTTP status mapping."""

    def __init__(self, outcome):
        self.outcome = outcome

    def get_job_status(self, job_id):
        if job_id == "missing":
            raise JobNotFound(job_id)
        return {"job_id": job_id, "status": "running", "progress_percent": 42.0}

    def get_job_result(self, job_id, timeout=None):
        if job_id == "missing":
            raise JobNotFound(job_id)
        raise self.outcome


@unittest.skipUnless(HAVE_FIRELENS, "firelens package not installed")
class StatusMappingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def use(self, outcome):
        p = patch.object(firelens_api, "get_manager", return_value=FakeManager(outcome))
        p.start()
        self.addCleanup(p.stop)

    def test_unfinished_job_is_202(self):
        self.use(JobNotFinished("running"))
        r = self.client.get("/api/v1/analyses/abc/result")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["progress_percent"], 42.0)

    def test_failed_job_maps_error_type(self):
        cases = {"firms_rate_limited": 503, "network": 504, "firms_auth": 502, "internal_error": 500,
                 "something_new": 500}
        for kind, code in cases.items():
            with self.subTest(kind=kind):
                self.use(JobFailed({"type": kind, "message": "boom", "recoverable": True, "failed_stage": "download"}))
                r = self.client.get("/api/v1/analyses/abc/result")
                self.assertEqual(r.status_code, code)
                self.assertEqual(r.json()["error"]["type"], kind)
                self.assertTrue(r.json()["error"]["recoverable"])

    def test_unknown_job_is_404(self):
        self.use(JobNotFinished("x"))
        self.assertEqual(self.client.get("/api/v1/analyses/missing").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/analyses/missing/result").status_code, 404)

    def test_invalid_request_is_422_and_creates_no_job(self):
        jm = JobManager(SETTINGS, max_workers=1, persist=False)
        self.addCleanup(jm.shutdown)
        p = patch.object(firelens_api, "get_manager", return_value=jm)
        p.start()
        self.addCleanup(p.stop)
        for body in ({"latitude": 95, "longitude": 0}, {"latitude": 0, "longitude": 0, "radius_km": 500},
                     {"latitude": 0, "longitude": 0, "target_month": "1990-01"}):
            with self.subTest(body=body):
                r = self.client.post("/api/v1/analyses", json=body)
                self.assertEqual(r.status_code, 422)
        self.assertEqual(jm.list_jobs(), [])


@unittest.skipUnless(HAVE_FIRELENS and HAVE_CACHE, "firelens cache for the test places not present")
class RealModelTests(unittest.TestCase):
    """The real FireLens engine on cached places; downloads disabled."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.jm = JobManager(SETTINGS, max_workers=2, store_dir=self.tmp.name)
        self.addCleanup(self.jm.shutdown)
        p = patch.object(firelens_api, "get_manager", return_value=self.jm)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_job_flow_status_events_result(self):
        lat, lon = CALIFORNIA
        r = self.client.post("/api/v1/analyses", json={"latitude": lat, "longitude": lon, "allow_download": False})
        self.assertEqual(r.status_code, 202)
        job_id = r.json()["job_id"]
        self.assertEqual(r.json()["links"]["events"], f"/api/v1/analyses/{job_id}/events")

        # Server-Sent Events until the job completes
        events = []
        with self.client.stream("GET", f"/api/v1/analyses/{job_id}/events") as s:
            self.assertEqual(s.status_code, 200)
            self.assertTrue(s.headers["content-type"].startswith("text/event-stream"))
            for line in s.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        self.assertEqual(events[-1]["type"], "completed")
        pct = [e["progress_percent"] for e in events]
        self.assertEqual(pct, sorted(pct))
        self.assertEqual(pct[-1], 100.0)
        self.assertEqual([e["seq"] for e in events], sorted({e["seq"] for e in events}))

        st = self.client.get(f"/api/v1/analyses/{job_id}").json()
        self.assertEqual(st["status"], "completed")
        self.assertTrue(st["result_available"])

        res = self.client.get(f"/api/v1/analyses/{job_id}/result")
        self.assertEqual(res.status_code, 200)
        result = res.json()["result"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data_coverage"]["retrieval"]["chunks_downloaded_this_call"], 0)
        p = result["prediction"]
        self.assertEqual(p["status"], "ok")
        self.assertTrue(0 < p["probability"] < 1)
        self.assertIn(p["method"], ("seasonal_baseline", "xgboost_pooled"))
        self.assertGreater(result["historical_activity"]["total_detections_observed_months"], 0)

        # reconnecting with Last-Event-ID only returns newer events
        with self.client.stream("GET", f"/api/v1/analyses/{job_id}/events",
                                headers={"Last-Event-ID": str(events[-2]["seq"])}) as s:
            again = [json.loads(x[6:]) for x in s.iter_lines() if x.startswith("data: ")]
        self.assertEqual([e["seq"] for e in again], [events[-1]["seq"]])

    def test_cached_synchronous_endpoint_and_ocean(self):
        lat, lon = OCEAN
        r = self.client.get("/api/v1/locations/analysis", params={"latitude": lat, "longitude": lon})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["prediction"]["probability"])
        self.assertEqual(result["prediction"]["reason"], "no_fire_detections_within_100km")
        bad = self.client.get("/api/v1/locations/analysis", params={"latitude": 0, "longitude": 400})
        self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()

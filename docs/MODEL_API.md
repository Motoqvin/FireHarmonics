# FireLens model endpoints

[Back to API reference](API.md)

These endpoints connect the backend to the FireLens fire model.
The model code is in a separate Python package called `firelens`.
The endpoint code is in `FireDetectionAPP/firelens_api.py`.

## 1. Install the model

The model is in the `firelens` folder of this repository.
`requirements.txt` installs it, so step 4 of [Setup](SETUP.md) already does this.

To install only the model, run from `FireHarmonics`:

```bash
python -m pip install -e ./firelens
```

Expected result: `Successfully installed ... firelens`.

The first install downloads pandas, scikit-learn and XGBoost. It can take a few minutes.

Restart the server.

Open <http://127.0.0.1:8000/api/v1/model>.

Expected result: **HTTP 200**.

```json
{
  "status_code": 200,
  "engine": "firelens",
  "schema_version": "1.0",
  "model_trained": true,
  "firms_key_configured": true,
  "forecast_horizon_months": 12
}
```

`firms_key_configured` shows only `true` or `false`. It never shows the key.

Without `firelens`, the model endpoints return **HTTP 503**.
The other endpoints keep working.

The model uses the same `NASA_FIRMS_MAP_KEY` as the rest of the backend.

## 2. Endpoints

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/v1/model` | Checks that the model can run |
| POST | `/api/v1/analyses` | Starts an analysis of any point. Returns a job ID at once |
| GET | `/api/v1/analyses` | Lists jobs |
| GET | `/api/v1/analyses/{job_id}` | Job status and progress |
| GET | `/api/v1/analyses/{job_id}/events` | Live progress as Server-Sent Events |
| GET | `/api/v1/analyses/{job_id}/result` | The finished result |
| POST | `/api/v1/analyses/{job_id}/retry` | Runs a failed job again |
| GET | `/api/v1/locations/analysis` | Quick analysis from saved data only. Never downloads |
| GET | `/api/v1/regions` | The five project regions |
| GET | `/api/v1/regions/{region_id}/activity` | Monthly fire detections for a region |
| GET | `/api/v1/regions/{region_id}/harmonized` | MODIS-equivalent series for a region |
| GET | `/api/v1/regions/{region_id}/patterns` | Seasons, trends, anomalies, hotspots |
| GET | `/api/v1/regions/{region_id}/forecast?month=YYYY-MM` | Saved grid forecast, if one exists |
| GET | `/api/v1/models` | Model accuracy and limits |

`region_id` is one of `california`, `zambia_angola`, `rondonia`, `yakutia`, `punjab`.

## 3. Analyse a point

Start the job:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyses \
  -H "Content-Type: application/json" \
  -d '{"latitude": -13.0, "longitude": 24.0, "radius_km": 25}'
```

Expected result: **HTTP 202**.

```json
{
  "status_code": 202,
  "job_id": "6a342f7bba52491ca1e16b9cf49b1f86",
  "status": "running",
  "links": {
    "status": "/api/v1/analyses/6a342f7bba52491ca1e16b9cf49b1f86",
    "result": "/api/v1/analyses/6a342f7bba52491ca1e16b9cf49b1f86/result",
    "events": "/api/v1/analyses/6a342f7bba52491ca1e16b9cf49b1f86/events",
    "retry": "/api/v1/analyses/6a342f7bba52491ca1e16b9cf49b1f86/retry"
  }
}
```

Your `job_id` will be different.

Request fields:

| Field | Default | Allowed |
| --- | --- | --- |
| `latitude` | required | -90 to 90 |
| `longitude` | required | -180 to 180 |
| `radius_km` | `25` | 1 to 200 |
| `target_month` | next month | `YYYY-MM`, from this month to 12 months ahead |
| `history_years` | `10` | 3 to 14 |
| `allow_download` | `true` | `false` uses saved data only |

A wrong value returns **HTTP 422** and creates no job.

## 4. How long it takes

| Place | Time |
| --- | --- |
| Already saved on this computer | 1 to 3 seconds |
| Not saved yet | 20 to 30 minutes with the default 10 years |

The repository holds the trained model and the region files.
It does not hold downloaded NASA data. That data is about 1.6 GB.
On a fresh copy, the first analysis of every place downloads its data.
For a faster first test, use `"history_years": 4`. It takes about 4 minutes.

With `"history_years": 3` there is no forecast. The model needs at least 36 observed months, and some months always have missing NASA data. The result is then `insufficient_data`, with the reason in `prediction.reason`.

New places download NASA data.
NASA limits how fast one key can download.
The model pauses for 10 minutes when it gets close to that limit.
During a pause, the status shows `waiting`.

A place that was analysed once is saved.
The next request for it takes seconds.

Before a demo, analyse every place you plan to show.

## 5. Follow progress

Choose one way.

**Polling.** Call the status endpoint every 1 to 2 seconds:

```bash
curl http://127.0.0.1:8000/api/v1/analyses/<job_id>
```

Replace `<job_id>` with your job ID.

Useful fields:

| Field | Meaning |
| --- | --- |
| `status` | `queued`, `running`, `completed` or `failed` |
| `progress_percent` | 0 to 100. Reaches 100 only when the result is ready |
| `stage_label` | What is happening now |
| `eta_seconds` | Seconds left. Only while downloading; otherwise `null` |
| `waiting` | Set during a NASA rate-limit pause; otherwise `null` |
| `indeterminate` | `true` when the length of this step is unknown. Show a spinner, not a bar |
| `result_available` | `true` when the result is ready |

**Live events.** In the browser:

```javascript
const events = new EventSource(`/api/v1/analyses/${jobId}/events`);
for (const type of ["status", "stage", "progress", "stage_done", "warning"]) {
  events.addEventListener(type, (e) => update(JSON.parse(e.data)));
}
events.addEventListener("completed", () => { events.close(); loadResult(); });
events.addEventListener("failed", (e) => { events.close(); showError(JSON.parse(e.data)); });
```

Each event has a name, so use `addEventListener`. `onmessage` receives nothing.

The stream ends after `completed` or `failed`. Call `events.close()` then, or the browser reconnects.

After a dropped connection, the browser reconnects and gets only newer events.

## 6. Get the result

```bash
curl http://127.0.0.1:8000/api/v1/analyses/<job_id>/result
```

| HTTP status | Meaning |
| --- | --- |
| 200 | Done. The analysis is in `result` |
| 202 | Not finished yet. Try again later |
| 404 | Unknown job ID |
| 502, 503, 504, 500 | The job failed. See the table in section 7 |

Main parts of `result`:

| Part | Content |
| --- | --- |
| `status` | `ok`, `partial` (some days missing) or `insufficient_data` |
| `prediction.probability` | Chance of at least one satellite fire detection in the target month |
| `prediction.method` | How the forecast was made |
| `prediction.reason` | Why there is no forecast, when `probability` is `null` |
| `historical_activity` | Monthly and yearly detections |
| `seasonal_patterns` | Peak month and fire season |
| `long_term_trends` | Change per year |
| `anomalies`, `hotspots` | Unusual months and busy places |
| `warnings`, `limitations` | Show these to the user |

Full field list: `firelens/docs/GLOBAL_INFERENCE_API.md`.

Rules for the screen:

- Show only `prediction.probability` as the forecast.
- If `probability` is `null`, show `prediction.explanation` instead. Never show 0.
- Say "satellite fire detections", not "fires".
- Credit NASA FIRMS as the data source.

## 7. Errors

| HTTP status | `error.type` | Meaning | Next step |
| --- | --- | --- | --- |
| 422 | — | Wrong request value | Read `detail` |
| 502 | `firms_auth` | NASA rejected the key, or no key | Check `.env`, then retry |
| 502 | `firms_request_rejected` | NASA rejected the request | Read `detail` |
| 503 | `firms_rate_limited` | NASA kept refusing | Retry later |
| 503 | `data_unavailable` | NASA has no data for the period | Retry later |
| 503 | `missing_file` | Model files are missing | Train the model in `firelens` |
| 503 | `interrupted` | The server restarted during the job | Retry |
| 504 | `network` | NASA timed out | Retry |
| 500 | `internal_error` | A bug | Report it |

Failed jobs include `error.recoverable`.
If it is `true`, offer a **Retry** button that calls:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyses/<job_id>/retry
```

## 8. Things to know

- Jobs run inside the server process. Run one server process.
- With `--reload`, saving a file restarts the server. Running jobs then fail with `interrupted`. Retry them.
- Job status and results are saved in `firelens/data/jobs/`. They survive a restart.
- Downloaded NASA data is saved in `firelens/data/`. Git ignores it. Keep that folder between runs.
- The model tests in `tests/test_firelens_api.py` that need saved data are skipped on a fresh copy. That is expected.
- The forecast is a prototype. It was checked in five regions only.

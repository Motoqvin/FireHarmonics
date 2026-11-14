# FireHarmonics

A Python backend for NASA fire detection data.

It downloads NASA FIRMS detections, imports historical exports, and returns
JSON through FastAPI. Map-point analyses run as background jobs with progress
and saved results. A prediction model can be added later.

## Start here

Read one guide at a time.

| You want to... | Open this guide |
| --- | --- |
| Run the backend | [Setup](docs/SETUP.md) |
| Use an endpoint | [API reference](docs/API.md) |
| Change the code | [Contributing](CONTRIBUTING.md) |
| Upload changes and invite a teammate | [GitHub sharing](docs/GITHUB.md) |

The guides use short steps, copyable commands, and expected results.

## Check that it runs

With the server running, open:

<http://127.0.0.1:8000/health>

Expected result: **HTTP 200**.

```json
{
  "status_code": 200,
  "status": "ok",
  "service": "FireHarmonics"
}
```

This checks the backend only. It does not check NASA or your key.

To test other endpoints, open:

<http://127.0.0.1:8000/docs>

## Analyse a map point

Open `/docs` and choose **POST /api/v1/analyses**.

Use this request body:

```json
{
  "latitude": -13,
  "longitude": 24,
  "radius_km": 25
}
```

Copy the returned `job_id`.
Check progress with **GET /api/v1/analyses/{job_id}**.
When it is complete, read **GET /api/v1/analyses/{job_id}/result**.

The [API guide](docs/API.md) explains SSE events, retries, archive imports,
and the limits of each statistic.

## Where the files are

| File | Purpose |
| --- | --- |
| `FireDetectionAPP/main.py` | API endpoints and application startup |
| `FireDetectionAPP/nasa.py` | NASA requests and CSV parsing |
| `FireDetectionAPP/jobs.py` | Background jobs, retries, and cache reuse |
| `FireDetectionAPP/analytics.py` | Radius filtering and descriptive statistics |
| `FireDetectionAPP/archives.py` | CSV, JSON, GeoJSON, and shapefile imports |
| `FireDetectionAPP/storage.py` | Persistent SQLite results and progress events |
| `FireDetectionAPP/schemas.py` | Request and response models |
| `FireDetectionAPP/regions.py` | Five example map locations |
| `FireDetectionAPP/.env.example` | Configuration example with a placeholder |
| `FireDetectionAPP/.env` | Your private NASA key; excluded from Git |
| `requirements.txt` | Python dependencies |
| `tests/` | API, worker, geospatial, statistics, and archive tests |

## Before sharing this repository

A NASA key is present in the existing Git history.

Replace that key before sharing the repository further.
The steps are in [GitHub sharing](docs/GITHUB.md#1-replace-the-exposed-key).

# API reference

[Back to README](../README.md)

Run the backend using [Setup](SETUP.md).

Local address: `http://127.0.0.1:8000`.

Open <http://127.0.0.1:8000/docs> to try the endpoints.
Select an endpoint, then **Try it out**, then **Execute**.

## Main flow: analyse a map point

### 1. Start the analysis

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyses \
  -H 'Content-Type: application/json' \
  -d '{"latitude":-13,"longitude":24,"radius_km":25}'
```

Expected status: **202**.
Copy `job_id` from the response.

The response also includes links to status, events, result, and retry.
The download runs in a background worker.

Optional body fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `source` | `VIIRS_NOAA20_NRT` | NASA dataset |
| `lookback_days` | `30` | Days ending at the latest available date |
| `start_date` | Not set | First requested day, as `YYYY-MM-DD` |
| `end_date` | Not set | Last requested day, as `YYYY-MM-DD` |

An explicit `start_date` replaces the lookback calculation.
The maximum period is 3650 days. Future dates are rejected.
Radius must be greater than zero and at most 500 km.

Availability can shorten the requested period.
The result states both the requested and downloaded dates.

### 2. Check progress

Replace `JOB_ID` with the ID you copied.

```bash
curl http://127.0.0.1:8000/api/v1/analyses/JOB_ID
```

Poll every 1–2 seconds until `status` is `completed` or `failed`.

| Field | Meaning |
| --- | --- |
| `status` | `queued`, `running`, `paused`, `completed`, or `failed` |
| `progress` | Progress from 0 to 100, weighted by completed download requests and processing stages |
| `stage` | Current operation, such as `downloading` or `analyzing` |
| `eta_seconds` | Estimate from observed request times, or `null` when unknown |
| `nasa_pause_seconds` | NASA retry pause, or `null` |
| `cache_hit` | Whether a saved result was reused |
| `error` | Failure details, including `recoverable`, or `null` |

Progress is not a prediction of exact runtime.
NASA limits and the selected period affect the duration.

For a live SSE stream instead of polling:

```bash
curl -N http://127.0.0.1:8000/api/v1/analyses/JOB_ID/events
```

SSE event names are `progress`, `completed`, and `failed`.
Each event contains a job-status JSON object and a numeric event ID.

Reconnect with `Last-Event-ID` or `?after_id=EVENT_ID` to read newer events.
The stream closes after the terminal event.

### 3. Read the result

```bash
curl -i http://127.0.0.1:8000/api/v1/analyses/JOB_ID/result
```

| HTTP status | Meaning |
| --- | --- |
| 200 | Completed; read `result` |
| 202 | Still working; `result` is `null` |
| 409 | Failed; read `error` |
| 404 | The job does not exist |

## What the result contains

| Field inside `result` | Meaning |
| --- | --- |
| `location` | Selected centre and radius |
| `coverage` | Dates, origin, and completeness of the data |
| `detection_count` | Unique detections inside the circle and period |
| `historical_activity` | Daily, monthly, and annual counts |
| `historical_activity.mean_annual_detections` | Mean across complete calendar years, or `null` |
| `seasonal_patterns.peak_month` | Month number 1–12, or `null`; needs two complete years |
| `long_term_trends` | Annual linear count trend; needs three complete years |
| `anomalies` | Monthly count outliers; needs 12 complete months |
| `hotspots` | Up to 100 cells with the most detections; each cell is 0.01 degrees |
| `prediction.probability` | Always `null` until a model is added |
| `prediction.explanation` | Why no forecast is available |
| `warnings` | Coverage and interpretation limits |

The NASA bounding box is filtered to the exact requested radius.
Duplicate observations across downloaded batches are removed.

Counts describe satellite detections, not distinct fires.
Clouds, sensor coverage, and processing changes affect those counts.
Trends are descriptive; anomaly scores are not adjusted for seasonality.

A recent-data request usually cannot support multi-year statistics.
Those sections return `insufficient_data` and an explanation.
A missing probability is not zero probability.

## Retry a failed analysis

Use this only when `error.recoverable` is `true`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyses/JOB_ID/retry
```

Expected status: **202**. The same job ID is reused.
Previously downloaded batches can be reused while fresh.

A non-recoverable or unfinished job returns **409**.
If NASA requested a longer pause, retry returns **429** with `Retry-After`.

The worker automatically retries transient failures up to twice per request.
It respects NASA's retry delay. Pauses over 60 seconds are left for manual retry.

## Read saved results without downloading

| GET path | Contents |
| --- | --- |
| `/api/v1/locations/analysis?latitude=-13&longitude=24&radius_km=25` | Latest saved analysis for that exact location, radius, and source |
| `/api/v1/regions` | Five example map locations and their saved-data status |
| `/api/v1/regions/{id}/patterns` | Saved result for one example location |
| `/api/v1/regions/{id}/activity` | Alias for that region's saved result |
| `/api/v1/harmonized?latitude=-13&longitude=24` | Saved normalized detections and original row fields |
| `/api/v1/analyses` | Recent jobs; optional `limit`, up to 100 |

Location queries accept the same fields as the analysis request.
The default source is NOAA-20. Optional dates select matching saved coverage.
Without dates, the location endpoint returns the latest saved period.

Harmonized rows support `offset` and `limit`; the maximum page size is 10000.
Coordinates and FRP are normalized to numbers. Original fields are in `raw`.

These endpoints never download data.
An empty cache returns `status: "insufficient_data"`.
A saved response includes its timestamp and a `stale` flag where applicable.

The five regions have metadata only until analysed or imported.
To prepare a demo, POST each region's coordinates and radius to `/api/v1/analyses`.
Wait for completion before presenting it.

## Import historical archives

Read supported formats and limits at `GET /api/v1/archives`.

The [NASA archive page](https://firms.modaps.eosdis.nasa.gov/download/) requires
Earthdata or email-code authentication. NASA processes the export and emails
instructions. This backend does not automate that login or request process.

Download the export yourself, then upload it:

```bash
curl -X POST \
  'http://127.0.0.1:8000/api/v1/archives/import?latitude=-13&longitude=24&radius_km=25&source=VIIRS_NOAA20_SP&start_date=2020-01-01&end_date=2023-12-31&coverage_complete=true' \
  -F 'file=@fires.csv'
```

Replace `fires.csv` with your downloaded file.
Set the source, location, and dates to match the export.

Expected status: **201**, with the calculated `result`.
The saved result is available through the location and region endpoints.

Supported files:

- CSV.
- JSON lists, objects containing `rows` or `data`, and Point GeoJSON.
- ZIP files containing CSV/JSON/GeoJSON or one point shapefile dataset.

A shapefile ZIP needs matching `.shp` and `.dbf` files.
Use WGS84 longitude/latitude coordinates; projected coordinates are unsupported.
Archive attribute names are normalized to lowercase.
Detections require `latitude`, `longitude`, and `acq_date`.
Optional `acq_time` uses `HHMM`.

Limits: 20 MiB uploaded, 100 MiB expanded ZIP, 100 files in a ZIP,
and 200000 input rows. The maximum analysis period is 3650 days.

`coverage_complete` defaults to **false**.
Set it to true only for a complete export of the declared period and area.
This declaration comes from the uploader and is recorded in `coverage`.
The backend cannot infer complete coverage from detection dates alone.
Incomplete imports do not produce annual means or seasonal/trend estimates.

## Direct NASA data

| GET path | Contents |
| --- | --- |
| `/api/v1/fires` | Raw parsed CSV rows |
| `/api/v1/availability` | Available dates by source |
| `/api/v1/sources` | Supported dataset identifiers and NASA documentation links |
| `/fires`, `/`, `/test` | Aliases for `/api/v1/fires` |
| `/availability` | Alias for `/api/v1/availability` |

Fire parameters: `source`, `area`, `day_range` from 1 to 5, and optional `date`.
`area` is `world` or `west,south,east,north`.
Omit `date` for the latest data.

```bash
curl 'http://127.0.0.1:8000/api/v1/fires?area=23,-14,25,-12&day_range=1'
```

All original CSV columns are preserved. Their raw values remain strings.
Empty or header-only CSV returns HTTP 200 with `count: 0` and `rows: []`.
Availability accepts `source=ALL` to list all products.

These requests follow the [Area API](https://firms.modaps.eosdis.nasa.gov/api/area/)
and [Data Availability API](https://firms.modaps.eosdis.nasa.gov/api/data_availability/).
The old fixed date and S-NPP default have been replaced with latest data and NOAA-20.

## Health and model status

`GET /health` returns HTTP 200 when the backend can respond.
It does not call NASA. Use `/availability` to check the NASA connection.

`GET /api/v1/model` and `/api/v1/models` return `available: false` and
`status: "not_configured"`. A model can be integrated later.
No forecast endpoint currently generates predictions.

## Errors

Handled errors include `status_code`, `detail`, and `recoverable`.

| HTTP status | Meaning |
| --- | --- |
| 413 | Archive too large |
| 415 | Unsupported archive format |
| 422 | Invalid request or invalid imported detections |
| 429 | Job queue full or NASA retry pause still active |
| 500 | Missing NASA key or an internal failure |
| 502 | Upstream HTTP, connection, or parsing failure |
| 504 | NASA request timed out |

## Local storage and workers

The queue accepts up to 32 active jobs, with two worker threads.
Run one Uvicorn process. Multiple Uvicorn workers sharing this store are unsupported.

Jobs, events, cached batches, and results live in `FireDetectionAPP/.data/analyses.sqlite3`.
This folder is excluded from Git.
Completed results survive reloads. Interrupted jobs become recoverable failures.

Recent-data cache freshness defaults to one hour.
Imported archive results stay available without automatic expiry.
Saved-location endpoints may return old results with `stale: true`.
A new POST refreshes an expired matching NASA analysis.

Optional settings in `.env`:

```dotenv
FIREHARMONICS_DATA_DIR=FireDetectionAPP/.data
FIREHARMONICS_CACHE_TTL_SECONDS=3600
```

Restart the backend after changing `.env`.
Upstream URLs and error bodies are excluded from responses to keep the NASA key private.

# FireHarmonics

A Python backend for NASA fire detection data.

It downloads CSV from NASA FIRMS and returns JSON through FastAPI.

## Start here

Read one guide at a time.

| You want to... | Open this guide |
| --- | --- |
| Run the backend | [Setup](docs/SETUP.md) |
| Use an endpoint | [API reference](docs/API.md) |
| Use the fire model | [FireLens model endpoints](docs/MODEL_API.md) |
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

## Where the files are

| File | Purpose |
| --- | --- |
| `FireDetectionAPP/main.py` | Endpoints, NASA requests, and CSV parsing |
| `FireDetectionAPP/.env.example` | Configuration example with a placeholder |
| `FireDetectionAPP/.env` | Your private NASA key; excluded from Git |
| `requirements.txt` | Python dependencies |
| `tests/test_api.py` | Tests with mocked NASA responses |
| `FireDetectionAPP/firelens_api.py` | Fire model endpoints under `/api/v1/` |
| `tests/test_firelens_api.py` | Tests for the fire model endpoints |
| `firelens/` | The FireLens fire model package and its documentation |

## Before sharing this repository

A NASA key is present in the existing Git history.

Replace that key before sharing the repository further.
The steps are in [GitHub sharing](docs/GITHUB.md#1-replace-the-exposed-key).

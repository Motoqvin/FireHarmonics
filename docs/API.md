# API reference

[Back to README](../README.md)

Start the backend using the [setup guide](SETUP.md).

Local address: `http://127.0.0.1:8000`.

Interactive reference: <http://127.0.0.1:8000/docs>.

In that page, select an endpoint, then **Try it out**, then **Execute**.

## Endpoints

All data endpoints use **GET**. Send no request body.
There are currently no query parameters.

| Path | What it returns | Needs a NASA key? |
| --- | --- | --- |
| `/health` | Backend liveness | No |
| `/fires` | Every parsed fire detection | Yes |
| `/availability` | Available dates for the configured NASA source | Yes |
| `/` | Same result as `/fires` | Yes |
| `/test` | Same result as `/fires`, including all rows | Yes |

`/docs` displays the interactive reference.
`/openapi.json` returns the API schema.

Fire model endpoints start with `/api/v1/`.
See [FireLens model endpoints](MODEL_API.md).

## Health response

```bash
curl -i http://127.0.0.1:8000/health
```

Expected HTTP status: **200**.

```json
{
  "status_code": 200,
  "status": "ok",
  "service": "FireHarmonics"
}
```

This means the backend can respond.
It does not confirm NASA availability or key validity.

## Fire response

```bash
curl -i http://127.0.0.1:8000/fires
```

Example only. NASA supplies the actual columns and rows.

```json
{
  "status_code": 200,
  "source": "VIIRS_SNPP_NRT",
  "count": 1,
  "rows": [
    {
      "latitude": "48.1",
      "longitude": "-120.2",
      "confidence": "n"
    }
  ],
  "area": "world",
  "date": "2026-11-02",
  "day_range": 1
}
```

`rows` contains all parsed records. It is not paginated.

`count` is the number of records in `rows`.

CSV column names become JSON keys. CSV values stay strings.
Convert values yourself if your feature needs numbers or dates.

Empty or header-only CSV returns HTTP 200 with `count: 0` and `rows: []`.

## Availability response

```bash
curl -i http://127.0.0.1:8000/availability
```

The response contains `status_code`, `source`, `count`, and `rows`.
Each object in `rows` uses the column names returned by NASA.

## Change which data is fetched

Edit the constants near the top of `FireDetectionAPP/main.py`:

| Setting | Current value | Meaning |
| --- | --- | --- |
| `SOURCE` | `VIIRS_SNPP_NRT` | NASA dataset |
| `AREA` | `world` | Geographic area |
| `DAY_RANGE` | `1` | Number of days requested |
| `DATE` | `2026-11-02` | Start date |

The date is fixed. It does not automatically change to today.
Use `/availability` to see which dates NASA offers for the source.

Requests for fire data wait up to 60 seconds.
Requests for availability wait up to 30 seconds.

## Errors

Check the actual HTTP status first. Handled errors repeat it in the JSON body.

| HTTP status | Meaning | Next step |
| --- | --- | --- |
| 500 | The server has no NASA key | Add the key to `.env` and restart. |
| 502 | NASA rejected the request, the connection failed, or CSV was invalid | Read `detail`. Check the key and dataset settings. |
| 504 | The NASA request timed out | Retry later. |

Example:

```json
{
  "status_code": 504,
  "detail": "The NASA FIRMS request timed out."
}
```

A successful response has HTTP status 200.
The backend leaves NASA error bodies and URLs out of responses to keep the key private.

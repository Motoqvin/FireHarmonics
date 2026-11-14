# Run the backend

[Back to README](../README.md)

You need Python **3.10 or newer** and a copy of this repository.

If you need to download the repository first, use the
[contribution guide](../CONTRIBUTING.md#1-download-the-project-once).

## 1. Open the project folder

Open a terminal in **FireHarmonics**.

This folder contains `README.md` and `requirements.txt`.

Run every command below from this folder.

## 2. Create a Python environment

Do this once for a new download.

**macOS or Linux:**

```bash
python3 -m venv .venv
```

**Windows PowerShell:**

```powershell
py -m venv .venv
```

## 3. Activate the environment

Do this when you open a new terminal.

**macOS or Linux:**

```bash
source .venv/bin/activate
```

**Windows PowerShell:**

```powershell
.venv\Scripts\Activate.ps1
```

Expected result: your prompt usually starts with `(.venv)`.

## 4. Install the dependencies

```bash
python -m pip install -r requirements.txt
```

This also installs the FireLens fire model from the `firelens` folder.
The first install can take a few minutes.

## 5. Start the server

```bash
python -m uvicorn FireDetectionAPP.main:app --reload
```

Expected result: the terminal shows `http://127.0.0.1:8000`.

Keep this terminal open. Saving a Python file reloads the backend.

Open <http://127.0.0.1:8000/health>. It should return HTTP 200.

## 6. Add your NASA key

The health check works without a key. NASA data endpoints need one.

Request your own key on the [NASA FIRMS key page](https://firms.modaps.eosdis.nasa.gov/api/map_key/).

If `FireDetectionAPP/.env` already exists, edit that file.
Keep your existing file instead of copying over it.

Otherwise, copy `FireDetectionAPP/.env.example` to `FireDetectionAPP/.env`.

**macOS or Linux:**

```bash
cp FireDetectionAPP/.env.example FireDetectionAPP/.env
```

**Windows PowerShell:**

```powershell
Copy-Item FireDetectionAPP/.env.example FireDetectionAPP/.env
```

Open `.env` in your editor. Replace the placeholder with your own key:

```dotenv
NASA_FIRMS_MAP_KEY=your_nasa_firms_map_key_here
```

Save the file. Stop the server with **Ctrl+C**, then run the start command again.
Changes to `.env` need a manual restart.

Open <http://127.0.0.1:8000/availability> to check the NASA connection.

Keep the real key in `.env` only. Leave `.env.example` as a placeholder.
An environment variable set in your terminal takes priority over `.env`.

## Run the tests

Use a second terminal with the environment activated.

```bash
python -m unittest discover -s tests -v
```

Expected result: `OK` at the end.

These tests use mock data. They do not need a NASA key or internet access.

## Common problems

| You see... | What to do |
| --- | --- |
| `Address already in use` | Another server uses port 8000. Stop it in its terminal with Ctrl+C, or add `--port 8001` to the start command. |
| `No module named FireDetectionAPP` | Run the command from FireHarmonics, the folder containing README.md. |
| `No module named uvicorn` | Activate the environment, then repeat step 4. |
| PowerShell blocks activation | Use `.venv\Scripts\python.exe` instead of `python` in steps 4 and 5. |
| HTTP 500: key not configured | Put the key in `FireDetectionAPP/.env`, then restart the server. |
| HTTP 502 or 504 | See the [API error guide](API.md#errors). |

If you choose port 8001, use `http://127.0.0.1:8001` in your browser.

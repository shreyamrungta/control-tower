# Deployment

The system is unusually easy to deploy because it has **no external
dependencies**: no API keys, no database, no third-party services. The agent
layer is rule-based and runs offline, and the dataset builds itself on first run
if it is missing. Any host that can run Python can run this.

Pick based on what you actually need:

| Need | Use |
|---|---|
| A link to put in your report / send to an evaluator | **Streamlit Community Cloud** |
| The viva demonstration itself | **Run it locally** |
| A host that isn't Streamlit Cloud, or a marked "deployment" component | **Docker** |
| Free hosting without a GitHub account tied to it | **Hugging Face Spaces** |

---

## Option 1 — Streamlit Community Cloud (recommended for a shareable link)

Free, no credit card, deploys straight from GitHub, and it is purpose-built for
exactly this kind of app.

**1. Push to GitHub**

```bash
cd control_tower
git init
git add .
git commit -m "Integrated Manufacturing Operations Control Tower"
git branch -M main
git remote add origin https://github.com/<you>/manufacturing-control-tower.git
git push -u origin main
```

Push the `data/` CSVs along with everything else. They are small (~30 KB total)
and committing them means the app starts instantly rather than regenerating on
first load. If you would rather not commit generated files, the app builds them
itself on first run — `generate_data.py` uses a fixed seed, so the numbers are
identical either way.

**2. Deploy**

Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub, and
click *New app*:

- Repository: `<you>/manufacturing-control-tower`
- Branch: `main`
- **Main file path: `dashboard/app.py`** ← the one setting people get wrong
- Python version: 3.11

Click *Deploy*. First build takes 2–4 minutes while it installs pandas, numpy,
streamlit and plotly. You get a permanent URL like
`https://<you>-manufacturing-control-tower.streamlit.app`.

**3. Nothing else to configure.** No secrets, no environment variables.

### Two things to know before your viva

- **Free apps sleep after about a week of inactivity**, and waking one takes
  30–60 seconds. Open your app the morning of the presentation so it is warm, and
  have the local copy running as a fallback.
- **The filesystem is ephemeral.** Approved manager decisions in
  `outputs/decision_log.json` survive a session but are wiped when the app
  restarts or sleeps. That is fine for a demonstration — and you can point at it
  as a deliberate boundary: the decision log is a session artefact, and making it
  durable would mean adding a database, which the brief does not ask for.

---

## Option 2 — Local (recommended for the demonstration itself)

For the viva, run it on your own machine. No network, no cold start, nothing to
go wrong in front of an examiner.

```bash
cd control_tower
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python generate_data.py
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`. To let someone on the same network see it:

```bash
streamlit run dashboard/app.py --server.address=0.0.0.0
```

They then browse to `http://<your-ip>:8501`.

---

## Option 3 — Docker (any host)

A `Dockerfile` is included. It builds the dataset at image-build time so the
first request is fast, and reads `$PORT` so it works on hosts that assign one.

```bash
docker build -t control-tower .
docker run -p 8501:8501 control-tower
```

This image runs unchanged on **Render**, **Railway**, **Fly.io**, **Azure
Container Apps**, **Google Cloud Run** or a university server.

**Render**, as a worked example:

1. Push to GitHub
2. New → Web Service → connect the repo
3. Runtime: **Docker** (it finds the `Dockerfile` automatically)
4. Instance type: Free
5. Deploy

Render injects `$PORT`; the Dockerfile already honours it. The free tier also
sleeps after 15 minutes of inactivity, with a ~50-second cold start.

---

## Option 4 — Hugging Face Spaces

Free, generous, and no cold-start penalty on the CPU basic tier.

1. Create a Space at [huggingface.co/new-space](https://huggingface.co/new-space)
2. SDK: **Streamlit**
3. Push your code to the Space's git remote
4. Add a `README.md` header at the repo root so the Space knows the entry point:

```yaml
---
title: Manufacturing Operations Control Tower
emoji: 🏭
sdk: streamlit
sdk_version: 1.63.0
app_file: dashboard/app.py
pinned: false
---
```

Put that block at the very top of `README.md`, above the existing title.

---

## Resource requirements

Comfortably inside every free tier:

| | Measured |
|---|---|
| Full pipeline run | ~2 seconds |
| Memory | well under 500 MB |
| Dependencies | 4 packages |
| Dataset | ~30 KB |
| Cold start (after install) | ~5 seconds |

The pipeline result is cached with `st.cache_resource`, so it runs once and every
page navigation is instant. It only recomputes when you change the scheduling
window, the dispatching rule, or approve a decision.

---

## Deployment checklist

- [ ] `requirements.txt` is committed, and `playwright` stays commented out —
      it is only for the browser test and would bloat the build
- [ ] Main file path is `dashboard/app.py`, not `app.py`
- [ ] `.streamlit/config.toml` is committed (it carries the light theme the
      colour palette was validated against)
- [ ] `python -m unittest discover -s tests` passes before you push
- [ ] Open the deployed URL once and click through all 13 pages
- [ ] App opened and warmed on the morning of the presentation
- [ ] Local copy running as a fallback

---

## Troubleshooting

**"ModuleNotFoundError: No module named 'engine'"**
The main file path is wrong. It must be `dashboard/app.py` — the app inserts its
parent directory on `sys.path` and that only resolves when it is launched as the
main file.

**"Missing input file(s)"**
Only happens if you run `run_pipeline.py` before `generate_data.py`. The
dashboard builds the dataset itself; the CLI runner does not.

**The app is slow on first load**
Expected: it is running the whole pipeline and, if the CSVs were not committed,
generating the dataset first. Subsequent navigation is served from cache.

**Charts render but look washed out**
`.streamlit/config.toml` did not get committed. The palette is validated against
the light surface `#fcfcfb` declared there.

# Pounce on Railway

This directory carries two deliberately distinct Railway images:

- `Dockerfile` is the reproducible release recipe. It installs a pinned
  published `bengal-pounce` version.
- `Dockerfile.canary` installs the current repository checkout. A
  GitHub-connected Railway service uses it to redeploy every merge to `main`.

Both install free-threaded CPython 3.14t with `uv`, force `PYTHON_GIL=0`, refuse
to boot if the GIL is enabled, bind `0.0.0.0:$PORT`, and use Pounce's built-in
`/readyz` endpoint to gate deployment activation.

The image requires uv's managed interpreter and installs it under
`/opt/uv-python`, so the GIL-enabled Python already present in the base image
cannot satisfy the `3.14t` request and the non-root runtime user can read the
selected interpreter.

Railway's config keeps one replica, overlaps the old deployment for five
seconds, and allows 15 seconds between `SIGTERM` and `SIGKILL`. Pounce's own
shutdown timeout is 10 seconds, leaving the platform a five-second safety
margin.

## Deploy

Create a fresh Railway service, generate a public domain, and use this
directory as the service root. The checked-in `railway.toml` selects the
Dockerfile and healthcheck automatically.

```bash
railway up --new --name pounce-railway-recipe
railway domain generate
```

For a GitHub-connected service, set the repository root directory to
`/examples/deploy/railway`.

The Dockerfile pins the latest published Pounce release known to this recipe.
Override the `POUNCE_VERSION` build argument deliberately when validating a
newer release candidate.

## Main-branch canary

The repository canary is a best-effort external integration, not a release or
uptime guarantee. Railway watches `lbliii/pounce` on `main`, builds from the
repository root with `examples/deploy/railway/Dockerfile.canary`, and overlaps
the old deployment while the new commit passes `/readyz`.

After every merge, `.github/workflows/railway-main-canary.yml` polls the public
app until it reports that exact `RAILWAY_GIT_COMMIT_SHA`, then verifies `/`,
`/readyz`, `/slow`, and `/stream`. The probe needs no Railway token because it
only reads the public canary. The root JSON exposes the non-secret deployment
channel, Pounce/Python versions, GIL state, git commit, and branch; never place
secrets in those identity fields.

The current canary origin is
<https://pounce-railway-smoke-production.up.railway.app>. Run the same probe
locally with:

```bash
python canary_probe.py \
  --origin https://pounce-railway-smoke-production.up.railway.app \
  --expected-sha "$(git rev-parse HEAD)"
```

## Local container proof

```bash
docker build -t pounce-railway-recipe .
docker run --rm -e PORT=8000 -p 8000:8000 pounce-railway-recipe
curl --fail http://127.0.0.1:8000/readyz
curl --fail http://127.0.0.1:8000/
curl --fail http://127.0.0.1:8000/stream
```

The root response must include `"gil_enabled":false`.

## Railway deploy and redeploy smoke

The smoke runner always requires explicit target identifiers so it cannot
silently deploy over whichever project happens to be linked locally. It waits
for terminal `SUCCESS`; a queued upload is not treated as a deployment.

```bash
python smoke.py \
  --project PROJECT_ID \
  --environment production \
  --service SERVICE_ID \
  --origin https://SERVICE.up.railway.app
```

It performs an initial deploy, waits for `/readyz`, verifies sample traffic and
the GIL-off response, then uploads a second deployment while continuously
probing both fast and slow requests. Any dropped request, non-200 response,
failed deployment, or readiness failure makes the script exit nonzero.

Railway healthchecks are deployment-admission checks, not continuous monitors.
Keep a separate external uptime monitor when continuous health verification is
required.

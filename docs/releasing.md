# Releasing, the demo site and the container

Three things publish this project. Each one needs a one-time switch on GitHub or PyPI and
then runs by itself.

## The demo site (GitHub Pages)

`.github/workflows/pages.yml` rebuilds the demo on every push to `main` that touches the
code. It runs `scripts/build_demo.sh`: 300 seeded hands between the five scripted bots,
exported with the odds panel and a coach report per seat. The same seed always builds the
same site.

Once, set Pages to deploy from GitHub Actions: Settings → Pages → Source: GitHub Actions. From
a terminal:

```sh
gh api -X POST repos/amirradjou/poker-table/pages -f build_type=workflow
gh workflow run pages.yml          # the first build, without waiting for a push
```

The site is then at https://amirradjou.github.io/poker-table/. To preview a build locally:

```sh
scripts/build_demo.sh site 300 && python3 -m http.server -d site 8000
```

## A release on PyPI

`.github/workflows/release.yml` runs on a `v*` tag. It checks that the tag, `pyproject.toml`
and `poker_table.__version__` agree, runs the tests, and builds the wheel, checking that the
viewer's static files are inside. It then publishes to PyPI with trusted publishing, so no
token is stored, and attaches the same files to a GitHub release.

Once, on PyPI (Your projects → Publishing → Add a pending publisher):

| field | value |
|---|---|
| project name | `poker-table` |
| owner / repository | `amirradjou` / `poker-table` |
| workflow | `release.yml` |
| environment | `pypi` |

For each release, bump both version strings in one commit, merge, then tag `main`:

```sh
uv version 0.2.0                                   # pyproject.toml
sed -i 's/__version__ = .*/__version__ = "0.2.0"/' src/poker_table/__init__.py
git tag v0.2.0 && git push origin v0.2.0
```

## The container

`Dockerfile` builds a slim image (uv for the build, a non-root `player` user at run time)
whose default command is a live table at a watchable pace on port 8765. `compose.yaml` keeps
the hands in a volume. CI builds the image on every pull request, checks that a live table
inside it serves hands, and plays a short session with `docker run`.

```sh
docker compose up -d && open http://localhost:8765
docker run --rm poker-table play -n 200 --seats tag,maniac,station
```

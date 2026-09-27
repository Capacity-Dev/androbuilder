# Publishing androbuilder

Four channels are supported, in increasing order of effort:
**PyPI → Homebrew → winget → apt**.

---

## 1. PyPI (pip / pipx / uv) — primary

Anything on PyPI is immediately installable with `pip`, `pipx`, or `uv`:

```bash
pipx install androbuilder
uv tool install androbuilder
```

### One-time setup
1. Create the project on <https://pypi.org> (or use the pending-publisher flow).
2. Configure **Trusted Publishing** (no token needed):
   PyPI → project → *Publishing* → add a GitHub publisher with
   `owner/repo`, workflow `release.yml`, environment `pypi`.
3. In the GitHub repo, create an **environment** named `pypi`.

### Release
```bash
# bump version in pyproject.toml and src/androbuilder/__init__.py, then:
git tag v0.1.0 && git push origin v0.1.0
```
`.github/workflows/release.yml` builds the sdist+wheel and publishes via OIDC.

### Manual (alternative)
```bash
python -m build
twine check dist/*
twine upload dist/*        # uses ~/.pypirc or TWINE_* env vars
```

---

## 2. Homebrew (macOS/Linux)

A **tap** is the simple route (`homebrew-core` requires notability).

1. Create a repo named `homebrew-tap` under your org/user.
2. Copy `packaging/homebrew/androbuilder.rb` into `Formula/androbuilder.rb`.
3. Update `url` (the PyPI sdist URL) and `sha256`:
   `shasum -a 256 dist/androbuilder-<ver>.tar.gz`.
4. Generate the dependency resources:
   ```bash
   brew update-python-resources Formula/androbuilder.rb
   ```
5. Users install with:
   ```bash
   brew install Capacity-Dev/tap/androbuilder
   ```

Automate 3–4 with a workflow in the tap repo triggered on release, or a
`brew bump-formula-pr` step.

---

## 3. winget (Windows)

winget ships installers/portable archives, not Python packages, so bundle a
standalone binary.

1. Build on Windows (GitHub runner `windows-latest`):
   ```powershell
   pip install pyinstaller
   pyinstaller --onefile --name androbuilder src/androbuilder/__main__.py
   ```
2. Zip `androbuilder.exe` and attach it to the GitHub Release
   (e.g. `androbuilder-windows-x64.zip`).
3. Fill in `packaging/winget/androbuilder.installer.yaml` (URL + SHA256).
4. Submit:
   ```bash
   wingetcreate submit packaging/winget/androbuilder.installer.yaml
   ```
   This opens a PR against `microsoft/winget-pkgs`.

Users: `winget install Capacity-Dev.androbuilder`.

---

## 4. apt (Debian/Ubuntu)

Not for the official Ubuntu/Debian archives (packaging boto3/fabric as debs is
heavy). Host your own repo instead.

1. Build the package:
   ```bash
   ./packaging/deb/build-deb.sh 0.1.0 amd64     # requires fpm + python3-venv
   ```
2. Publish to a signed repo (any of: `aptly`, `reprepro`, GitHub Pages, S3) with a
   GPG key, or just attach the `.deb` to a GitHub Release.
3. Users add your repo/key and run `apt install androbuilder`.

You can replace `fpm` with `dh-virtualenv` if you want a more Debian-native build.

---

## Version bumping checklist

- [ ] `pyproject.toml` → `project.version`
- [ ] `src/androbuilder/__init__.py` → `__version__`
- [ ] Commit, tag `vX.Y.Z`, push the tag (triggers PyPI)
- [ ] Homebrew: bump url/sha256 + `brew update-python-resources`
- [ ] winget/apt: rebuild artifacts, update manifests

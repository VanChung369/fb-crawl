# fb-crawl

`fb-crawl` provides explicit public HTTP and authenticated browser modes. This phase implements the public CLI; it never reads a browser session or starts Selenium.

## Requirements

- Python 3.12+
- Access only to data you are authorized to collect

## Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Public commands

```powershell
fb-crawl public page https://www.facebook.com/example
fb-crawl public search --keyword "spa" --target pages --limit 20
fb-crawl public crawl https://www.facebook.com/example --depth 1 --max-nodes 20
fb-crawl public crawl https://www.facebook.com/groups/pythonvn --target all --depth 0
```

Use `--format json` for the full result envelope or `--output PATH` to select a destination. Default output is `runtime/output/pages.csv`.

Detailed options and output schemas are documented in [docs/public-cli.md](docs/public-cli.md).

## Exit codes

- `0`: run completed without target failures
- `1`: partial target failure; successful records remain available
- `2`: invalid input or configuration
- `4`: output could not be written safely

## Privacy and safety

Generated data is written under Git-ignored `runtime/`. Public mode does not use cookies or login credentials. The project does not bypass access controls, CAPTCHA, checkpoints, or two-factor authentication.

## Development checks

```powershell
python -m pytest -q
python -m compileall -q src tests
python -m pip check
git diff --check
```

# Public CLI guide

The public CLI collects information available through public HTTP pages. It does not import Selenium, read browser sessions, use login credentials, or automatically fall back to authenticated mode.

## Installation

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Confirm the command is installed:

```powershell
fb-crawl --help
fb-crawl public --help
```

## Commands

### Direct page or profile URLs

```powershell
fb-crawl public page URL [URL ...]
```

Example:

```powershell
fb-crawl public page `
  https://www.facebook.com/example `
  https://www.facebook.com/profile.php?id=100013976614656 `
  --target all `
  --limit 20
```

Each valid page or profile URL is normalized and processed independently. A failed target does not remove successful records from the result.

### Keyword search

```powershell
fb-crawl public search --keyword KEYWORD
```

Example:

```powershell
fb-crawl public search `
  --keyword "spa ho chi minh" `
  --target pages `
  --limit 20 `
  --format json
```

Discovery uses a deterministic fallback order:

1. DuckDuckGo HTML results
2. Bing RSS when DuckDuckGo has no usable results
3. Facebook public directory for `people` and `all` targets when more results are needed

### Bounded crawl

```powershell
fb-crawl public crawl URL [URL ...]
```

Example:

```powershell
fb-crawl public crawl `
  https://www.facebook.com/example `
  --depth 1 `
  --max-nodes 20 `
  --delay 0.5
```

Crawl behavior is breadth-first, deduplicated, depth-bounded, and node-bounded.

A public group URL can be used as a discovery seed:

```powershell
fb-crawl public crawl `
  https://www.facebook.com/groups/pythonvn `
  --target all `
  --depth 0 `
  --max-nodes 20
```

The group is used to discover public page/profile targets. It is not emitted as a page/profile result.

## Common options

| Option                        |        Default | Description                                                           |
| ----------------------------- | -------------: | --------------------------------------------------------------------- |
| `--target pages\|people\|all` |        `pages` | Select the type of Facebook target to accept.                         |
| `--limit NUMBER`              |           `20` | Maximum initial targets or search results. Must be greater than zero. |
| `--delay SECONDS`             |            `0` | Delay between queued requests. Must be zero or greater.               |
| `--output PATH`               | Generated path | Select the destination file.                                          |
| `--format csv\|json`          |          `csv` | Select flat CSV or the complete JSON envelope.                        |
| `--timeout SECONDS`           |           `20` | Override the HTTP timeout. Must be greater than zero.                 |
| `--max-retries NUMBER`        |            `2` | Override retry count. Must be zero or greater.                        |

The crawl command also supports:

| Option               |         Default | Description                                    |
| -------------------- | --------------: | ---------------------------------------------- |
| `--depth NUMBER`     |             `1` | Maximum discovery depth. A seed has depth `0`. |
| `--max-nodes NUMBER` | `--limit` value | Maximum number of processed targets.           |

## Configuration precedence

Configuration is resolved in this order:

1. CLI option
2. Environment variable
3. Built-in default

Supported environment variables:

```dotenv
FB_CRAWL_TIMEOUT_SECONDS=20
FB_CRAWL_MAX_RETRIES=2
FB_CRAWL_OUTPUT_DIR=runtime/output
```

## Output paths

Without `--output`, results are written to:

```text
runtime/output/pages.csv
```

When `--format json` is selected:

```text
runtime/output/pages.json
```

The complete `runtime/` tree is ignored by Git.

Writes use a temporary file in the destination directory followed by an atomic replacement. If a result contains no records and no issues, an existing destination is preserved and the summary reports `output=unchanged`.

## CSV schema

CSV files use UTF-8 with a BOM for spreadsheet compatibility.

| Field           | Description                                                        |
| --------------- | ------------------------------------------------------------------ |
| `user_id`       | Facebook page/profile identifier when found.                       |
| `name`          | Common display name; public records use the parsed page name.       |
| `username`      | Vanity username derived from a supported profile URL.              |
| `page_name`     | Parsed public page/profile name; empty for authenticated users.     |
| `category`      | Public profile category; empty when not applicable.                |
| `website`       | Public website value; empty when not applicable.                   |
| `address`       | Public address value; empty when not available or not applicable.  |
| `phone_numbers` | Phone values separated by `; `.                                    |
| `phone_sources` | Sources contributing phone values.                                 |
| `profile_url`   | Canonical Facebook page/profile URL.                               |
| `source`        | Discovery source for records or action name for issue rows.        |
| `source_url`    | URL from which the record was collected or the failed target URL.  |
| `error_code`    | Stable error code for an issue row.                                |
| `error_message` | Safe error message for an issue row.                               |

Successful records are written first. Issues are then written as separate rows without discarding successful data.

## JSON schema

JSON output contains a complete result envelope:

```json
{
  "records": [],
  "issues": [],
  "stats": {
    "requested": 0,
    "discovered": 0,
    "succeeded": 0,
    "failed": 0
  }
}
```

Every item in `records` uses the same fields as CSV. Every item in `issues`
uses that schema with record fields empty, `source` set to the action,
`source_url` set to the failed target, and the safe error fields populated.
This schema is shared with authenticated output so downstream consumers can
load both modes without separate column mappings.

## Exit codes

| Code | Meaning                                                          |
| ---: | ---------------------------------------------------------------- |
|  `0` | Run completed without target failures.                           |
|  `1` | One or more targets failed; successful records remain available. |
|  `2` | Invalid arguments, request, or configuration.                    |
|  `4` | Output could not be written safely.                              |

## Privacy and operational boundaries

Only collect data you are authorized to access and process.

Public mode:

- does not use cookies or login credentials;
- does not read browser-session files;
- does not start Selenium or another browser;
- does not bypass access controls, CAPTCHA, checkpoints, or two-factor authentication;
- does not automatically switch to authenticated mode.

Facebook and search-provider HTML can change. Parser or discovery failures are returned as safe issues rather than exposing query parameters or transport details.

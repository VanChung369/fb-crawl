# Thiết kế hợp nhất `fb-crawl`

## Bối cảnh

Workspace hiện có hai codebase Python độc lập:

- `craw` thu thập dữ liệu Facebook công khai bằng HTTP. Nó hỗ trợ URL trực tiếp, tìm kiếm theo từ khóa, discovery/crawl liên kết, lấy thông tin page/profile, UID, số điện thoại và xuất CSV.
- `Facebook-Data-Scraping-Tools` dùng Selenium với phiên đăng nhập để lấy thành viên group và người bình luận bài viết. Nó có session persistence, proxy, batch input và xuất TXT/CSV/JSON/XLSX.

Hai codebase đều có logic chuẩn hóa URL, parse HTML, điều phối scraping, ghi output và xử lý lỗi, nhưng các phần này đang nằm trong script cấp cao và khó tái sử dụng. Project mới cần hợp nhất hai khả năng mà vẫn giữ ranh giới rõ giữa truy cập công khai và truy cập có phiên đăng nhập.

Project đích là `D:\project\fb\fb-crawl`. Hai project nguồn được giữ nguyên làm bản đối chiếu trong suốt quá trình migration. Không di chuyển hoặc sao chép session thật, output thật, notebook hay cache vào project mới.

## Mục tiêu

- Cung cấp hai chế độ được chọn tường minh: `public` và `authenticated`.
- Dùng CLI làm giao diện đầu tiên.
- Đặt toàn bộ logic nghiệp vụ trong service layer để Web UI và API tương lai có thể gọi lại mà không sao chép logic scraping.
- Giữ và chuyển các hành vi hữu ích đang được kiểm thử trong hai project nguồn.
- Cô lập HTTP, Selenium, parsing, output và session thành các module có trách nhiệm đơn nhất.
- Bảo vệ thông tin đăng nhập, cookie, session và dữ liệu output khỏi Git và log.
- Hỗ trợ batch theo từng target, trong đó lỗi một target không làm mất kết quả của target khác.

## Không thuộc phạm vi phase CLI

- Web UI hoặc HTTP API.
- Job queue, scheduler, database hoặc distributed workers.
- Tự động fallback từ `public` sang `authenticated`.
- Bypass CAPTCHA, checkpoint, 2FA, account recovery hoặc cơ chế bảo vệ của Facebook.
- Đảm bảo selector hoặc HTML Facebook luôn ổn định.
- Xóa hoặc sửa hai project nguồn sau khi migration.

Web UI và API sẽ là phase riêng sau khi CLI và service contract ổn định. Trước khi public API được mở ra Internet, phase API phải bổ sung authentication, rate limiting và chính sách chống SSRF cho mọi URL outbound.

## Các phương án đã cân nhắc

### 1. Một modular package dùng chung service layer — được chọn

Tách core models, services, HTTP adapters, browser adapters, exporters và CLI. Phương án này cần migration có kiểm soát nhưng tạo ranh giới rõ, hỗ trợ test độc lập và cho phép Web UI/API dùng lại services.

### 2. Giữ gần nguyên hai codebase và bọc bằng một CLI

Phương án này nhanh hơn ban đầu nhưng giữ lại orchestration trùng lặp, model output không đồng nhất và sự phụ thuộc giữa CLI với Selenium/parser. Web UI/API sau đó vẫn phải refactor lại.

### 3. Plugin architecture đầy đủ

Mỗi scraper là plugin được đăng ký động. Nó linh hoạt nhưng thêm registry, lifecycle, discovery và configuration chưa cần thiết cho hai mode hiện tại. Đây là over-engineering cho phase đầu.

## Kiến trúc được chọn

Project dùng Python 3.12 trở lên, `src` layout, standard-library `argparse` cho CLI và `dataclasses` cho domain models. Service layer chỉ phụ thuộc vào protocol/interface nhỏ và domain types; dependency cụ thể được CLI ghép nối ở composition root.

```text
fb-crawl/
├── pyproject.toml
├── README.md
├── .gitignore
├── .env.example
├── src/
│   └── fb_crawl/
│       ├── __init__.py
│       ├── config.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── models.py
│       │   ├── urls.py
│       │   └── exceptions.py
│       ├── services/
│       │   ├── __init__.py
│       │   ├── public.py
│       │   └── authenticated.py
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── http/
│       │   │   ├── __init__.py
│       │   │   ├── client.py
│       │   │   ├── discovery.py
│       │   │   ├── page_parser.py
│       │   │   └── contact_parser.py
│       │   └── browser/
│       │       ├── __init__.py
│       │       ├── driver.py
│       │       ├── login.py
│       │       ├── session.py
│       │       ├── members.py
│       │       └── comments.py
│       ├── exporters/
│       │   ├── __init__.py
│       │   ├── csv.py
│       │   ├── json.py
│       │   ├── text.py
│       │   └── xlsx.py
│       └── cli/
│           ├── __init__.py
│           ├── app.py
│           ├── public.py
│           └── authenticated.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
└── runtime/
    ├── output/
    └── session.json
```

`runtime/` được Git ignore toàn bộ. Các thư mục rỗng không cần được commit; ứng dụng tạo chúng đúng lúc khi có dữ liệu hợp lệ cần ghi.

## Ranh giới module

### `config.py`

Đọc default và environment variables có prefix `FB_CRAWL_`, sau đó hợp nhất với override từ CLI. Module trả typed configuration nhưng không khởi tạo HTTP client, browser hoặc service.

### `core`

Chứa kiểu dữ liệu và quy tắc thuần, không import Selenium, HTTP client, BeautifulSoup, Selectolax, CLI hoặc exporter.

- `models.py`: `ScrapeMode`, `ScrapeRequest`, `ContactRecord`, `PageRecord`, `UserRecord`, `ScrapeIssue`, `ScrapeStats` và generic `ScrapeResult`.
- `urls.py`: chuẩn hóa, phân loại và validate Facebook URL dùng chung.
- `exceptions.py`: exception có mã ổn định cho validation, session, fetch, parsing và export.

### `services`

Điều phối use case, nhận dependency qua constructor và trả `ScrapeResult` thay vì in hoặc ghi file trực tiếp.

- `PublicService`: URL trực tiếp, keyword discovery, public group discovery, page/profile scraping và bounded crawl.
- `AuthenticatedService`: session lifecycle, group-member extraction, post-comment extraction và batch orchestration.

Không service nào tự chuyển sang mode còn lại. `PublicService` không đọc session và không khởi tạo browser. `AuthenticatedService` chỉ chạy khi người dùng chọn mode authenticated.

### `adapters/http`

Chứa phần được migration từ `craw`:

- HTTP client có timeout, header và lỗi tập trung.
- DuckDuckGo/Bing/public-directory discovery.
- Facebook page/profile parser.
- UID/contact/phone parser và optional website enrichment.

### `adapters/browser`

Chứa phần được migration từ `Facebook-Data-Scraping-Tools`:

- Firefox options và proxy.
- Login, checkpoint/manual verification và session validation.
- Đọc/ghi cookie session an toàn, atomic.
- Bounded scrolling cho group members.
- Bounded comment expansion cho posts.
- Chuyển browser HTML thành `UserRecord`.

### `exporters`

Mỗi exporter nhận records/domain result và một path. Export là atomic: ghi vào file tạm cùng thư mục, flush/close thành công rồi mới replace destination. Kết quả rỗng không ghi đè file đang tồn tại.

### `cli`

Chỉ parse argument, tạo dependency, gọi service, gọi exporter và map kết quả sang stdout/stderr/exit code. CLI không chứa selector, parsing, scrolling, session JSON hoặc business rules.

## CLI contract

Entry point được cài từ `pyproject.toml` là `fb-crawl`.

```text
fb-crawl public page URL [URL ...]
fb-crawl public search --keyword TEXT [--target pages|people|all]
fb-crawl public crawl URL [URL ...] --depth N --max-nodes N

fb-crawl authenticated members URL
fb-crawl authenticated comments URL
fb-crawl authenticated batch --input PATH
```

Các option chung gồm `--output`, `--format`, `--limit`, `--delay` và mức log. Browser commands có thêm `--headless`, `--proxy`, bounded `--steps` và `--verification-timeout` với mặc định 300 giây. Format hợp lệ phụ thuộc record type nhưng exporter hỗ trợ CSV/JSON cho mọi loại, TXT cho user records và XLSX khi extra `xlsx` được cài.

Format mặc định là CSV. Khi người dùng không truyền `--output`, các path mặc định là `runtime/output/pages.csv`, `runtime/output/members.csv`, `runtime/output/comments.csv` và `runtime/output/batch.csv` theo action. Nhờ vậy các action không âm thầm ghi đè output của nhau.

Không có `auto` mode trong phase đầu. Người dùng luôn biết session có được dùng hay không.

## Domain models

### `ScrapeRequest`

Chứa mode, action, targets, giới hạn, delay, crawl depth và browser steps đã validate. Output path/format thuộc `ExportOptions` ở boundary CLI/exporter, không nằm trong request của service. CLI/API adapter tương lai chịu trách nhiệm chuyển input thô thành model này.

### `PageRecord`

Chứa canonical URL, page name, UID, category, website, danh sách contact có source, page metadata, crawl depth và discovery source. Phone number được biểu diễn bằng record có `value` và `sources` thay vì hai list song song.

### `UserRecord`

Chứa user ID hoặc handle, tên, canonical profile URL, source type và source URL. Việc deduplicate ưu tiên ID; nếu chỉ có handle thì dùng normalized handle.

### `ScrapeIssue`

Chứa code, message an toàn, target, mode, action và cờ retryable. Nó không chứa cookie, password, raw session JSON hoặc full HTML.

### `ScrapeResult`

Chứa records, issues và stats cho toàn bộ run. Service trả result ngay cả khi một số target thất bại; lỗi cấu hình hoặc session không hợp lệ trước khi bắt đầu là exception cấp run.

## Data flow

### Public mode

1. CLI validate argument và tạo `ScrapeRequest`.
2. `PublicService` chuẩn hóa URL, loại trùng và áp dụng limit.
3. Discovery adapter tìm target khi input là keyword/source/crawl.
4. HTTP adapter fetch public HTML với timeout hữu hạn.
5. Parser tạo `PageRecord` và contact records.
6. Service tích lũy records/issues/stats trong `ScrapeResult`.
7. Exporter ghi kết quả atomic vào `runtime/output/` hoặc path người dùng chỉ định.
8. CLI hiển thị summary và exit code.

### Authenticated mode

1. CLI validate URL/action và tạo `ScrapeRequest`.
2. Browser factory khởi tạo Firefox với headless/proxy config.
3. Session adapter thử khôi phục `runtime/session.json`.
4. Nếu session không hợp lệ: headless mode dừng với hướng dẫn; interactive mode cho phép đăng nhập và hoàn tất checkpoint thủ công rồi lưu session đã xác nhận.
5. Browser adapter mở target, xác nhận session, scroll/click trong giới hạn và lấy page source.
6. Parser tạo `UserRecord` và service tích lũy result theo target.
7. Exporter ghi kết quả atomic.
8. Browser luôn đóng trong `finally`.

## Cấu hình và dependency

`pyproject.toml` là nguồn dependency duy nhất. Base dependencies cho public mode là `curl-cffi` và `selectolax`. Extra `browser` chứa `selenium` và package chính xác `beautifulsoup4` thay cho package shim `bs4`; extra `xlsx` chứa `openpyxl`; test/development dependencies nằm trong extra `dev`. Hướng dẫn cài đặt CLI đầy đủ dùng `pip install -e ".[browser,xlsx]"`, còn contributor dùng `pip install -e ".[browser,xlsx,dev]"`. Nếu người dùng gọi authenticated/XLSX command khi thiếu extra tương ứng, CLI trả lỗi cấu hình tập trung thay vì traceback import.

Cấu hình có precedence rõ:

1. CLI option.
2. Environment variable có prefix `FB_CRAWL_`.
3. Default an toàn trong code.

`.env.example` chỉ ghi tên biến và giá trị mẫu không nhạy cảm. Project không tự đọc `.env` trong phase đầu và không lưu email/password. Session path mặc định là `runtime/session.json`.

## Xử lý lỗi và exit code

- `0`: run hoàn tất, có thể có warning không làm mất target result.
- `1`: run thực hiện nhưng một hoặc nhiều target thất bại; kết quả thành công vẫn được export.
- `2`: input/configuration không hợp lệ và scraping chưa bắt đầu.
- `3`: authenticated session/login/checkpoint không sẵn sàng.
- `4`: không thể ghi output an toàn.

Public batch cô lập lỗi theo target. HTTP fetch lỗi tạo `ScrapeIssue` và tiếp tục khi còn target. Browser batch cũng cô lập lỗi target, nhưng session mất hiệu lực dừng toàn run vì mọi target tiếp theo đều không đáng tin cậy.

Parser không tìm thấy dữ liệu hợp lệ trả warning có cấu trúc. Exception không mong đợi được giữ nguyên cause cho debug nội bộ nhưng message CLI phải được sanitize. Không retry vô hạn. HTTP retry, nếu triển khai, tối đa hai lần cho lỗi tạm thời với backoff; browser action không được retry blind khi session/checkpoint chưa rõ.

## Bảo mật và dữ liệu

- Chỉ thu thập dữ liệu mà người vận hành được phép truy cập; project không vượt qua access control.
- Password được nhập qua `getpass` trong interactive flow, không qua CLI argument, file hoặc log.
- Session chỉ được lưu sau khi `c_user` tồn tại và URL không phải login/checkpoint/2FA.
- Session được ghi atomic, permission owner-only trên nền tảng hỗ trợ và luôn nằm dưới path Git-ignore.
- Không log cookie value, password, raw session, verification URL có query nhạy cảm hoặc full HTML.
- Generated output, session, cache, browser logs và temporary files nằm dưới `runtime/` và bị ignore.
- Project mới không copy `.facebook_session.json`, `results.csv`, `output/`, `*.ipynb`, `__pycache__/` hoặc `geckodriver.log` từ nguồn.

## Testing

### Unit tests

- URL normalization, classification và deduplication.
- Public discovery và search-result parsing.
- Page/profile/contact/phone/UID parsing.
- User-record extraction từ group/comment fixtures.
- Domain model validation và result aggregation.
- Exporter format, atomic replacement và empty-result preservation.
- Session compatibility, validation và secret-safe errors.
- Scroll/comment loops luôn hữu hạn.
- CLI parsing, option precedence và exit-code mapping.

### Integration tests

- Public service chạy qua fake HTTP client với HTML/RSS fixtures.
- Authenticated service chạy qua fake browser/session adapters.
- Batch giữ record thành công khi target khác lỗi.
- Public CLI path không import hoặc khởi tạo Selenium.
- CLI-to-export flow tạo đúng schema trong temporary directory.

Automated tests không đăng nhập Facebook thật và không gọi live Facebook. Manual smoke test xác nhận public page, interactive session bootstrap, group members và post comments chỉ được thực hiện sau khi offline suite pass.

Các regression tests tương ứng với 27 tests của `craw` và 45 tests của project Selenium được migration theo hành vi, không copy máy móc theo module path cũ. Kiểm tra phụ thuộc Git phải chạy trong Git repository mới, khắc phục nguyên nhân ba subtest hiện thất bại ở source copy không có `.git`.

## Trình tự migration

1. Khởi tạo Git, `pyproject.toml`, package skeleton, `.gitignore`, README tối thiểu và test runner.
2. Viết tests rồi tạo core models, exceptions, config và URL utilities.
3. Migration pure public parsers và regression tests.
4. Migration HTTP client, discovery, public service và public CLI commands.
5. Migration browser config, login/session và fake-driver tests.
6. Migration member/comment adapters, authenticated service và authenticated CLI commands.
7. Hợp nhất exporters, atomic output và batch result schema.
8. Chạy toàn bộ offline tests, compile checks, dependency checks và CLI smoke tests.
9. Viết README hướng dẫn cài đặt, session bootstrap và từng command.
10. Chạy manual smoke tests có kiểm soát; không xóa hai project nguồn.

Mỗi bước migration phải giữ test suite xanh trước khi sang bước sau. Logic được copy theo phần nhỏ rồi refactor tại boundary mới; không copy nguyên các script lớn vào package mới.

## Tiêu chí chấp nhận phase CLI

- `fb-crawl --help`, `fb-crawl public --help` và `fb-crawl authenticated --help` chạy thành công.
- Public và authenticated là hai mode tường minh, không có fallback ẩn.
- Public commands không cần browser hoặc session để khởi động.
- Authenticated commands khôi phục session an toàn, hỗ trợ manual verification và luôn đóng browser.
- Page và user outputs dùng typed records, schema ổn định và atomic write.
- Batch giữ kết quả từng target và báo lỗi có cấu trúc.
- Các hành vi hữu ích từ cả hai project nguồn có regression coverage trong cấu trúc mới.
- Automated tests không cần network hoặc tài khoản Facebook.
- Session/output/cache không xuất hiện trong Git status.
- Service layer có thể được gọi trực tiếp mà không import CLI, tạo điểm mở rộng cho Web UI/API.
- Hai project nguồn và mọi dữ liệu/session hiện có vẫn nguyên vẹn.

## Phase tiếp theo

Sau khi phase CLI được nghiệm thu, Web UI và API được thiết kế như adapter đầu vào mới gọi cùng services. API phase phải có authentication, authorization, rate limiting, job lifecycle, SSRF protection và secret management trước khi deploy. Những yêu cầu đó không làm thay đổi domain/service contract đã định nghĩa ở phase CLI.

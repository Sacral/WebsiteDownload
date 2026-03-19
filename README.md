# 網頁資源下載器（離線可開啟）
因為公司不能安裝chrome的網站下載器擴充套件，但為了將pm產出的規格網站資源完整下載下來，以利做後續的規格文件比對，而使用 cursor codex5.3 產生的小工具

# 說明
這個工具不需要瀏覽器外掛，會直接在本機用 Chromium 引擎載入網頁，並把載入期間的資源存下來，產生可離線開啟的 `index.html`。

## 1) 安裝

1. 安裝 Python 3.10+  
2. 安裝套件與瀏覽器核心：

```bash
python -m pip install playwright
python -m playwright install chromium
```

## 2) 執行下載

```bash
python web_resource_downloader.py "<要下載的網頁URL>" -o offline_axure --include-cross-origin --wait-seconds 5 --scroll-rounds 6
```

若要下載時直接用標題命名資料夾：

```bash
python web_resource_downloader.py "<要下載的網頁URL>" --output-title "<你的輸出資料夾>" --include-cross-origin --wait-seconds 5 --scroll-rounds 6 --browser-channel msedge
```

## 3) 開啟離線頁

下載完成後，直接用瀏覽器開啟：

- `<你的輸出資料夾>/index.html`

若頁面是 Axure/高互動網站，建議改走本機 HTTP（避免 `file://` 限制）：

```bash
cd <你的輸出資料夾>
python -m http.server 8000
```

然後開啟 `http://127.0.0.1:8000/`（也可直接雙擊 `run_offline_server.bat`）。

## 參數說明

- `-o, --output`：輸出資料夾（預設 `offline_site`）
- `--output-title`：直接用這段文字當輸出資料夾名稱（支援中文）
- `--wait-seconds`：頁面載入後額外等待秒數，讓延遲資源也被抓到
- `--scroll-rounds`：自動下滑次數，觸發 lazy-load
- `--include-cross-origin`：是否包含其他網域資源（CDN、字型等）
- `--timeout-ms`：載入逾時時間（毫秒）
- `--browser-channel`：指定系統瀏覽器通道（`auto`/`msedge`/`chrome`）
- `--no-crawl-axure-pages`：關閉 Axure 全頁自動補抓（預設是開啟）

## 輸出內容

- `index.html`：離線入口頁（launcher）
- `index_rewritten.html`：重寫過 URL 的備援入口頁
- `site/`：所有下載到的資源
- `manifest.json`：原始 URL 與本機檔案路徑對照表
- `run_offline_server.bat`：一鍵啟動本機 HTTP 伺服器

## 注意事項

- 某些站點若有嚴格防爬、登入態、或動態 API 驗證，離線版可能仍需補抓流程。
- 若站點依賴即時 API，離線開啟時該互動可能無法完全重現。
- 若公司環境無法下載 Playwright 內建 Chromium，可加上 `--browser-channel msedge` 走本機 Edge。
- 若同時給 `-o` 與 `--output-title`，會以 `--output-title` 為主。

## 延伸文件

- 差異比較工具說明請看： https://github.com/Sacral/WebDiff `README.md`


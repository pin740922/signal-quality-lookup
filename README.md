# 電信訊號品質查詢系統

提供電信業者門市 / 客服人員，依**地址**、**地標**或**經緯度座標**快速查詢指定位置方圓(可調)範圍內的 **4G / 5G** 訊號品質，以利處理客訴或回覆用戶關於住家、生活區域收訊狀況的詢問。

---

## 功能

1. **地址查詢**：輸入「縣/市 → 行政區 → 路名 → 門牌號碼」，系統自動轉換成經/緯度，查詢該位置方圓 500m（可調整）內的訊號格點。
2. **地標查詢**：輸入地標關鍵字（如 `台北101`、`taipei 101`、`台中市政府`），自動定位後查詢方圓 500m 訊號。
3. **地圖視覺化**：以查詢點為圓心畫出 500m 範圍圓，並依 RSRP 訊號強度以顏色標示每個 25m 量測格點。
4. **品質摘要**：自動計算平均 RSRP、SINR、下行速率、弱訊號比例、量測樣本數，並給出整體品質判讀。

---

## 專案結構

```
signal_lookup/
├── postcode_mapping.py   # 台灣 3 碼郵遞區號 → 縣/市 對照
├── districts.py          # 各縣市行政區清單 + 中英對照 (pypinyin)
├── process_data.py       # 來源資料整理（建資料夾 / 搬移 / 合併）
├── app.py                # Flask 後端 (geocoding + 範圍查詢，支援 4G/5G)
├── templates/index.html  # 前端地圖介面 (Leaflet，含中英切換)
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 設定 (環境變數)

| 變數 | 說明 | 預設 |
|------|------|------|
| `SIGNAL_DATA_BASE` | 訊號資料根目錄（其下需有 `4G/All`、`5G/All`） | 本機開發路徑 |
| `GOOGLE_MAPS_KEY` | (選用) Google Maps JS 金鑰；未設定則改用 TGOS / Nominatim 定位 | 空 |
| `TGOS_API_KEY` / `TGOS_APP_ID` | (選用) TGOS API 憑證 | 空 |
| `PORT` | 服務埠 | 5000 |

> **資料不隨程式碼進版控**（約 1.9GB），請另存於雲端，部署時下載到伺服器並以
> `SIGNAL_DATA_BASE` 指向該目錄。本機開發維持預設路徑即可。

---

## 步驟一：整理來源資料

> 此步驟已執行完成。如需重新整理或套用到其他資料夾，可再次執行。

```powershell
cd signal_lookup
$env:PYTHONUTF8="1"
python process_data.py --dry-run   # 先預覽動作
python process_data.py             # 實際執行
```

整理內容：
- **a.** 在來源資料夾建立全台灣 22 個縣/市英文資料夾（如 `Taipei_City`、`New_Taipei_City`）。
- **b.** 依 CSV 檔名前三碼（post code）將檔案搬移到對應縣/市資料夾。
- **c.** 將各縣/市資料夾內的 CSV 合併成一個 `<英文縣市名>.csv`（標頭僅保留一份）。

---

## 步驟二：啟動查詢系統

```powershell
cd signal_lookup
$env:PYTHONUTF8="1"
pip install -r requirements.txt
python app.py
```

啟動後開啟瀏覽器：<http://127.0.0.1:5000>

> 首次啟動會建立各縣市資料邊界框索引（`county_bbox.json`），需數十秒；之後讀快取即可。

---

## 訊號品質分級 (依 RSRP)

| 等級 | RSRP (dBm) | 顏色 |
|------|-----------|------|
| 優良 | ≥ -85 | 綠 |
| 良好 | -85 ~ -95 | 黃綠 |
| 普通 | -95 ~ -105 | 黃 |
| 較弱 | -105 ~ -115 | 橙 |
| 微弱 | < -115 | 紅 |

---

## 部署 (GitHub + 雲端主機)

> GitHub 只存放程式碼，無法直接「執行」Flask；需搭配可跑 Python 的主機
> (Render / Railway / PythonAnywhere / 自架 VM 等)。

1. **資料上雲**：將整理好的 `TWM_MDT_City_25m`（含 `4G/All`、`5G/All` 的合併 CSV）
   壓縮上傳到雲端（Google Drive / GCS / S3…），取得下載連結。
2. **推程式碼到 GitHub**（資料已由 `.gitignore` 排除）：

   ```powershell
   cd signal_lookup
   git init
   git add .
   git commit -m "Initial commit: 電信訊號品質查詢系統"
   git branch -M main
   git remote add origin https://github.com/<帳號>/<repo>.git
   git push -u origin main
   ```

3. **主機端設定**（已內建開機自動下載資料）：
   - 設定環境變數：
     - `DATA_URL` = 雲端 ZIP 的**直接下載**連結（ZIP 內需為 `TWM_MDT_City_25m/{4G,5G}/All/...`）。
     - `SIGNAL_DATA_BASE` = 解壓後的 `TWM_MDT_City_25m` 路徑。
   - 啟動時程式會自動下載並解壓資料（若該路徑已有資料則略過），再建立索引。
   - 啟動指令（已寫入 `Procfile` / `render.yaml`）：

     ```bash
     gunicorn -w 1 --preload --timeout 300 -b 0.0.0.0:$PORT app:app
     ```

   **以 Render 為例**：連結此 GitHub repo → Render 會讀取 `render.yaml` 自動建立服務 →
   在後台把 `DATA_URL` 填入你的雲端連結 → Deploy 即可。

---

## 備註

- 地址定位優先使用 TGOS 全國門牌服務（網頁查詢），地標 / 座標則搭配 Google(若設金鑰) / Nominatim。
- Nominatim 為免費公用服務，請避免短時間大量查詢。
- 資料來源為 4G / 5G MDT 25m 網格量測，欄位含 RSRP、RSRQ、SINR、下行吞吐量、樣本數等。

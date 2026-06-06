# -*- coding: utf-8 -*-
"""電信訊號品質查詢系統 - 後端。

提供門市 / 客服人員快速查詢指定地址或地標方圓 500m 範圍內的 4G 訊號品質。

查詢流程：
  1. 地址 (縣市 + 行政區 + 路名 + 門牌) 或地標關鍵字
  2. 透過 OpenStreetMap Nominatim 轉成經/緯度
  3. 依經緯度找出所屬縣市合併資料，篩出方圓 500m 內的訊號格點
  4. 回傳格點明細與整體品質摘要
"""

import json
import math
import os
import re
import threading
import time
import zipfile

import numpy as np
import pandas as pd
import requests
import urllib3
from flask import Flask, jsonify, render_template, request

from postcode_mapping import all_counties, ZH_TO_EN
from districts import districts_of, districts_of_i18n
from ai_diagnose import diagnose as ai_diagnose, ai_available

# ----------------------------------------------------------------------------
# 設定
# ----------------------------------------------------------------------------
_BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RADIUS_M = 500

# 訊號資料根目錄。預設為本機開發路徑；部署時以環境變數 SIGNAL_DATA_BASE 覆寫
# (例如指向伺服器上由雲端下載解壓後的資料夾)。
DATA_BASE = os.environ.get(
    "SIGNAL_DATA_BASE",
    r"C:\Users\tsuwang\Desktop\我的資料夾\PIN_Cursor\test1\TWM_MDT_City_25m",
)

# 各網路別 (4G / 5G) 的資料來源與欄位對照。
# 4G 與 5G (NR) 的合併檔欄位名稱不同，於此統一映射為內部欄位。
NETWORKS = {
    "4G": {
        "data_dir": os.path.join(DATA_BASE, "4G", "All"),
        "bbox_cache": os.path.join(_BASE, "county_bbox_4g.json"),
        "cols": {
            "lon": "LONGITUDE", "lat": "LATITUDE",
            "rsrp": "AVGRSRP", "rsrq": "AVGRSRQ", "sinr": "AVGPUSCHSINR",
            "dl": "MACTPUTKBPS_DL", "mr": "MR_COUNT", "cqi": "AVGCQI",
        },
        "dl_divisor": 1000.0,  # kbps -> Mbps
    },
    "5G": {
        "data_dir": os.path.join(DATA_BASE, "5G", "All"),
        "bbox_cache": os.path.join(_BASE, "county_bbox_5g.json"),
        "cols": {
            "lon": "LONGITUDE", "lat": "LATITUDE",
            "rsrp": "AVGNRCELLRSRP", "rsrq": "AVGNRCELLRSRQ", "sinr": "AVGNRCELLSINR",
            "dl": "DLAVGTPUT", "mr": "SUMNRMRCOUNT", "cqi": None,
        },
        "dl_divisor": 1.0,  # 已是 Mbps
    },
}
DEFAULT_NET = "4G"


def net_cfg(net):
    """取得網路別設定，未知值回退至預設 4G。"""
    return NETWORKS.get((net or "").upper(), NETWORKS[DEFAULT_NET])

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "TWM-Signal-Lookup/1.0 (telecom store support tool)"

# Google Maps JavaScript API 金鑰 (選用，供前端瀏覽器端地址定位使用)。
# 請以環境變數 GOOGLE_MAPS_KEY 設定；未設定時前端改用 TGOS / Nominatim 定位。
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_KEY", "")

# TGOS 全國門牌地址定位服務 (內政部)。
# 方式一(預設、免申請)：透過 TGOS 公開圖臺 (map.tgos.tw) 的查詢控制器取得門牌座標，
#   不需個人 AppID/APIKey；以瀏覽器相同的方式取得 session 與 CSRF/Request 權杖後查詢。
# 方式二(選用)：若貴單位已申請 AppID/APIKey，可設定後改走官方 Web Service。
TGOS_URL = "https://addr.tgos.tw/addrws/v30/QueryAddr.asmx/QueryAddr"

TGOS_VIEWER_URL = "https://map.tgos.tw/TGOSCloudMap"
TGOS_CTRL_URL = "https://map.tgos.tw/TGOSCloudMap/reqcontroller.go"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# TGOS 圖臺 session 與權杖快取
_tgos_session = None
_tgos_tokens = None  # (csrf_token, request_token)
_tgos_lock = threading.Lock()


def _load_tgos_config():
    appid = os.environ.get("TGOS_APP_ID", "")
    apikey = os.environ.get("TGOS_API_KEY", "")
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tgos_config.json")
    if (not appid or not apikey) and os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                c = json.load(f)
            appid = appid or c.get("app_id", "")
            apikey = apikey or c.get("api_key", "")
        except Exception:  # noqa: BLE001
            pass
    return appid, apikey


TGOS_APP_ID, TGOS_API_KEY = _load_tgos_config()

app = Flask(__name__)
# 讓 templates 變更即時生效（避免修改前端後仍載入舊版快取）
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# 縣市資料快取（key = "<net>:<中文名>" -> dict of numpy arrays）
_county_cache = {}
_cache_lock = threading.Lock()
# 邊界框索引快取（key = net -> dict）
_bbox = {}


# ----------------------------------------------------------------------------
# 工具函式
# ----------------------------------------------------------------------------
def merged_csv_path(en_name, net=DEFAULT_NET):
    return os.path.join(net_cfg(net)["data_dir"], en_name, f"{en_name}.csv")


def haversine_m(lat1, lon1, lat2, lon2):
    """以公尺為單位的 haversine 距離 (lat2/lon2 可為 numpy 陣列)。"""
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = np.radians(lat2)
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def rsrp_quality(rsrp):
    """依 RSRP (dBm) 分級訊號品質。"""
    if rsrp is None or (isinstance(rsrp, float) and math.isnan(rsrp)):
        return "無資料", "#9ca3af"
    if rsrp >= -85:
        return "優良", "#2a52e8"
    if rsrp >= -95:
        return "良好", "#1fa01f"
    if rsrp >= -105:
        return "普通", "#d4d43b"
    if rsrp >= -115:
        return "較弱", "#f6b26b"
    return "微弱", "#e60000"


def build_bbox_index(net=DEFAULT_NET, force=False):
    """建立 / 載入某網路別各縣市合併檔的經緯度邊界框 (bounding box) 索引。"""
    net = (net or DEFAULT_NET).upper()
    if net in _bbox and not force:
        return _bbox[net]
    cache_path = net_cfg(net)["bbox_cache"]
    if os.path.exists(cache_path) and not force:
        with open(cache_path, "r", encoding="utf-8") as f:
            _bbox[net] = json.load(f)
        return _bbox[net]

    print(f"[啟動] 首次建立 {net} 縣市邊界框索引，請稍候…")
    cols = net_cfg(net)["cols"]
    bbox = {}
    for zh, en in all_counties():
        path = merged_csv_path(en, net)
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path, usecols=[cols["lon"], cols["lat"]])
            bbox[zh] = {
                "en": en,
                "lon_min": float(df[cols["lon"]].min()),
                "lon_max": float(df[cols["lon"]].max()),
                "lat_min": float(df[cols["lat"]].min()),
                "lat_max": float(df[cols["lat"]].max()),
            }
            print(f"  - {zh} ({en}) bbox 完成")
        except Exception as e:  # noqa: BLE001
            print(f"  ! {zh} 讀取失敗：{e}")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(bbox, f, ensure_ascii=False, indent=2)
    _bbox[net] = bbox
    print(f"[啟動] {net} 邊界框索引建立完成。")
    return _bbox[net]


def load_county(zh, net=DEFAULT_NET):
    """載入並快取某網路別、某縣市的合併資料 (numpy 陣列)。"""
    net = (net or DEFAULT_NET).upper()
    cache_key = f"{net}:{zh}"
    with _cache_lock:
        if cache_key in _county_cache:
            return _county_cache[cache_key]
    en = ZH_TO_EN.get(zh)
    if not en:
        return None
    path = merged_csv_path(en, net)
    if not os.path.exists(path):
        return None
    cols = net_cfg(net)["cols"]
    usecols = [c for c in cols.values() if c]
    df = pd.read_csv(path, usecols=usecols)
    n = len(df)

    def col(key):
        name = cols.get(key)
        if name and name in df.columns:
            return df[name].to_numpy(dtype="float64")
        return np.full(n, np.nan, dtype="float64")

    data = {
        "lon": col("lon"), "lat": col("lat"),
        "rsrp": col("rsrp"), "rsrq": col("rsrq"), "sinr": col("sinr"),
        "dl": col("dl"), "mr": col("mr"), "cqi": col("cqi"),
    }
    with _cache_lock:
        _county_cache[cache_key] = data
    return data


def candidate_counties(lat, lon, net=DEFAULT_NET, margin_deg=0.01):
    """依邊界框找出可能包含查詢點 (含 500m 邊界) 的縣市。"""
    bbox = build_bbox_index(net)
    hits = []
    for zh, b in bbox.items():
        if (b["lon_min"] - margin_deg <= lon <= b["lon_max"] + margin_deg and
                b["lat_min"] - margin_deg <= lat <= b["lat_max"] + margin_deg):
            hits.append(zh)
    return hits


def _isnan(x):
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True


def _num(x, ndigits=1):
    """數值轉成可序列化的 JSON 值；NaN / 無效值回傳 None (即 JSON null)。"""
    if _isnan(x):
        return None
    return round(float(x), ndigits)


def _int(x):
    """整數轉換；NaN / 無效值回傳 None。"""
    if _isnan(x):
        return None
    return int(float(x))


def query_signals(lat, lon, radius_m=DEFAULT_RADIUS_M, net=DEFAULT_NET):
    """查詢以 (lat, lon) 為圓心、radius_m 為半徑範圍內的訊號格點。"""
    counties = candidate_counties(lat, lon, net)
    dl_div = net_cfg(net)["dl_divisor"]
    deg_margin = radius_m / 111000.0 + 0.0005  # 經緯度粗篩邊界
    points = []
    for zh in counties:
        data = load_county(zh, net)
        if data is None:
            continue
        lon_arr = data["lon"]
        lat_arr = data["lat"]
        # 先用矩形粗篩，再算精確距離
        mask = (
            (lon_arr >= lon - deg_margin) & (lon_arr <= lon + deg_margin) &
            (lat_arr >= lat - deg_margin) & (lat_arr <= lat + deg_margin)
        )
        idx = np.where(mask)[0]
        if idx.size == 0:
            continue
        dist = haversine_m(lat, lon, lat_arr[idx], lon_arr[idx])
        within = idx[dist <= radius_m]
        if within.size == 0:
            continue
        d_within = haversine_m(lat, lon, lat_arr[within], lon_arr[within])
        for i, di in zip(within, d_within):
            rsrp = float(data["rsrp"][i])
            label, color = rsrp_quality(rsrp)
            dl = data["dl"][i]
            points.append({
                "lon": round(float(data["lon"][i]), 6),
                "lat": round(float(data["lat"][i]), 6),
                "rsrp": _num(rsrp, 1),
                "rsrq": _num(data["rsrq"][i], 1),
                "sinr": _num(data["sinr"][i], 1),
                "dl_mbps": _num(dl / dl_div if not _isnan(dl) else dl, 1),
                "mr": _int(data["mr"][i]),
                "cqi": _num(data["cqi"][i], 1),
                "distance": round(float(di), 0),
                "quality": label,
                "color": color,
            })
    points.sort(key=lambda p: p["distance"])
    return points, counties


def summarize(points):
    """產生整體訊號品質摘要。"""
    if not points:
        return {
            "count": 0,
            "verdict": "查無資料",
            "verdict_color": "#9ca3af",
            "detail": "此位置方圓範圍內查無訊號量測資料，建議擴大查詢範圍或確認地址。",
        }
    def vals(key):
        return np.array([p[key] for p in points if p.get(key) is not None], dtype="float64")

    rsrps = vals("rsrp")
    dls = vals("dl_mbps")
    sinrs = vals("sinr")
    rsrqs = vals("rsrq")
    cqis = vals("cqi")
    total_mr = int(sum(p["mr"] for p in points if p["mr"] is not None))

    if rsrps.size == 0:
        return {
            "count": len(points),
            "verdict": "資料不足",
            "verdict_color": "#9ca3af",
            "detail": "範圍內格點缺少有效訊號強度 (RSRP) 量測值，無法判讀。",
            "total_mr": total_mr,
        }

    avg_rsrp = float(np.mean(rsrps))
    poor_ratio = float(np.mean(rsrps < -105) * 100)
    verdict, color = rsrp_quality(avg_rsrp)

    if poor_ratio >= 40:
        detail = f"區域內約 {poor_ratio:.0f}% 格點訊號偏弱，可能有覆蓋不足或室內收訊不佳情形。"
    elif poor_ratio >= 15:
        detail = f"整體訊號尚可，但約 {poor_ratio:.0f}% 格點較弱，部分位置或室內可能收訊較差。"
    else:
        detail = "整體訊號覆蓋良好，多數位置可正常使用。"

    # RSRP 五級品質分佈（百分比）
    def pct(mask):
        return round(float(np.mean(mask) * 100), 1)
    quality_dist = {
        "excellent": pct(rsrps >= -85),
        "good": pct((rsrps >= -95) & (rsrps < -85)),
        "fair": pct((rsrps >= -105) & (rsrps < -95)),
        "weak": pct((rsrps >= -115) & (rsrps < -105)),
        "very_weak": pct(rsrps < -115),
    }

    def r1(x):
        return round(float(x), 1)

    return {
        "count": len(points),
        "verdict": verdict,
        "verdict_color": color,
        "avg_rsrp": round(avg_rsrp, 1),
        "avg_sinr": r1(np.mean(sinrs)) if sinrs.size else None,
        "avg_dl_mbps": r1(np.mean(dls)) if dls.size else None,
        "poor_ratio": round(poor_ratio, 1),
        "total_mr": total_mr,
        "detail": detail,
        # --- 深度分析用的擴充指標 ---
        "avg_rsrq": r1(np.mean(rsrqs)) if rsrqs.size else None,
        "avg_cqi": r1(np.mean(cqis)) if cqis.size else None,
        "rsrp_min": r1(np.min(rsrps)),
        "rsrp_max": r1(np.max(rsrps)),
        "rsrp_std": r1(np.std(rsrps)),
        "sinr_min": r1(np.min(sinrs)) if sinrs.size else None,
        "sinr_neg_ratio": pct(sinrs < 0) if sinrs.size else None,
        "dl_min": r1(np.min(dls)) if dls.size else None,
        "dl_p10": r1(np.percentile(dls, 10)) if dls.size else None,
        "dl_median": r1(np.median(dls)) if dls.size else None,
        "quality_dist": quality_dist,
        "nearest_m": int(min(p["distance"] for p in points)),
    }


def _http_get(url, params, timeout=15):
    """HTTP GET，遇到企業 SSL 攔截 (自簽憑證) 時停用驗證重試一次。"""
    try:
        return requests.get(url, params=params, timeout=timeout)
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, params=params, timeout=timeout, verify=False)


def _iter_dicts(obj):
    """遞迴走訪 JSON，回傳其中所有 dict。"""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _iter_dicts(it)


def _tgos_refresh_session():
    """載入 TGOS 公開圖臺一次，取得 session cookie 與 CSRF / Request 權杖。"""
    global _tgos_session, _tgos_tokens
    sess = requests.Session()
    sess.headers.update({"User-Agent": BROWSER_UA, "Referer": TGOS_VIEWER_URL})
    sess.verify = True
    try:
        resp = sess.get(TGOS_VIEWER_URL, params={"addr": "init"}, timeout=20)
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        sess.verify = False
        resp = sess.get(TGOS_VIEWER_URL, params={"addr": "init"}, timeout=20)
    html = resp.text
    m_csrf = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
    m_req = re.search(r'<gs-request-token[^>]*value="([^"]+)"', html)
    if not (m_csrf and m_req):
        return False
    _tgos_session = sess
    _tgos_tokens = (m_csrf.group(1), m_req.group(1))
    return True


def _tgos_parse(payload, address):
    """從 reqcontroller.go 回傳的 JSON 取出第一筆門牌座標。"""
    try:
        addr_list = payload["Content"]["data"]["AddressList"]
    except (KeyError, TypeError):
        return None
    if not addr_list:
        return None
    rec = addr_list[0]
    try:
        lon = float(rec.get("X"))
        lat = float(rec.get("Y"))
    except (TypeError, ValueError):
        return None
    if not (118.0 <= lon <= 122.5 and 21.5 <= lat <= 26.5):
        return None
    return lat, lon, (rec.get("FULL_ADDR") or address)


def geocode_tgos_web(address):
    """透過 TGOS 公開圖臺 (map.tgos.tw) 查詢門牌座標，免申請 AppID/APIKey。

    回傳 (lat, lon, display) 或 None。權杖失效時自動重新取得並重試一次。
    """
    with _tgos_lock:
        if _tgos_session is None or _tgos_tokens is None:
            if not _tgos_refresh_session():
                return None

    for attempt in range(2):
        with _tgos_lock:
            sess, tokens = _tgos_session, _tgos_tokens
        if sess is None or tokens is None:
            return None
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": TGOS_VIEWER_URL + "?addr=q",
            "GS-CSRF-TOKEN": tokens[0],
            "GS-REQUEST-TOKEN": tokens[1],
        }
        data = {"method": "index.QueryAddr", "oAddress": address}
        try:
            resp = sess.post(TGOS_CTRL_URL, data=data, headers=headers,
                             timeout=20, verify=sess.verify)
        except requests.exceptions.SSLError:
            sess.verify = False
            resp = sess.post(TGOS_CTRL_URL, data=data, headers=headers,
                             timeout=20, verify=False)
        except requests.exceptions.RequestException:
            return None

        # 權杖 / session 失效 -> 重新取得後重試
        if resp.status_code in (401, 403) or resp.text.lstrip().startswith("認證"):
            with _tgos_lock:
                if not _tgos_refresh_session():
                    return None
            continue
        if resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        return _tgos_parse(payload, address)
    return None


def geocode_tgos(address):
    """透過 TGOS 全國門牌地址定位服務轉經緯度 (EPSG:4326)。

    回傳 (lat, lon, display) 或 None。未設定 AppID/APIKey 時直接回傳 None。
    """
    if not (TGOS_APP_ID and TGOS_API_KEY):
        return None
    params = {
        "oAPPId": TGOS_APP_ID,
        "oAPIKey": TGOS_API_KEY,
        "oAddress": address,
        "oSRS": "EPSG:4326",
        "oFuzzyType": "2",
        "oResultDataType": "JSON",
        "oFuzzyBuffer": "0",
        "oIsOnlyFullMatch": "false",
        "oIsLockCounty": "false",
        "oIsLockTown": "false",
        "oIsLockVillage": "false",
        "oIsLockRoadSection": "false",
        "oIsLockLane": "false",
        "oIsLockAlley": "false",
        "oIsLockArea": "false",
        "oIsSameNumber_SubNumber": "true",
        "oCanIgnoreVillage": "true",
        "oCanIgnoreNeighborhood": "true",
        "oReturnMaxCount": "1",
    }
    resp = _http_get(TGOS_URL, params)
    resp.raise_for_status()
    text = resp.text.strip()
    # ASMX 可能用 <string>...</string> 把 JSON 包在 XML 外層
    if text.startswith("<"):
        m = re.search(r"<string[^>]*>(.*)</string>", text, re.S)
        if m:
            text = m.group(1).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    # 在回傳結構中尋找含 X/Y 座標的門牌記錄
    for rec in _iter_dicts(data):
        x = y = full = None
        for k, v in rec.items():
            kk = str(k).lstrip("@").upper()
            if kk == "X":
                x = v
            elif kk == "Y":
                y = v
            elif kk in ("FULL_ADDR", "FULLADDR", "ADDRESS"):
                full = v
        if x in (None, "") or y in (None, ""):
            continue
        try:
            lon = float(x)
            lat = float(y)
        except (ValueError, TypeError):
            continue
        # 台灣範圍合理性檢查 (含外島)
        if 118.0 <= lon <= 122.5 and 21.5 <= lat <= 26.5:
            return lat, lon, (full or address)
    return None


def geocode(query):
    """透過 Nominatim 將地址 / 地標轉成經緯度。回傳 (lat, lon, display_name) 或 None。"""
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "tw",
        "accept-language": "zh-TW",
        "addressdetails": 1,
    }
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    r = results[0]
    return float(r["lat"]), float(r["lon"]), r.get("display_name", query)


def geocode_with_fallback(candidates):
    """依序嘗試多組查詢字串，回傳第一個成功的 (lat, lon, display, used_query)。

    用於地址查詢：完整地址 (含門牌) 若定位失敗，會逐步退回較粗略的範圍。
    """
    last_exc = None
    # 去除重複並保留順序
    seen = set()
    uniq = []
    for q in candidates:
        q = (q or "").strip()
        if q and q not in seen:
            seen.add(q)
            uniq.append(q)
    for i, q in enumerate(uniq):
        if i > 0:
            time.sleep(1.1)  # 遵守 Nominatim 每秒至多 1 次的使用規範，避免被限流
        try:
            geo = geocode(q)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            print(f"[geocode] 候選 {i} '{q}' 發生例外：{e}", flush=True)
            continue
        print(f"[geocode] 候選 {i} '{q}' -> {'命中' if geo else '空'}", flush=True)
        if geo is not None:
            return geo[0], geo[1], geo[2], q
    if last_exc is not None:
        raise last_exc
    return None


# ----------------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", google_key=GOOGLE_MAPS_KEY)


@app.route("/api/query_coord", methods=["POST"])
def api_query_coord():
    """直接以經緯度查詢訊號 (前端 Google 定位成功後呼叫)。"""
    body = request.get_json(force=True) or {}
    try:
        lat = float(body["lat"])
        lon = float(body["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "error": "缺少有效的經緯度。"}), 400
    radius = int(body.get("radius", DEFAULT_RADIUS_M))
    net = (body.get("net") or DEFAULT_NET).upper()
    points, counties = query_signals(lat, lon, radius, net)
    summary = summarize(points)
    return jsonify({
        "ok": True,
        "query": body.get("query", ""),
        "matched_query": body.get("query", ""),
        "approximate": bool(body.get("approximate", False)),
        "display_name": body.get("display_name", ""),
        "source": body.get("source", "Google"),
        "net": net,
        "center": {"lat": lat, "lon": lon},
        "radius": radius,
        "counties": counties,
        "summary": summary,
        "points": points,
    })


@app.route("/api/ai_diagnose", methods=["POST"])
def api_ai_diagnose():
    """AI 訊號判讀：以查詢結果的 summary 產生專業判讀與建議。

    前端在拿到 /api/search 或 /api/query_coord 的結果後，把 summary 與情境
    傳進來即可；未設定 LLM 金鑰時會自動退回規則式判讀。
    """
    body = request.get_json(force=True) or {}
    summary = body.get("summary") or {}
    if not isinstance(summary, dict):
        return jsonify({"ok": False, "error": "缺少 summary。"}), 400
    context = {
        "net": (body.get("net") or DEFAULT_NET),
        "radius": body.get("radius", DEFAULT_RADIUS_M),
        "display_name": body.get("display_name", ""),
        "source": body.get("source", ""),
    }
    lang = body.get("lang", "zh")
    result = ai_diagnose(summary, context, lang)
    return jsonify(result)


@app.route("/api/ai_status")
def api_ai_status():
    """回報目前 AI 判讀模式（ai = 已接 LLM；rule = 規則式 fallback）。"""
    return jsonify({"mode": "ai" if ai_available() else "rule"})


@app.route("/api/counties")
def api_counties():
    return jsonify([{"zh": zh, "en": en} for zh, en in all_counties()])


@app.route("/api/districts")
def api_districts():
    city = request.args.get("city", "")
    return jsonify(districts_of_i18n(city))


@app.route("/api/search", methods=["POST"])
def api_search():
    body = request.get_json(force=True) or {}
    mode = body.get("mode", "address")
    radius = int(body.get("radius", DEFAULT_RADIUS_M))
    net = (body.get("net") or DEFAULT_NET).upper()

    if mode == "address":
        city = (body.get("city") or "").strip()
        district = (body.get("district") or "").strip()
        road = (body.get("road") or "").strip()
        number = (body.get("number") or "").strip()
        query = f"{city}{district}{road}{number}".strip()
        if not query:
            return jsonify({"ok": False, "error": "請至少輸入縣市與路名。"}), 400
        # 含門牌的完整地址若定位失敗，逐步退回較粗略範圍以提高成功率
        candidates = [
            f"{city}{district}{road}{number}",
            f"{city}{district}{road}",
            f"{city}{road}{number}",
            f"{city}{district}",
        ]
    else:
        query = (body.get("keyword") or "").strip()
        if not query:
            return jsonify({"ok": False, "error": "請輸入地標關鍵字。"}), 400
        candidates = [query]

    # 轉經緯度：地址模式優先使用 TGOS (政府門牌)，失敗再退回 OpenStreetMap
    source = "OpenStreetMap"
    geo = None
    if mode == "address":
        tg = None
        # 1) TGOS 公開圖臺 (免申請)
        try:
            tg = geocode_tgos_web(query)
        except Exception as e:  # noqa: BLE001
            print(f"[TGOS-Web] 定位發生例外：{e}", flush=True)
            tg = None
        # 2) 若已設定官方 AppID/APIKey，改走官方 Web Service
        if not tg:
            try:
                tg = geocode_tgos(query)
            except Exception as e:  # noqa: BLE001
                print(f"[TGOS-API] 定位發生例外：{e}", flush=True)
                tg = None
        if tg:
            geo = (tg[0], tg[1], tg[2], query)
            source = "TGOS"

    if geo is None:
        try:
            geo = geocode_with_fallback(candidates)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"地址定位服務發生錯誤：{e}"}), 502
        if geo is None:
            return jsonify({"ok": False, "error": f"找不到「{query}」的位置，請確認輸入是否正確。"}), 404

    lat, lon, display, used_query = geo
    points, counties = query_signals(lat, lon, radius, net)
    summary = summarize(points)

    return jsonify({
        "ok": True,
        "query": query,
        "matched_query": used_query,
        "approximate": (used_query != query),
        "display_name": display,
        "source": source,
        "net": net,
        "center": {"lat": lat, "lon": lon},
        "radius": radius,
        "counties": counties,
        "summary": summary,
        "points": points,
    })


def ensure_data():
    """部署用：若資料不存在且設定了 DATA_URL，則自雲端下載 ZIP 並解壓到 DATA_BASE。

    ZIP 內部結構需為 `TWM_MDT_City_25m/{4G,5G}/All/<City>/<City>.csv`，
    DATA_BASE 會指向解壓後的 `TWM_MDT_City_25m` 目錄 (由環境變數 SIGNAL_DATA_BASE 設定)。
    """
    data_url = os.environ.get(
        "DATA_URL",
        "https://github.com/pin740922/signal-quality-lookup/releases/download/"
        "v1.0-demo/demo_data_3cities.zip",
    ).strip()
    sample = os.path.join(DATA_BASE, "4G", "All")
    if os.path.isdir(sample) and os.listdir(sample):
        return  # 資料已存在
    if not data_url:
        return  # 無下載來源，維持本機既有資料
    extract_to = os.path.dirname(os.path.abspath(DATA_BASE)) or "."
    os.makedirs(extract_to, exist_ok=True)
    zip_path = os.path.join(extract_to, "_data_download.zip")
    print(f"[啟動] 下載資料中：{data_url}")
    if "drive.google" in data_url or "googleusercontent" in data_url:
        # Google Drive 大檔需處理「無法掃毒」確認頁，交給 gdown 處理。
        # 自連結解析檔案 ID，改用最相容的 uc?id= 形式 (不依賴 fuzzy 參數)。
        import gdown
        m = (re.search(r"/d/([A-Za-z0-9_-]+)", data_url)
             or re.search(r"[?&]id=([A-Za-z0-9_-]+)", data_url))
        if not m:
            raise ValueError(f"無法從連結解析 Google Drive 檔案 ID：{data_url}")
        gdown.download(f"https://drive.google.com/uc?id={m.group(1)}",
                       zip_path, quiet=False)
    else:
        resp = requests.get(data_url, timeout=600)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            f.write(resp.content)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_to)
    os.remove(zip_path)
    print(f"[啟動] 資料已解壓至：{extract_to}")


def bootstrap():
    """資料就緒 + 建立各網路別邊界框索引（gunicorn / 直接執行皆會呼叫）。"""
    try:
        ensure_data()
    except Exception as e:
        print(f"[啟動] 資料下載失敗（將以現有資料運作）：{e}")
    for _net in NETWORKS:
        build_bbox_index(_net)


bootstrap()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)

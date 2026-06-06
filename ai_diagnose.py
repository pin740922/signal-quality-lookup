# -*- coding: utf-8 -*-
"""AI 訊號判讀模組 (PoC)。

把原本 `summarize()` 產生的「規則式」一句話判讀 (s.detail)，升級成由
大型語言模型 (LLM) 產生的專業化診斷 + 可執行建議。

設計重點
--------
1. 與供應商無關 (provider-agnostic)：使用 OpenAI 相容的 Chat Completions API，
   只靠 `requests` 即可運作 (專案已有 requests，不需新增相依套件)。
   可接 OpenAI / Azure OpenAI / Gemini(OpenAI 相容端點) / 本地 Ollama 等。
2. 零金鑰也能跑：未設定 API 金鑰、或呼叫失敗時，自動退回「強化版規則式判讀」，
   確保前端永遠拿得到結果，方便先看 PoC 效果。

環境變數
--------
    AI_API_KEY     LLM 服務金鑰 (留空 = 強制使用規則式 fallback)
    AI_BASE_URL    API base url，預設 https://api.openai.com/v1
    AI_MODEL       模型名稱，預設 gpt-4o-mini
    AI_TIMEOUT     呼叫逾時秒數，預設 20
"""

import json
import os

import requests

try:  # 自 .env 載入金鑰等設定（不寫入程式碼、不進版控）
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # noqa: BLE001 - 無 python-dotenv 時改用系統環境變數
    pass

AI_API_KEY = os.environ.get("AI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "20"))


def ai_available():
    """是否已設定可用的 LLM 金鑰。"""
    return bool(AI_API_KEY)


# ----------------------------------------------------------------------------
# 規則式 fallback（無金鑰 / LLM 失敗時使用）
# ----------------------------------------------------------------------------
def _rule_based(summary, context, lang="zh"):
    """以多欄位結構化指標產生較完整的判讀與根因分析（不需 LLM）。"""
    net = context.get("net", "4G")
    count = summary.get("count", 0) or 0
    avg_rsrp = summary.get("avg_rsrp")
    avg_sinr = summary.get("avg_sinr")
    avg_dl = summary.get("avg_dl_mbps")
    avg_rsrq = summary.get("avg_rsrq")
    avg_cqi = summary.get("avg_cqi")
    rsrp_std = summary.get("rsrp_std")
    sinr_neg = summary.get("sinr_neg_ratio")
    dl_p10 = summary.get("dl_p10")
    qd = summary.get("quality_dist") or {}
    poor = summary.get("poor_ratio")
    verdict = summary.get("verdict", "")

    if not count:
        headline = "查無量測資料" if lang == "zh" else "No measurement data"
        detail = ("此位置方圓範圍內查無訊號量測資料，建議擴大查詢半徑或確認地址是否正確。"
                  if lang == "zh" else
                  "No signal measurements within this radius. Try a larger radius or verify the address.")
        return {"headline": headline, "detail": detail, "advice": []}

    if avg_rsrp is None:
        headline = "資料不足" if lang == "zh" else "Insufficient data"
        detail = ("範圍內格點缺少有效訊號強度 (RSRP) 量測值，無法判讀。"
                  if lang == "zh" else
                  "Grid points lack valid RSRP values; unable to assess.")
        return {"headline": headline, "detail": detail, "advice": []}

    p = float(poor or 0)
    weak_share = (qd.get("weak", 0) or 0) + (qd.get("very_weak", 0) or 0)

    # ---- 多欄位根因推論：覆蓋 / 干擾 / 容量 / 不均 ----
    causes = []  # (zh, en)
    if avg_rsrp < -105 or weak_share >= 40:
        causes.append(("覆蓋不足（RSRP 偏低、弱訊格點偏多）", "coverage gap (low RSRP, many weak cells)"))
    inter = ((avg_sinr is not None and avg_sinr < 3 and avg_rsrp >= -100)
             or (sinr_neg is not None and sinr_neg >= 20)
             or (avg_rsrq is not None and avg_rsrq <= -15))
    if inter:
        causes.append(("可能受干擾（RSRP 尚可但 SINR/RSRQ 偏低）",
                       "possible interference (ok RSRP but low SINR/RSRQ)"))
    cong = ((avg_dl is not None and avg_dl < 20 and avg_rsrp >= -100
             and (avg_sinr is None or avg_sinr >= 3))
            or (dl_p10 is not None and dl_p10 < 5 and avg_rsrp >= -100))
    if cong:
        causes.append(("可能容量受限（訊號可但下行速率偏低）",
                       "possible congestion (good signal but low downlink)"))
    if rsrp_std is not None and rsrp_std >= 8:
        causes.append(("覆蓋不均、點狀弱訊（RSRP 離散度大）",
                       "uneven / spotty coverage (high RSRP variability)"))
    if avg_cqi is not None and avg_cqi < 7:
        causes.append(("通道品質不佳（CQI 偏低）", "poor channel quality (low CQI)"))

    i = 0 if lang == "zh" else 1
    cause_txt = "、".join(c[i] for c in causes) if lang == "zh" else ", ".join(c[i] for c in causes)

    if lang == "zh":
        headline = f"{net} 訊號整體{verdict}"
        # 主判讀
        if p >= 40:
            detail = f"區域內約 {p:.0f}% 格點訊號偏弱（平均 RSRP {avg_rsrp} dBm），可能有覆蓋不足或室內收訊不佳情形。"
        elif p >= 15:
            detail = f"整體訊號尚可（平均 RSRP {avg_rsrp} dBm），但約 {p:.0f}% 格點較弱，部分位置或室內可能收訊較差。"
        else:
            detail = f"整體訊號覆蓋良好（平均 RSRP {avg_rsrp} dBm），多數位置可正常使用。"
        # 補充品質分佈
        if qd:
            detail += (f" 格點分佈：優良/良好 {qd.get('excellent',0)+qd.get('good',0):.0f}%、"
                       f"普通 {qd.get('fair',0):.0f}%、較弱以下 {weak_share:.0f}%。")
        advice = []
        if cause_txt:
            advice.append("可能成因：" + cause_txt + "。")
        # 各指標的具體建議
        if avg_sinr is not None:
            q = "佳" if avg_sinr >= 13 else ("尚可" if avg_sinr >= 0 else "偏差")
            advice.append(f"訊號品質 SINR 平均 {avg_sinr} dB（{q}）"
                          + (f"，其中約 {sinr_neg:.0f}% 格點為負值，建議查核鄰區干擾/重疊覆蓋。" if (sinr_neg or 0) >= 10 else "。"))
        if avg_dl is not None:
            extra = f"，最差 10% 約 {dl_p10} Mbps" if dl_p10 is not None else ""
            advice.append(f"下行速率平均 {avg_dl} Mbps{extra}，可作為用戶體驗參考。")
        if rsrp_std is not None and rsrp_std >= 8:
            advice.append(f"訊號強弱落差大（RSRP 標準差 {rsrp_std} dB），可能為點狀死角，建議現場逐點勘查。")
        if p >= 15:
            advice.append("室內或大樓深處建議實測收訊，必要時評估室內訊號增強方案。")
    else:
        headline = f"{net} signal overall {verdict}"
        if p >= 40:
            detail = (f"About {p:.0f}% of grid points are weak (avg RSRP {avg_rsrp} dBm); "
                      "possible coverage gaps or poor indoor reception.")
        elif p >= 15:
            detail = (f"Overall acceptable (avg RSRP {avg_rsrp} dBm) but about {p:.0f}% of points "
                      "are weak; some spots or indoors may have poorer reception.")
        else:
            detail = f"Coverage is good (avg RSRP {avg_rsrp} dBm); most locations work normally."
        if qd:
            detail += (f" Distribution: excellent/good {qd.get('excellent',0)+qd.get('good',0):.0f}%, "
                       f"fair {qd.get('fair',0):.0f}%, weak-or-below {weak_share:.0f}%.")
        advice = []
        if cause_txt:
            advice.append("Likely cause(s): " + cause_txt + ".")
        if avg_sinr is not None:
            q = "good" if avg_sinr >= 13 else ("ok" if avg_sinr >= 0 else "poor")
            advice.append(f"Avg SINR {avg_sinr} dB ({q})"
                          + (f"; about {sinr_neg:.0f}% cells are negative — check neighbor interference/overlap." if (sinr_neg or 0) >= 10 else "."))
        if avg_dl is not None:
            extra = f", worst-10% ~ {dl_p10} Mbps" if dl_p10 is not None else ""
            advice.append(f"Avg downlink {avg_dl} Mbps{extra} as a user-experience reference.")
        if rsrp_std is not None and rsrp_std >= 8:
            advice.append(f"Large signal spread (RSRP std {rsrp_std} dB) suggests spotty dead zones; on-site survey advised.")
        if p >= 15:
            advice.append("Verify real reception indoors / deep inside buildings; consider an indoor booster if needed.")

    return {"headline": headline, "detail": detail, "advice": advice}


# ----------------------------------------------------------------------------
# LLM 判讀
# ----------------------------------------------------------------------------
def _build_prompt(summary, context, lang):
    qd = summary.get("quality_dist") or {}
    metrics = {
        "網路別 net": context.get("net"),
        "查詢半徑公尺 radius_m": context.get("radius"),
        "地點 location": context.get("display_name"),
        "平均RSRP_dBm avg_rsrp": summary.get("avg_rsrp"),
        "RSRP最小/最大_dBm rsrp_min/max": f"{summary.get('rsrp_min')} / {summary.get('rsrp_max')}",
        "RSRP標準差_dB rsrp_std(離散度)": summary.get("rsrp_std"),
        "平均SINR_dB avg_sinr": summary.get("avg_sinr"),
        "SINR最小_dB sinr_min": summary.get("sinr_min"),
        "SINR負值格點比例% sinr_neg_ratio(干擾指標)": summary.get("sinr_neg_ratio"),
        "平均RSRQ_dB avg_rsrq(負載/干擾指標)": summary.get("avg_rsrq"),
        "平均CQI avg_cqi(通道品質)": summary.get("avg_cqi"),
        "平均下行Mbps avg_dl_mbps": summary.get("avg_dl_mbps"),
        "下行中位數/最差10%_Mbps dl_median/p10": f"{summary.get('dl_median')} / {summary.get('dl_p10')}",
        "弱訊格點比例% poor_ratio": summary.get("poor_ratio"),
        "RSRP五級分佈% quality_dist": (
            f"優良 {qd.get('excellent')}、良好 {qd.get('good')}、普通 {qd.get('fair')}、"
            f"較弱 {qd.get('weak')}、微弱 {qd.get('very_weak')}"),
        "範圍內格點數 count": summary.get("count"),
        "最近格點距離m nearest_m": summary.get("nearest_m"),
        "量測樣本數 total_mr": summary.get("total_mr"),
        "規則判定 verdict": summary.get("verdict"),
    }
    metrics_text = "\n".join(f"- {k}: {v}" for k, v in metrics.items())

    if lang == "en":
        sys = ("You are a senior RAN (radio access network) optimization engineer. "
               "Given aggregated MDT signal metrics around a location, write a concise, "
               "professional yet plain-language assessment for a telecom store / customer-support staff. "
               "Reference: RSRP >=-85 excellent, -95~-85 good, -105~-95 fair, -115~-105 weak, <-115 very weak; "
               "SINR>13 great, 0~13 ok, <0 poor; RSRQ <=-15 indicates load/interference; low CQI = poor channel. "
               "Diagnose root cause using ALL fields: low RSRP/weak distribution=coverage gap; "
               "ok RSRP but low SINR/RSRQ or high sinr_neg_ratio=interference/overlap; "
               "good RSRP+SINR but low downlink (esp. p10)=congestion/capacity; high rsrp_std=spotty dead zones. "
               "Return STRICT JSON only with keys: headline (<=16 words), detail (2-4 sentences citing the key numbers), "
               "advice (array of 2-4 short actionable bullet strings). No markdown, no extra text.")
        user = f"Location signal metrics:\n{metrics_text}\nReturn JSON now."
    else:
        sys = ("你是一位資深的無線網路 (RAN) 優化工程師。根據某地點周邊聚合的 MDT 訊號指標，"
               "為電信門市／客服人員撰寫精簡、專業但口語化的判讀。"
               "分級參考：RSRP >=-85 優良、-95~-85 良好、-105~-95 普通、-115~-105 較弱、<-115 微弱；"
               "SINR>13 很好、0~13 尚可、<0 偏差；RSRQ <=-15 代表負載/干擾偏高；CQI 偏低代表通道品質差。"
               "請綜合『所有欄位』做根因判讀：RSRP 低或弱訊分佈高=覆蓋不足；"
               "RSRP 尚可但 SINR/RSRQ 偏低或 sinr_neg_ratio 高=干擾/重疊覆蓋；"
               "RSRP 與 SINR 都好但下行(尤其最差10% p10)偏低=容量壅塞；rsrp_std 大=點狀死角。"
               "只回傳嚴格 JSON，鍵為：headline（不超過 20 字的標題）、detail（2-4 句說明並引用關鍵數值）、"
               "advice（2-4 條精簡可執行建議字串的陣列）。不要 markdown、不要多餘文字。")
        user = f"地點訊號指標：\n{metrics_text}\n現在請回傳 JSON。"
    return sys, user


def _parse_json_loose(content):
    """容錯解析模型輸出的 JSON（可能被 ```json 包覆或夾雜文字）。"""
    content = (content or "").strip()
    try:
        return json.loads(content)
    except Exception:  # noqa: BLE001
        pass
    # 去除 markdown code fence
    if content.startswith("```"):
        content = content.strip("`")
        if content[:4].lower() == "json":
            content = content[4:]
    # 擷取第一個 { ... } 區塊
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except Exception:  # noqa: BLE001
            pass
    return None


def _post_chat(payload):
    url = f"{AI_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    return requests.post(url, headers=headers, json=payload, timeout=AI_TIMEOUT)


def _call_llm(summary, context, lang):
    sys, user = _build_prompt(summary, context, lang)
    base_payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    # 先嘗試要求 JSON 物件輸出；部分相容端點不支援時，去掉該參數重試
    resp = _post_chat(dict(base_payload, response_format={"type": "json_object"}))
    if resp.status_code >= 400:
        resp = _post_chat(base_payload)
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]
    data = _parse_json_loose(content)
    if not data:
        # 模型未回傳可解析 JSON：整段文字當作 detail
        return {"headline": "", "detail": content.strip(), "advice": []}

    advice = data.get("advice") or []
    if isinstance(advice, str):
        advice = [advice]
    return {
        "headline": str(data.get("headline", "")).strip(),
        "detail": str(data.get("detail", "")).strip(),
        "advice": [str(a).strip() for a in advice if str(a).strip()],
    }


def diagnose(summary, context=None, lang="zh"):
    """產生 AI 訊號判讀。

    參數
    ----
    summary : dict   `summarize()` 的輸出（含 avg_rsrp / poor_ratio / verdict ...）。
    context : dict   查詢情境（net / radius / display_name ...）。
    lang    : str    'zh' 或 'en'。

    回傳
    ----
    dict: { ok, mode('ai'|'rule'), model, headline, detail, advice[list] }
    """
    context = context or {}
    lang = "en" if str(lang).lower().startswith("en") else "zh"

    if ai_available():
        try:
            result = _call_llm(summary, context, lang)
            if result.get("detail"):
                result.update({"ok": True, "mode": "ai", "model": AI_MODEL})
                return result
        except Exception as e:  # noqa: BLE001 - LLM 失敗一律退回規則式
            print(f"[ai_diagnose] LLM 呼叫失敗，改用規則式：{e}", flush=True)

    result = _rule_based(summary, context, lang)
    result.update({"ok": True, "mode": "rule", "model": None})
    return result

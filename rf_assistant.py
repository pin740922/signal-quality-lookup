# -*- coding: utf-8 -*-
"""資深 RF 工程師問答助理（RAG + LLM）。

設計
----
1. RAG（檢索增強生成）：把 `knowledge/` 內的文件（.md/.txt，若安裝 pypdf 亦支援 .pdf）
   切塊後建立輕量 BM25 索引（純 Python、不需外部服務 / 向量資料庫），
   回答前先檢索最相關段落，連同問題一起餵給 LLM，降低幻覺、貼合內部資料。
2. 強化版資深 RF 工程師 persona：內建 4G/5G KPI 判讀準則與根因分析框架。
3. 與供應商無關：沿用專案既有的 OpenAI 相容 Chat API 設定（AI_API_KEY / AI_BASE_URL / AI_MODEL）。
   無金鑰或呼叫失敗時，退回「擷取式」回答（直接回傳最相關的知識庫段落），確保不會壞掉。

環境變數（沿用 ai_diagnose）
----------------------------
    AI_API_KEY / AI_BASE_URL / AI_MODEL / AI_TIMEOUT
"""

import math
import os
import re

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # noqa: BLE001
    pass

AI_API_KEY = os.environ.get("AI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "30"))

_BASE = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.join(_BASE, "knowledge")

# 檢索索引（記憶體內）
_chunks = []        # [{"text":..., "source":...}]
_df = {}            # token -> document frequency
_chunk_tokens = []  # 每塊的 token 計數 dict
_avg_len = 0.0
_loaded = False


# ----------------------------------------------------------------------------
# 知識庫載入與切塊
# ----------------------------------------------------------------------------
def _read_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".txt"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            return "\n".join((pg.extract_text() or "") for pg in reader.pages)
        except Exception as e:  # noqa: BLE001
            print(f"[rf_assistant] 無法讀取 PDF {path}：{e}", flush=True)
            return ""
    return ""


def _chunk_text(text, max_chars=700):
    """以段落為單位累積成約 max_chars 的塊。"""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) + 2 <= max_chars:
            cur = (cur + "\n\n" + b) if cur else b
        else:
            if cur:
                chunks.append(cur)
            # 單一段落過長時再切
            if len(b) > max_chars:
                for i in range(0, len(b), max_chars):
                    chunks.append(b[i:i + max_chars])
                cur = ""
            else:
                cur = b
    if cur:
        chunks.append(cur)
    return chunks


def _tokenize(text):
    """中英混合斷詞：英數取詞、中日韓取字元 unigram + bigram。"""
    text = (text or "").lower()
    toks = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        toks.extend(list(run))
        toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def load_knowledge(force=False):
    """掃描 knowledge/ 建立 BM25 索引（記憶體內）。"""
    global _chunks, _df, _chunk_tokens, _avg_len, _loaded
    if _loaded and not force:
        return len(_chunks)
    _chunks, _df, _chunk_tokens = [], {}, []
    if os.path.isdir(KB_DIR):
        for name in sorted(os.listdir(KB_DIR)):
            path = os.path.join(KB_DIR, name)
            if not os.path.isfile(path):
                continue
            raw = _read_file(path)
            if not raw.strip():
                continue
            for ch in _chunk_text(raw):
                _chunks.append({"text": ch, "source": name})
    # 建立 token 統計
    total_len = 0
    for ch in _chunks:
        toks = _tokenize(ch["text"])
        tf = {}
        for tk in toks:
            tf[tk] = tf.get(tk, 0) + 1
        _chunk_tokens.append(tf)
        total_len += len(toks)
        for tk in tf:
            _df[tk] = _df.get(tk, 0) + 1
    _avg_len = (total_len / len(_chunks)) if _chunks else 0.0
    _loaded = True
    print(f"[rf_assistant] 知識庫載入完成：{len(_chunks)} 塊（來源 {KB_DIR}）", flush=True)
    return len(_chunks)


# ----------------------------------------------------------------------------
# BM25 檢索
# ----------------------------------------------------------------------------
def retrieve(query, k=4):
    """回傳最相關的 k 個知識塊 [{"text","source","score"}]。"""
    load_knowledge()
    if not _chunks:
        return []
    n = len(_chunks)
    q_toks = set(_tokenize(query))
    if not q_toks:
        return []
    k1, b = 1.5, 0.75
    scored = []
    for i, tf in enumerate(_chunk_tokens):
        dl = sum(tf.values()) or 1
        score = 0.0
        for tk in q_toks:
            if tk not in tf:
                continue
            df = _df.get(tk, 0) or 1
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            f = tf[tk]
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / (_avg_len or 1)))
        if score > 0:
            scored.append((score, i))
    scored.sort(reverse=True)
    out = []
    for score, i in scored[:k]:
        out.append({"text": _chunks[i]["text"], "source": _chunks[i]["source"], "score": round(score, 3)})
    return out


# ----------------------------------------------------------------------------
# 提示與 LLM 呼叫
# ----------------------------------------------------------------------------
def _system_prompt(lang):
    if lang == "en":
        return (
            "You are a SENIOR RF / RAN (radio access network) optimization engineer with 15+ years of "
            "experience in 4G LTE and 5G NR planning, drive-test analysis, and troubleshooting. "
            "Answer like an expert mentor: precise, practical, and structured. "
            "Use the provided KNOWLEDGE CONTEXT as the authoritative source for thresholds, definitions and "
            "company-specific rules; if the context is insufficient, rely on solid RF fundamentals but DO NOT "
            "invent specific numbers, standards clauses, or company policies. If unsure, say so. "
            "When relevant, explain root cause across coverage / interference / capacity / mobility, and give "
            "actionable next steps. Keep answers concise and well-organized (use short bullets when helpful). "
            "IMPORTANT: Write your ENTIRE response in English, even if the question or the knowledge context "
            "is written in Chinese. Translate any Chinese terms/values from the context into English."
        )
    return (
        "你是一位資深的 RF / RAN（無線接取網路）優化工程師，擁有 15 年以上 4G LTE 與 5G NR 的網路規劃、"
        "路測分析與疑難排解經驗。請以專家導師的口吻回答：精準、實用、有條理。"
        "請把下方提供的『知識庫內容』當作門檻、定義與公司內部規則的權威依據；若內容不足，可運用扎實的 RF 基本功，"
        "但『不要捏造』具體數值、規範條文或公司政策。不確定就明說。"
        "適當時請從『覆蓋 / 干擾 / 容量 / 移動性』面向做根因分析，並給出可執行的後續步驟。"
        "回答力求精簡、有結構（必要時用簡短條列）。"
        "重要：無論問題或知識庫內容使用何種語言，請一律以『繁體中文』作答。"
    )


def _build_messages(history, user_msg, contexts, lang):
    sys = _system_prompt(lang)
    msgs = [{"role": "system", "content": sys}]
    if contexts:
        joined = "\n\n---\n".join(f"[來源 {c['source']}]\n{c['text']}" for c in contexts)
        label = ("KNOWLEDGE CONTEXT (authoritative; cite thresholds from here):"
                 if lang == "en" else "知識庫內容（權威依據，門檻請以此為準）：")
        msgs.append({"role": "system", "content": f"{label}\n{joined}"})
    # 夾帶最近數輪對話
    for m in (history or [])[-6:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = str(m.get("content", "")).strip()
        if content:
            msgs.append({"role": role, "content": content})
    # 強化語言指令（緊鄰使用者問題，避免被前文中文內容帶偏）
    lang_directive = ("Reminder: respond in English only."
                      if lang == "en" else "提醒：請以繁體中文回答。")
    msgs.append({"role": "system", "content": lang_directive})
    msgs.append({"role": "user", "content": user_msg})
    return msgs


def _post_chat(messages):
    url = f"{AI_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": AI_MODEL, "messages": messages, "temperature": 0.4}
    resp = requests.post(url, headers=headers, json=payload, timeout=AI_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def ai_available():
    return bool(AI_API_KEY)


def chat(user_msg, history=None, lang="zh"):
    """RF 助理問答主入口。

    回傳 dict: { ok, mode('ai'|'extractive'), answer, sources(list[str]) }
    """
    lang = "en" if str(lang).lower().startswith("en") else "zh"
    user_msg = (user_msg or "").strip()
    if not user_msg:
        return {"ok": False, "error": "empty"}

    contexts = retrieve(user_msg, k=4)
    sources = sorted({c["source"] for c in contexts})

    if ai_available():
        try:
            messages = _build_messages(history, user_msg, contexts, lang)
            answer = _post_chat(messages)
            if answer:
                return {"ok": True, "mode": "ai", "answer": answer, "sources": sources}
        except Exception as e:  # noqa: BLE001
            print(f"[rf_assistant] LLM 呼叫失敗，改用擷取式回答：{e}", flush=True)

    # Fallback：無金鑰 / 失敗 → 直接回傳最相關知識段落
    if contexts:
        if lang == "en":
            head = "AI service is unavailable; showing the most relevant knowledge-base excerpts:"
        else:
            head = "AI 服務暫時無法使用，以下提供知識庫中最相關的內容："
        body = "\n\n".join(f"• {c['text']}" for c in contexts[:2])
        return {"ok": True, "mode": "extractive", "answer": f"{head}\n\n{body}", "sources": sources}

    msg = ("找不到相關資料，且 AI 服務暫時無法使用，請稍後再試或補充知識庫文件。"
           if lang == "zh" else
           "No relevant data found and AI is unavailable. Please try later or add knowledge files.")
    return {"ok": True, "mode": "extractive", "answer": msg, "sources": []}

"""
Core logic: disposition check + warrant screening + sector momentum
Refactored from disposition_checker.py for web use
"""

import re
import json
import math
import warnings
import requests
import urllib3
from datetime import date, timedelta
from collections import Counter

# 台灣政府網站 SSL 憑證在海外伺服器驗證會失敗，關閉警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 建立共用 session，跳過 SSL 驗證
_session = requests.Session()
_session.verify = False

# ============================================================
#  Constants
# ============================================================

INDUSTRY_MAP = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙",
    "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造",
    "15": "航運", "16": "觀光餐飲", "17": "金融保險", "18": "貿易百貨",
    "20": "其他", "21": "化學", "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體", "25": "電腦週邊", "26": "光電", "27": "通信網路",
    "28": "電子零組件", "29": "電子通路", "30": "資訊服務", "31": "其他電子",
    "32": "文化創意", "33": "農業科技", "34": "電子商務", "35": "綠能環保",
    "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "91": "存託憑證",
}

INDUSTRY_TO_INDEX = {
    "水泥": "水泥類指數", "食品": "食品類指數", "塑膠": "塑膠類指數",
    "紡織": "紡織纖維類指數", "電機機械": "電機機械類指數",
    "電器電纜": "電器電纜類指數", "玻璃陶瓷": "玻璃陶瓷類指數",
    "造紙": "造紙類指數", "鋼鐵": "鋼鐵類指數", "橡膠": "橡膠類指數",
    "汽車": "汽車類指數", "建材營造": "建材營造類指數", "航運": "航運類指數",
    "觀光餐飲": "觀光餐旅類指數", "金融保險": "金融保險類指數",
    "貿易百貨": "貿易百貨類指數", "油電燃氣": "油電燃氣類指數",
    "化學": "化學類指數", "生技醫療": "生技醫療類指數",
    "半導體": "半導體類指數", "電腦週邊": "電腦及週邊設備類指數",
    "光電": "光電類指數", "通信網路": "通信網路類指數",
    "電子零組件": "電子零組件類指數", "電子通路": "電子通路類指數",
    "資訊服務": "資訊服務類指數", "其他電子": "其他電子類指數",
    "綠能環保": "綠能環保類指數", "數位雲端": "數位雲端類指數",
    "運動休閒": "運動休閒類指數", "居家生活": "居家生活類指數",
    "其他": "其他類指數",
}

GOOD_ISSUERS = {
    "980": "元大", "585": "統一", "920": "凱基", "779": "國票",
    "592": "元富", "9B0": "元富", "616": "中信", "9A0": "永豐",
    "700": "兆豐", "910": "群益",
}


# ============================================================
#  Utilities
# ============================================================

def roc_to_date(roc_str):
    roc_str = roc_str.strip()
    if "/" in roc_str:
        parts = roc_str.split("/")
        return date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
    else:
        return date(int(roc_str[:3]) + 1911, int(roc_str[3:5]), int(roc_str[5:7]))


def parse_number(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("%", "")
    if s in ("", "--", "---", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def format_volume(vol):
    if vol is None:
        return None
    if vol >= 1e8:
        return f"{vol/1e8:.1f}億股"
    elif vol >= 1e4:
        return f"{vol/1e4:.0f}萬股"
    else:
        return f"{vol:.0f}股"


# ============================================================
#  Data fetching
# ============================================================

def fetch_industry_and_name_map():
    code_to_industry = {}
    name_to_code = {}

    try:
        resp = _session.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=15)
        resp.raise_for_status()
        for item in resp.json():
            sc = item.get("公司代號", "").strip()
            ic = item.get("產業別", "").strip()
            sn = item.get("公司簡稱", "").strip()
            if sc and ic:
                code_to_industry[sc] = INDUSTRY_MAP.get(ic, ic)
            if sc and sn:
                name_to_code[sn] = sc
    except Exception:
        pass

    try:
        resp = _session.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", timeout=15)
        resp.raise_for_status()
        for item in resp.json():
            sc = item.get("SecuritiesCompanyCode", "").strip()
            ic = item.get("SecuritiesIndustryCode", "").strip()
            sn = item.get("CompanyAbbreviation", "").strip()
            if sc and ic:
                code_to_industry[sc] = INDUSTRY_MAP.get(ic, ic)
            if sc and sn:
                name_to_code[sn] = sc
    except Exception:
        pass

    return code_to_industry, name_to_code


def fetch_sector_index(target_date):
    date_str = target_date.strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=IND&date={date_str}"
    resp = _session.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    result = {}
    if data.get("stat") == "OK" and data.get("tables"):
        for row in data["tables"][0].get("data", []):
            name = row[0].strip()
            close = parse_number(row[1])
            if close is not None:
                result[name] = close
    return result


def fetch_sector_momentum():
    today = date.today()
    latest_data = None
    latest_date = None
    for i in range(5):
        d = today - timedelta(days=i)
        try:
            data = fetch_sector_index(d)
            if data:
                latest_data = data
                latest_date = d
                break
        except Exception:
            continue
    if not latest_data:
        return {}, None, None

    early_data = None
    early_date = None
    for i in range(7, 14):
        d = latest_date - timedelta(days=i)
        try:
            data = fetch_sector_index(d)
            if data:
                early_data = data
                early_date = d
                break
        except Exception:
            continue
    if not early_data:
        return {}, latest_date, None

    momentum = {}
    for name, close in latest_data.items():
        if name in early_data and early_data[name] > 0:
            pct = (close - early_data[name]) / early_data[name] * 100
            momentum[name] = round(pct, 2)
    return momentum, latest_date, early_date


def fetch_twse_disposition(today=None):
    if today is None:
        today = date.today()
    url = "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"
    resp = _session.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    stocks = {}
    if data.get("stat") == "OK" and data.get("data"):
        for row in data["data"]:
            code, name = str(row[2]).strip(), str(row[3]).strip()
            period, level = str(row[6]).strip(), str(row[7]).strip()
            m = re.match(r"(.+?)[～~](.+)", period)
            if m:
                start, end = roc_to_date(m.group(1)), roc_to_date(m.group(2))
                if start <= today <= end:
                    stocks[name] = {"code": code, "name": name, "level": level,
                                    "period": period, "market": "上市"}
    return stocks


def fetch_tpex_disposition(today=None):
    if today is None:
        today = date.today()
    resp = _session.get("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information", timeout=10)
    resp.raise_for_status()
    stocks = {}
    for item in resp.json():
        code = item.get("SecuritiesCompanyCode", "").strip()
        name = item.get("CompanyName", "").strip()
        ps = item.get("DispositionPeriod", "").strip()
        if not re.match(r"^\d{4}$", code):
            continue
        m = re.match(r"(\d{7})[～~](\d{7})", ps)
        if m:
            start, end = roc_to_date(m.group(1)), roc_to_date(m.group(2))
            if start <= today <= end:
                stocks[name] = {"code": code, "name": name, "period": ps, "market": "上櫃"}
    return stocks


def fetch_volume_data():
    volume = {}
    today = date.today()
    for i in range(5):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y%m%d")
        try:
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999&date={date_str}"
            resp = _session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("stat") == "OK" and data.get("tables"):
                for tbl in data["tables"]:
                    if "每日收盤行情" in tbl.get("title", ""):
                        for row in tbl.get("data", []):
                            code = row[0].strip()
                            vol = parse_number(row[2])
                            if code and vol is not None:
                                volume[code] = vol
                        break
                if volume:
                    break
        except Exception:
            continue

    try:
        resp = _session.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            timeout=15,
        )
        resp.raise_for_status()
        for item in resp.json():
            code = item.get("SecuritiesCompanyCode", "").strip()
            vol = parse_number(item.get("TradingShares"))
            if code and vol is not None:
                volume[code] = vol
    except Exception:
        pass

    return volume


def fetch_margin_data():
    margin = {}
    today = date.today()
    for i in range(5):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y%m%d")
        try:
            url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=json&date={date_str}&selectType=ALL"
            resp = _session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("stat") == "OK" and data.get("tables"):
                for tbl in data["tables"]:
                    title = tbl.get("title", "")
                    if "融資融券" in title and "彙總" in title:
                        for row in tbl.get("data", []):
                            code = str(row[0]).strip()
                            balance = parse_number(row[6])
                            limit = parse_number(row[7])
                            if code and balance is not None and limit and limit > 0:
                                margin[code] = round(balance / limit * 100, 2)
                        break
                if margin:
                    break
        except Exception:
            continue

    for i in range(5):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y/%m/%d")
        try:
            url = f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={date_str}&response=json"
            resp = _session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("tables"):
                for tbl in data["tables"]:
                    for row in tbl.get("data", []):
                        code = str(row[0]).strip()
                        rate = parse_number(row[8])
                        if code and rate is not None:
                            margin[code] = rate
                if margin:
                    break
        except Exception:
            continue

    return margin


# ============================================================
#  Warrant scoring + fetching
# ============================================================

def compute_warrant_score(w):
    score = 0
    lev = w.get("leverage")
    if lev is not None:
        if 3 <= lev <= 8:
            score += 40 * min(lev / 5, 1)
        elif lev > 8:
            score += 40 * max(0, 1 - (lev - 8) / 8)
        elif lev > 0:
            score += 40 * (lev / 3)
    else:
        score += 10

    spread = w.get("spread")
    if spread is not None and spread < 100:
        score += 25 * max(0, 1 - spread / 15)
    elif spread is None:
        score += 10

    iv_val = w.get("iv_bid") or w.get("iv")
    if iv_val is not None:
        score += 20 * max(0, 1 - max(0, iv_val - 40) / 80)
    else:
        score += 8

    out = w.get("outstanding")
    if out is not None:
        score += 15 * max(0, 1 - out / 60)
    else:
        score += 5

    return round(score, 1)


def fetch_warrants_for_stock(stock_code, min_days=120, max_outstanding=70, top_n=5):
    url = "https://www.warrantwin.com.tw/eyuanta/ws/GetWarData.ashx"
    payload = {
        "format": "JSON",
        "factor": {
            "columns": [
                "FLD_WAR_ID", "FLD_WAR_NM", "FLD_UND_ID", "FLD_UND_NM",
                "FLD_WAR_TYPE", "FLD_OBJ_TXN_PRICE", "FLD_ISSUE_AGT_ID",
                "FLD_WAR_TXN_PRICE", "FLD_WAR_BUY_PRICE", "FLD_WAR_SELL_PRICE",
                "FLD_N_STRIKE_PRC", "FLD_N_UND_CONVER",
                "FLD_PERIOD", "FLD_DUR_END", "FLD_IN_OUT",
                "FLD_IV_CLOSE_PRICE", "FLD_IV_BUY_PRICE",
                "FLD_DELTA", "FLD_LEVERAGE", "FLD_BUY_SELL_RATE",
                "FLD_OUT_VOL_RATE", "FLD_WAR_TXN_VOLUME",
            ],
            "condition": [
                {"field": "FLD_UND_ID", "values": [stock_code]},
                {"field": "FLD_WAR_TYPE", "values": ["1"]},
                {"field": "FLD_PERIOD", "left": str(min_days)},
                {"field": "FLD_OUT_VOL_RATE", "right": str(max_outstanding)},
                {"field": "FLD_IN_OUT_DECIMAL", "left": "-20", "right": "5"},
                {"field": "FLD_ISSUE_AGT_ID", "values": list(GOOD_ISSUERS.keys())},
            ],
            "orderby": {"field": "FLD_LEVERAGE", "sort": "DESC"}
        },
        "pagination": {"row": str(top_n * 4), "page": "1"}
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.warrantwin.com.tw/eyuanta/Warrant/Search.aspx",
    }
    resp = _session.post(url, data={"data": json.dumps(payload)}, headers=headers, timeout=15)
    resp.raise_for_status()
    result = resp.json()

    warrants = []
    for item in result.get("result", []):
        w = {
            "code": item.get("FLD_WAR_ID", ""),
            "name": item.get("FLD_WAR_NM", ""),
            "issuer_id": item.get("FLD_ISSUE_AGT_ID", ""),
            "price": parse_number(item.get("FLD_WAR_TXN_PRICE")),
            "bid": parse_number(item.get("FLD_WAR_BUY_PRICE")),
            "ask": parse_number(item.get("FLD_WAR_SELL_PRICE")),
            "strike": parse_number(item.get("FLD_N_STRIKE_PRC")),
            "days_left": parse_number(item.get("FLD_PERIOD")),
            "expiry": item.get("FLD_DUR_END", ""),
            "in_out": item.get("FLD_IN_OUT", ""),
            "iv": parse_number(item.get("FLD_IV_CLOSE_PRICE")),
            "iv_bid": parse_number(item.get("FLD_IV_BUY_PRICE")),
            "leverage": parse_number(item.get("FLD_LEVERAGE")),
            "spread": parse_number(item.get("FLD_BUY_SELL_RATE")),
            "outstanding": parse_number(item.get("FLD_OUT_VOL_RATE")),
        }
        if w["bid"] is None and w["ask"] is None and w["price"] is None:
            continue
        if w["spread"] is not None and w["spread"] >= 100:
            continue
        # 程式端 hard filter: 價內5%~價外20%
        in_out_str = w["in_out"]
        in_out_pct = parse_number(re.sub(r"[%價內外]", "", in_out_str)) if in_out_str else None
        if in_out_pct is not None:
            if "價內" in in_out_str and in_out_pct > 5:
                continue
            if "價外" in in_out_str and in_out_pct > 20:
                continue
        w["score"] = compute_warrant_score(w)
        w["issuer"] = GOOD_ISSUERS.get(w["issuer_id"], "")
        warrants.append(w)

    warrants.sort(key=lambda x: -x["score"])
    return warrants[:top_n]


# ============================================================
#  Input parsing
# ============================================================

def parse_input(text):
    lines = text.strip().splitlines()
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(.+?):\s*(.+)$", line)
        if m:
            day_label = m.group(1).strip()
            stock_str = m.group(2).strip()
            names = [s.strip() for s in re.split(r"[、,，\s]+", stock_str) if s.strip()]
            result.append((day_label, names))
    return result


def match_disposition(name, all_disposition):
    clean = name.replace("*", "").strip()
    for dn in all_disposition:
        cd = dn.replace("*", "").strip()
        if clean == cd or clean in cd or cd in clean:
            return all_disposition[dn]
    return None


# ============================================================
#  Stock scoring
# ============================================================

def compute_stock_scores(stock_entries, sector_momentum, volume_data, margin_data):
    industry_counts = Counter()
    for s in stock_entries:
        if s["industry"]:
            industry_counts[s["industry"]] += 1

    max_count = max(industry_counts.values()) if industry_counts else 1

    momentum_values = [v for v in sector_momentum.values() if v is not None]
    max_momentum = max(momentum_values) if momentum_values else 1
    min_momentum = min(momentum_values) if momentum_values else 0
    momentum_range = max_momentum - min_momentum if max_momentum != min_momentum else 1

    volumes = []
    for s in stock_entries:
        vol = volume_data.get(s["code"])
        s["volume"] = vol
        if vol is not None and vol > 0:
            volumes.append(vol)

    log_volumes = [math.log10(v) for v in volumes] if volumes else []
    max_log_vol = max(log_volumes) if log_volumes else 1
    min_log_vol = min(log_volumes) if log_volumes else 0
    log_vol_range = max_log_vol - min_log_vol if max_log_vol != min_log_vol else 1

    for s in stock_entries:
        score = 0
        ind = s["industry"]

        if ind and ind in industry_counts:
            count = industry_counts[ind]
            score += 35 * (count / max_count)

        idx_name = INDUSTRY_TO_INDEX.get(ind, "")
        if idx_name and idx_name in sector_momentum:
            mom = sector_momentum[idx_name]
            score += 35 * max(0, (mom - min_momentum) / momentum_range)

        vol = s.get("volume")
        if vol is not None and vol > 0:
            log_vol = math.log10(vol)
            score += 30 * max(0, (log_vol - min_log_vol) / log_vol_range)

        s["margin_rate"] = margin_data.get(s["code"])
        s["stock_score"] = round(score, 1)
        s["cluster_count"] = industry_counts.get(ind, 0) if ind else 0

        idx_name = INDUSTRY_TO_INDEX.get(ind, "")
        s["sector_mom"] = sector_momentum.get(idx_name, None) if idx_name else None


# ============================================================
#  Main analysis (returns JSON-friendly dict)
# ============================================================

def run_analysis(input_text, progress_cb=None):
    """
    Run the full analysis pipeline.
    progress_cb: optional callback(step, total, message) for progress updates.
    Returns a dict with all results.
    """
    today = date.today()

    def progress(step, total, msg):
        if progress_cb:
            progress_cb(step, total, msg)

    progress(1, 6, "取得處置股資料...")
    try:
        twse_disp = fetch_twse_disposition(today)
    except Exception:
        twse_disp = {}
    try:
        tpex_disp = fetch_tpex_disposition(today)
    except Exception:
        tpex_disp = {}
    all_disposition = {**twse_disp, **tpex_disp}

    progress(2, 6, "取得產業別與公司名稱...")
    industry_map, all_name_to_code = fetch_industry_and_name_map()

    progress(3, 6, "計算產業指數動能...")
    sector_momentum, latest_dt, early_dt = fetch_sector_momentum()

    progress(4, 6, "取得成交量...")
    volume_data = fetch_volume_data()

    progress(5, 6, "取得融資使用率...")
    margin_data = fetch_margin_data()

    # Parse input
    parsed = parse_input(input_text)
    if not parsed:
        return {"error": "無法解析輸入格式，請使用「周二(3/24): 股票A、股票B」格式"}

    # Build name->code mapping
    name_to_code = dict(all_name_to_code)
    for name, info in all_disposition.items():
        name_to_code[name] = info["code"]

    def find_code(name):
        clean = name.replace("*", "").strip()
        if clean in name_to_code:
            return name_to_code[clean]
        for k, v in name_to_code.items():
            ck = k.replace("*", "").strip()
            if clean == ck or clean in ck or ck in clean:
                return v
        return None

    # Build stock entries
    seen = set()
    stock_entries = []
    for day_label, names in parsed:
        for name in names:
            code = find_code(name)
            if code and code not in seen:
                seen.add(code)
                ind = industry_map.get(code, "")
                disp = match_disposition(name, all_disposition)
                stock_entries.append({
                    "name": name, "code": code, "industry": ind,
                    "disp_info": disp, "day_label": day_label,
                })

    compute_stock_scores(stock_entries, sector_momentum, volume_data, margin_data)

    # Fetch warrants
    progress(6, 6, f"查詢 {len(stock_entries)} 檔標的的權證...")
    warrant_cache = {}
    for s in stock_entries:
        try:
            warrant_cache[s["code"]] = fetch_warrants_for_stock(s["code"])
        except Exception:
            warrant_cache[s["code"]] = []

    code_to_entry = {s["code"]: s for s in stock_entries}

    # Build output structure
    days = []
    all_names = []
    for day_label, names in parsed:
        all_names.extend(names)
        day_stocks = []
        for name in names:
            code = find_code(name)
            entry = code_to_entry.get(code) if code else None
            disp = match_disposition(name, all_disposition)
            warrants = warrant_cache.get(code, []) if code else []

            stock_data = {
                "name": name,
                "code": code or "?",
                "industry": entry["industry"] if entry else "",
                "stock_score": entry["stock_score"] if entry else 0,
                "cluster_count": entry["cluster_count"] if entry else 0,
                "sector_mom": entry["sector_mom"] if entry else None,
                "volume": entry.get("volume") if entry else None,
                "volume_str": format_volume(entry.get("volume")) if entry else None,
                "margin_rate": entry.get("margin_rate") if entry else None,
                "is_disposition": disp is not None,
                "disp_level": disp.get("level", "") if disp else "",
                "warrants": warrants,
            }
            day_stocks.append(stock_data)

        day_stocks.sort(key=lambda x: -x["stock_score"])
        days.append({"label": day_label, "stocks": day_stocks})

    # Cluster summary
    ind_counts = Counter()
    for s in stock_entries:
        if s["industry"]:
            ind_counts[s["industry"]] += 1

    clusters = []
    for ind, cnt in ind_counts.most_common():
        idx_name = INDUSTRY_TO_INDEX.get(ind, "")
        mom = sector_momentum.get(idx_name)
        clusters.append({"industry": ind, "count": cnt, "momentum": mom})

    # Top sectors
    top_sectors = []
    if sector_momentum:
        for name, pct in sorted(sector_momentum.items(), key=lambda x: -x[1])[:5]:
            top_sectors.append({"name": name, "pct": pct})

    unique_names = list(set(all_names))
    disp_count = sum(1 for n in unique_names if match_disposition(n, all_disposition))
    has_warrants = sum(1 for v in warrant_cache.values() if v)

    return {
        "date": today.strftime("%Y-%m-%d"),
        "disposition_count": len(all_disposition),
        "sector_period": f"{early_dt} ~ {latest_dt}" if early_dt and latest_dt else None,
        "top_sectors": top_sectors,
        "days": days,
        "clusters": clusters,
        "stats": {
            "total": len(unique_names),
            "disposition": disp_count,
            "non_disposition": len(unique_names) - disp_count,
            "with_warrants": has_warrants,
        },
        "issuers": sorted(set(GOOD_ISSUERS.values())),
    }

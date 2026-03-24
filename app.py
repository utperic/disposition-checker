from flask import Flask, render_template, request, jsonify
import json
import time
import requests
from checker import run_analysis, fetch_warrants_for_stock, _session

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    input_text = data.get("input", "").strip()
    if not input_text:
        return jsonify({"error": "請輸入明牌資料"}), 400

    try:
        result = run_analysis(input_text)
    except Exception as e:
        return jsonify({"error": f"分析失敗: {str(e)}"}), 500
    return jsonify(result)


@app.route("/refresh_quotes", methods=["POST"])
def refresh_quotes():
    """Fetch real-time stock quotes from TWSE/TPEx"""
    data = request.get_json()
    # stocks: [{code, market}] where market is "上市" or "上櫃"
    stocks = data.get("stocks", [])
    if not stocks:
        return jsonify({"error": "No stocks provided"}), 400

    # Build query: tse_XXXX.tw for TWSE, otc_XXXX.tw for TPEx
    parts = []
    for s in stocks:
        prefix = "tse" if s.get("market") != "上櫃" else "otc"
        parts.append(f"{prefix}_{s['code']}.tw")

    # TWSE mis API needs session cookie — visit index page first if needed
    query = "|".join(parts)
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={query}"
    raw = None
    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(1)
            resp = _session.get(url, timeout=10)
            resp.raise_for_status()
            # Check if we got HTML instead of JSON (missing session cookie)
            ct = resp.headers.get("Content-Type", "")
            if "html" in ct or resp.text.strip().startswith("<!"):
                # Get session cookie by visiting index page
                _session.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)
                resp = _session.get(url, timeout=10)
                resp.raise_for_status()
            raw = resp.json()
            break
        except Exception as e:
            if attempt >= 2:
                return jsonify({"error": str(e)}), 500

    result = {}
    for item in raw.get("msgArray", []):
        code = item.get("c", "")
        price = item.get("z", "-")  # 成交價
        prev_close = item.get("y", "-")  # 昨收
        open_price = item.get("o", "-")  # 開盤
        high = item.get("h", "-")  # 最高
        low = item.get("l", "-")  # 最低
        volume = item.get("v", "-")  # 成交量(張)
        name = item.get("n", "")
        time = item.get("t", "")  # 時間

        # Best bid/ask from 五檔
        bid_str = item.get("b", "")  # 買價 (best first, _ separated)
        ask_str = item.get("a", "")  # 賣價 (best first, _ separated)
        best_bid = None
        best_ask = None
        spread = None
        spread_pct = None
        try:
            if bid_str:
                b1 = bid_str.split("_")[0]
                if b1 and b1 != "-":
                    best_bid = float(b1)
            if ask_str:
                a1 = ask_str.split("_")[0]
                if a1 and a1 != "-":
                    best_ask = float(a1)
            if best_bid and best_ask and best_bid > 0:
                spread = round(best_ask - best_bid, 2)
                spread_pct = round(spread / best_bid * 100, 2)
        except (ValueError, IndexError):
            pass

        # Calculate change
        change = None
        change_pct = None
        try:
            p = float(price) if price != "-" else None
            y = float(prev_close) if prev_close != "-" else None
            if p and y:
                change = round(p - y, 2)
                change_pct = round((p - y) / y * 100, 2)
        except (ValueError, ZeroDivisionError):
            pass

        result[code] = {
            "name": name,
            "price": price,
            "prev_close": prev_close,
            "open": open_price,
            "high": high,
            "low": low,
            "volume": volume,
            "time": time,
            "change": change,
            "change_pct": change_pct,
            "bid": best_bid,
            "ask": best_ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "odd_price": None,
            "odd_diff_pct": None,
        }

    # Fetch odd-lot (盤中零股) quotes
    try:
        odd_url = f"https://mis.twse.com.tw/stock/api/getOddInfo.jsp?ex_ch={query}"
        odd_resp = _session.get(odd_url, timeout=10)
        odd_resp.raise_for_status()
        odd_raw = odd_resp.json()
        for item in odd_raw.get("msgArray", []):
            code = item.get("c", "")
            odd_z = item.get("z", "-")
            if code in result and odd_z and odd_z != "-":
                try:
                    odd_p = float(odd_z)
                    result[code]["odd_price"] = odd_p
                    # Calculate diff vs regular price
                    reg_p = float(result[code]["price"]) if result[code]["price"] != "-" else None
                    if reg_p and reg_p > 0:
                        result[code]["odd_diff_pct"] = round((odd_p - reg_p) / reg_p * 100, 2)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass  # Odd-lot data is optional

    return jsonify(result)


@app.route("/debug")
def debug():
    """Test if Taiwan APIs are accessible from this server"""
    results = {}

    tests = [
        ("TWSE 處置股", "https://www.twse.com.tw/rwd/zh/announcement/punish?response=json"),
        ("TWSE 公司列表", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
        ("TPEx 公司列表", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"),
        ("TPEx 處置股", "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"),
        ("元大權證", "https://www.warrantwin.com.tw/eyuanta/Warrant/Search.aspx"),
    ]

    for name, url in tests:
        try:
            resp = requests.get(url, timeout=10, verify=False)
            body = resp.text[:200]
            results[name] = {
                "status": resp.status_code,
                "size": len(resp.text),
                "preview": body,
            }
        except Exception as e:
            results[name] = {"error": str(e)}

    return jsonify(results)


@app.route("/debug_disp")
def debug_disp():
    """Check disposition matching for 欣興"""
    from checker import (fetch_twse_disposition, fetch_tpex_disposition,
                         match_disposition, parse_input,
                         fetch_industry_and_name_map)
    from datetime import date
    today = date.today()

    results = {}
    twse_disp = {}
    tpex_disp = {}
    try:
        twse_disp = fetch_twse_disposition(today)
        results["twse_count"] = len(twse_disp)
        results["twse_has_欣興"] = "欣興" in twse_disp
        if "欣興" in twse_disp:
            results["twse_欣興"] = twse_disp["欣興"]
    except Exception as e:
        results["twse_error"] = str(e)

    try:
        tpex_disp = fetch_tpex_disposition(today)
        results["tpex_count"] = len(tpex_disp)
    except Exception as e:
        results["tpex_error"] = str(e)

    all_disp = {**twse_disp, **tpex_disp}
    results["total_disp"] = len(all_disp)
    results["all_disp_names"] = sorted(all_disp.keys())

    # Test match
    m = match_disposition("欣興", all_disp)
    results["match_欣興"] = m is not None
    results["match_detail"] = str(m) if m else None

    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True, port=5000)

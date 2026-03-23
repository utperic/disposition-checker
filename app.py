from flask import Flask, render_template, request, jsonify
import json
import requests
from checker import run_analysis

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

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
            resp = requests.get(url, timeout=10)
            body = resp.text[:200]
            results[name] = {
                "status": resp.status_code,
                "size": len(resp.text),
                "preview": body,
            }
        except Exception as e:
            results[name] = {"error": str(e)}

    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True, port=5000)

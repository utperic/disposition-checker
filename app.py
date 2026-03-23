from flask import Flask, render_template, request, jsonify, Response
import json
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

    result = run_analysis(input_text)
    return jsonify(result)


@app.route("/analyze_stream", methods=["POST"])
def analyze_stream():
    """SSE endpoint for progress updates"""
    data = request.get_json()
    input_text = data.get("input", "").strip()
    if not input_text:
        return jsonify({"error": "請輸入明牌資料"}), 400

    def generate():
        def on_progress(step, total, msg):
            yield f"data: {json.dumps({'type': 'progress', 'step': step, 'total': total, 'message': msg})}\n\n"

        # We can't use yield inside callback easily with Flask,
        # so run analysis and send result at the end
        result = run_analysis(input_text)
        yield f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=True, port=5000)

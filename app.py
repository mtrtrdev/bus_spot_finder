import os
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import google.generativeai as genai
from time import time

from config import APP_CONFIG

# .envファイルから環境変数を読み込む
load_dotenv()

app = Flask(__name__)

MAPS_API_KEY = os.getenv("MAPS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

@app.route('/')
def index_default():
    # デフォルトはconfigで指定されたデータセットを使う
    dataset = APP_CONFIG.get("defaultDataset", "kyoto_bus_route_historical_sites")
    return render_template('index.html', maps_api_key=MAPS_API_KEY, dataset=dataset, cache_buster=int(time()))

@app.route('/kyoto_bus_route_historical_sites')
def index_kyoto():
    dataset = "kyoto_bus_route_historical_sites"
    return render_template('index.html', maps_api_key=MAPS_API_KEY, dataset=dataset, cache_buster=int(time()))

@app.route('/tokyo_bus_route_cafes')
def index_tokyo():
    dataset = "tokyo_bus_route_cafes"
    return render_template('index.html', maps_api_key=MAPS_API_KEY, dataset=dataset, cache_buster=int(time()))

@app.route('/generate_description', methods=['POST'])
def generate_description():
    data = request.json
    place_name = data.get('name')
    place_address = data.get('address')

    if not place_name:
        return jsonify({"error": "場所の名前が提供されていません"}), 400

    prompt = (
        f"以下のスポット情報について、厳格に以下の出力形式に則って回答文を生成してください。\n"
        f"一般ユーザーに提供する情報のため、情報精度には最大限の注意を払ってください。\n\n"
        f"# 指示\n"
        f"・導入：その場所の一番の魅力や核となる情報を簡潔に（約50字程度で）まとめてください。\n"
        f"・展開：具体的な見どころやエピソードを交え、訪れた人がどのような体験ができるか伝わるように（約100字程度で）記述してください。\n"
        f"・結び：訪れる人への呼びかけや、その場所への興味を惹くようなメッセージを（約50字程度で）加えてください。\n\n"
        f"# 出力形式\n"
        f"以下の要素を**自然な文章で構成**してください。小項目や文字数は表示しないでください。\n\n"
        f" 導入に該当する文章を記載 \n 展開に該当する文章を記載 \n 結びに該当する文章を記載\n"
        f"# 入力データ\n"
        f"{place_name} - {place_address or '所在地不明'}"
    )
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=300,
                temperature=0.7
            )
        )
        generated_text = response.text
        return jsonify({"description": generated_text})
    except Exception as e:
        print(f"Gemini API呼び出し中にエラーが発生しました: {e}")
        return jsonify({"error": "回答の生成中にエラーが発生しました。"}), 500

@app.route('/get_map_config')
def get_map_config():
    # URLクエリパラメータ "dataset" により設定を切り替え
    dataset = request.args.get("dataset")
    if not dataset:
        dataset = APP_CONFIG.get("defaultDataset", "kyoto_bus_route_historical_sites")
    dataset_config = APP_CONFIG["datasets"].get(dataset)
    if not dataset_config:
        return jsonify({"error": "指定された設定が見つかりません"}), 404
    config = {
        "siteInfo": dataset_config.get("siteInfo", {}),
        "map": dataset_config.get("map", {}),
        "themes": dataset_config.get("themes", {}),
        "busStops": dataset_config.get("busStops", {}),
        # 各データセット内で初期プリセット値を個別に設定している場合は追記可能
        "initialPreset": list(dataset_config.get("busStops", {}).keys())[0] if dataset_config.get("busStops") else ""
    }
    return jsonify(config)

if __name__ == '__main__':
    app.run(debug=True)
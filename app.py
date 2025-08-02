import os
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import google.generativeai as genai
from time import time
from cachetools import LFUCache
import json
import logging
from datetime import datetime
from collections import deque, defaultdict

from config import APP_CONFIG

# .envファイルから環境変数を読み込む
load_dotenv()

app = Flask(__name__)

# --- ロギング設定 ---
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO) # INFOレベルに設定

app.logger.info("Flask application started and logging configured.")

# --- 管理者ダッシュボード用の新しいデータ構造 ---
access_stats = defaultdict(lambda: {
    "page_views": 0,
    "searches_by_stop": defaultdict(int)
})

# 最新50件の検索履歴は別途保持
search_history = deque(maxlen=50)


MAPS_API_KEY = os.getenv("MAPS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

description_cache = LFUCache(maxsize=100)
map_config_cache = LFUCache(maxsize=10)


def log_page_view(dataset):
    if dataset:
        access_stats[dataset]["page_views"] += 1
        app.logger.info(f"Page view logged for: {dataset}. Total: {access_stats[dataset]['page_views']}")


@app.route('/')
def index_default():
    dataset = APP_CONFIG.get("defaultDataset", "kyoto_bus_route_historical_sites")
    log_page_view(dataset)
    return render_template('index.html', maps_api_key=MAPS_API_KEY, dataset=dataset, cache_buster=int(time()))


@app.route('/kyoto_bus_route_historical_sites')
def index_kyoto():
    dataset = "kyoto_bus_route_historical_sites"
    log_page_view(dataset)
    return render_template('index.html', maps_api_key=MAPS_API_KEY, dataset=dataset, cache_buster=int(time()))


@app.route('/tokyo_bus_route_cafes')
def index_tokyo():
    dataset = "tokyo_bus_route_cafes"
    log_page_view(dataset)
    return render_template('index.html', maps_api_key=MAPS_API_KEY, dataset=dataset, cache_buster=int(time()))


@app.route('/admin')
def admin_dashboard():
    app.logger.info("Admin dashboard route accessed.")
    chart_data = {}
    app.logger.debug(f"Current access_stats: {json.dumps(access_stats, indent=2, default=str)}")

    for dataset_key, stats in access_stats.items():
        dataset_config = APP_CONFIG["datasets"].get(dataset_key, {})
        bus_systems = dataset_config.get("busStops", {})

        system_charts = {}
        for system_name, stops in bus_systems.items():
            labels = [stop['name'] for stop in stops]
            data = [stats["searches_by_stop"].get(stop['name'], 0) for stop in stops]
            system_charts[system_name] = {
                "labels": labels,
                "data": data,
                "slug": system_name
            }

        chart_data[dataset_key] = {
            "page_views": stats.get("page_views", 0),
            "total_searches": sum(stats.get("searches_by_stop", {}).values()),
            "system_charts": system_charts,
            "dataset_name": dataset_config.get("siteInfo", {}).get("title", dataset_key)
        }
    
    app.logger.debug(f"Generated chart_data: {json.dumps(chart_data, indent=2, default=str)}")

    sorted_search_history = []
    for item in sorted(search_history, key=lambda x: x['timestamp'], reverse=True):
        dataset_name = APP_CONFIG["datasets"].get(item.get('dataset', ''), {}).get('siteInfo', {}).get('title', item.get('dataset', '不明'))
        system_name_from_config = item.get('system', 'N/A')
        display_system_name = system_name_from_config 

        item['dataset_name'] = dataset_name
        item['system_name'] = display_system_name
        sorted_search_history.append(item)

    return render_template(
        'admin.html',
        chart_data_json=json.dumps(chart_data),
        search_history=sorted_search_history
    )

@app.route('/log_action', methods=['POST'])
def log_action():
    data = request.get_json()
    action = data.get('action')
    dataset = data.get('dataset')
    system = data.get('system')
    bus_stop_name = data.get('bus_stop_name')
    place_name = data.get('place_name')

    if not all([action, dataset]):
        return jsonify({"error": "必須パラメータが不足しています"}), 400

    if action == "nearby_search":
        # アクセス統計を更新
        if bus_stop_name:
            access_stats[dataset]["searches_by_stop"][bus_stop_name] += 1
            app.logger.info(f"Search logged for bus stop: '{bus_stop_name}' in {dataset} with system: {system}")
        
        # ログデータを作成し、検索履歴に追加
        log_entry = {
            "dataset": dataset,
            "bus_stop_name": bus_stop_name,
            "system": system,
            "place_name": place_name,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        search_history.append(log_entry)
        app.logger.info(f"Action logged: {action} on dataset '{dataset}'")
    
    return jsonify({"status": "success"}), 200

@app.route('/generate_description', methods=['POST'])
def generate_description():
    data = request.get_json()
    place_name = data.get('name')
    place_address = data.get('address')
    dataset = data.get('dataset')

    if not place_name:
        return jsonify({"error": "場所の名前が提供されていません"}), 400

    cache_key = f"{place_name}-{place_address}"
    cached_response = description_cache.get(cache_key)
    if cached_response:
        app.logger.info(f"Cache HIT for description: {cache_key}")
        return jsonify({"description": cached_response})

    app.logger.info(f"Cache MISS for description: {cache_key}")
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
        description_cache[cache_key] = generated_text
        app.logger.info(f"Cached new description for: {cache_key}")
        return jsonify({"description": generated_text})
    except Exception as e:
        app.logger.error(f"Gemini API error for {cache_key}: {e}")
        return jsonify({"error": "回答の生成中にエラーが発生しました。"}), 500

@app.route('/get_map_config')
def get_map_config():
    dataset = request.args.get("dataset")
    if not dataset:
        dataset = APP_CONFIG.get("defaultDataset", "kyoto_bus_route_historical_sites")

    cached_config = map_config_cache.get(dataset)
    if cached_config:
        app.logger.info(f"Cache HIT for map_config: {dataset}")
        return jsonify(cached_config)

    app.logger.info(f"Cache MISS for map_config: {dataset}")
    dataset_config = APP_CONFIG["datasets"].get(dataset)
    if not dataset_config:
        return jsonify({"error": "指定された設定が見つかりません"}), 404
    config = {
        "siteInfo": dataset_config.get("siteInfo", {}),
        "map": dataset_config.get("map", {}),
        "themes": dataset_config.get("themes", {}),
        "busStops": dataset_config.get("busStops", {}),
        "initialPreset": list(dataset_config.get("busStops", {}).keys())[0] if dataset_config.get("busStops") else ""
    }
    map_config_cache[dataset] = config
    app.logger.info(f"Cached new map_config for: {dataset}")
    return jsonify(config)

if __name__ == '__main__':
    app.run(debug=True)
import asyncio
import os
import time
import threading
import json

from dash import Dash, html, dcc, callback, Output, Input
from dotenv import load_dotenv

# クラスを分割したファイルからインポート
from real_time_agent import RealTimeAgent

load_dotenv('api.env')

API_KEY = os.getenv('OPENAI_API_KEY')

# WebSocket URLとヘッダー情報
WS_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
HEADERS = {
    "Authorization": "Bearer " + API_KEY,
    "OpenAI-Beta": "realtime=v1"
}

# prompt.jsonを読み込む
instructions = {}
with open("./data/prompts.json", "r", encoding="utf-8") as f:
    prompt = f.read()
    instructions = json.loads(prompt)["instructions"]
    print(instructions)

# Dashアプリの設定
app = Dash()

text_area = html.Div(
    id='input_text',
    style={
        'width': '100%',
        'height': '700px',
        'boarderRadius': 10,
        'border': '1px solid #AAA',
        'readonly': True,
        'fontFamily': 'Arial',
        'fontSize': 24,
        'marginBottom': 10,
        'backgroundColor': '#F0F0F0',
        "overflow": "scroll",
        "padding": 3
    },
    children=[""]
)

app.layout = [
    html.Div(
        children=[
            html.H1(children='山県昌景',
                    style={'textAlign': 'left', 'fontSize': 50, 'marginBottom': 3}),
            html.P(children='山県昌景（やまがた まさかげ、生没年不詳）は、鎌倉時代の武士である。',
                   style={'textAlign': 'left', 'marginBottom': 10, 'fontSize': 30}),
            text_area,
        ],
        style={
            'textAlign': 'left',
            'paddingLeft': 20,
            'paddingRight': 20,
            'backgroundColor': '#FDFDFD',
        }
    ),
    html.Button('開始', id='submit-val', n_clicks=0,
                style={'marginLeft': 20, 'marginTop': 10, 'fontSize': 30, 'fontWeight': 'bold'}),
    dcc.Interval(
        id='interval-component',
        interval=500,  # in milliseconds
        n_intervals=0
    )
]

is_started = False

@app.callback(
    Input('submit-val', 'n_clicks'),
)
def update_output(n_clicks):
    global is_started
    if n_clicks > 0:
        is_started = True
        messages = "***ラジオを開始しました***"
        text_area.children.append(messages)

@app.callback(Output('input_text', 'children'),
              Input('interval-component', 'n_intervals'))
def update_display(n):
    return text_area.children

async def main():
    """
    2つのエージェントを起動し、タスクグループで並行実行する
    """
    try:
        # やまがた用のプロンプト
        agent1_instructions = ""
        with open(
            instructions["initial"]["instruction_path"]["やまがた"], "r", encoding="utf-8"
        ) as f:
            agent1_instructions = f.read()

        agent1_settings = {
            "modalities": ["audio", "text"],
            "instructions": f"あなたはラジオパーソナリティパーソナリティ「やまがた」です。{agent1_instructions}",
            "voice": "alloy",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
            }
        }

        agent1 = RealTimeAgent(
            ws_url=WS_URL,
            headers=HEADERS,
            settings=agent1_settings,
            name="やまがた",
            port=15000,
            dst_port=15001,
            is_first_agent=True,
            instructions=instructions,
            text_area=text_area,  # Dashコンポーネントを渡す
        )

        # まさかげ用のプロンプト
        agent2_instructions = ""
        with open(
            instructions["initial"]["instruction_path"]["まさかげ"], "r", encoding="utf-8"
        ) as f:
            agent2_instructions = f.read()

        agent2_settings = {
            "modalities": ["audio", "text"],
            "instructions": f"あなたはラジオパーソナリティパーソナリティ「まさかげ」です。{agent2_instructions}",
            "voice": "shimmer",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
            }
        }

        agent2 = RealTimeAgent(
            ws_url=WS_URL,
            headers=HEADERS,
            settings=agent2_settings,
            name="まさかげ",
            port=15001,
            dst_port=15000,
            is_first_agent=False,
            instructions=instructions,
            text_area=text_area,  # Dashコンポーネントを渡す
        )

        # タスクグループでAgentを並列実行
        async with asyncio.TaskGroup() as tg:
            tg.create_task(agent1.stream_audio_and_receive_response())
            tg.create_task(agent1.receive_from_ai())
            tg.create_task(agent2.stream_audio_and_receive_response())
            tg.create_task(agent2.receive_from_ai())

    except Exception as e:
        import traceback
        text_area.children.append(str(e))
        text_area.children.append(str(traceback.format_stack()))

def run():
    """
    Dashの開始ボタンが押されるまで待機し、押されれば非同期処理(main)を起動する
    """
    global is_started
    while not is_started:
        time.sleep(1)
    asyncio.run(main())

if __name__ == "__main__":
    # エージェント起動スレッド
    threading.Thread(target=run).start()

    # Dashアプリ起動
    app.run()

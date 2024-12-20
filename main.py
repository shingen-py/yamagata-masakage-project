
import asyncio
import websockets
import pyaudio
import base64
import json
import threading
import os
from dotenv import load_dotenv
from queue import Queue
import socket
from dash import Dash, html, dcc, callback, Output, Input
import time
import logging

# 環境変数の読み込み
load_dotenv('api.env')

API_KEY = os.getenv('OPENAI_API_KEY')

# WebSocket URLとヘッダー情報
# OpenAI
WS_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
HEADERS = {
    "Authorization": "Bearer " + API_KEY,
    "OpenAI-Beta": "realtime=v1"
}


instructions = {}
# prompt.jsonを読み込む
with open("./data/prompts.json", "r", encoding="utf-8") as f:
    prompt = f.read()
    instructions = json.loads(prompt)["instructions"]
    print(instructions)

app = Dash()

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("PIL").setLevel(logging.WARNING)

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
                    "padding" : 3
                    },
                children=[""]
            )

app.layout = [
    html.Div(
        children=[
            html.H1(children='山県昌景', style={'textAlign':'left', 'fontSize': 50, 'marginBottom': 3}),
            html.P(children='山県昌景（やまがた まさかげ、生没年不詳）は、鎌倉時代の武士である。', style={'textAlign':'left', 'marginBottom': 10, 'fontSize': 30}),
            text_area,           
        ], style={'textAlign':'left', 'paddingLeft': 20, 'paddingRight': 20, 'backgroundColor': '#FDFDFD',}
    ),
    html.Button('開始', id='submit-val', n_clicks=0, style={'marginLeft': 20, 'marginTop': 10, 'fontSize': 30, 'fontWeight': 'bold'}),
    dcc.Interval(
        id='interval-component',
        interval=500, # in milliseconds
        n_intervals=0
    )
]

class RealTimeAgent():
    def __init__(
            self,
            ws_url: str,
            headers: dict,
            settings: dict,
            name: str = "assistant",
            port: int = 5000,
            dst_port: int = 5001,
            is_first_agent: bool = False,
            instructions: dict = {},
            audio_receive_queue: Queue[bytes] = Queue(),
            active_audio_received: str = '',
            websocket: websockets.WebSocketClientProtocol = None):
        self.ws_url = ws_url
        self.headers = headers
        self.settings = settings
        self.name = name
        self.port = port
        self.dst_port = dst_port
        self.is_first_agent = is_first_agent
        self.instructions = instructions
        self.audio_receive_queue = audio_receive_queue
        self.active_audio_received = active_audio_received
        self.websocket = websocket

    def base64_to_pcm16(self, base64_audio):
        audio_data = base64.b64decode(base64_audio)
        return audio_data

    async def receive_from_ai(self):
        tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_server.bind(("localhost", self.port))
        tcp_server.settimeout(1)
        tcp_server.listen(1)

        while True:
            try:
                conn, addr = tcp_server.accept()
                with conn:
                    print(f"{self.name}: Connected by {addr}")

                    recv_data = b""

                    while True:
                        data = conn.recv(1024)
                        if len(data) <= 0:
                            break
                        recv_data += data

                    size = round(len(recv_data)/1024, 1)
                    print(f"{self.name}: AIからの音声を受信しました。 {size} KB")

                    if size <= 0:
                        continue

                    trigger_event = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": recv_data.decode("utf-8")
                                }
                            ]
                        }
                    }
                    await self.websocket.send(json.dumps(trigger_event))
                    await self.websocket.send(json.dumps(
                        {"type": "response.create"}
                    ))
                    print(f"{self.name}: AIからの応答をAIに送信しました。")

                    # キューの処理間隔を少し空ける
                    await asyncio.sleep(1)
            except Exception:
                pass
                # print(f"{self.name}: エラーが発生しました: {e}")
                # スタックトレースを表示
                # import traceback
                # traceback.print_exc()
            finally:
                await asyncio.sleep(3)

    # サーバーから音声を受信してキューに格納する非同期関数
    async def receive_audio_to_queue(self):
        number_of_audio_received = 0
        current_state = "initial"
        current_message = ""
        while True:
            response = await self.websocket.recv()
            if response:
                response_data = json.loads(response)

                # サーバーからの応答をリアルタイムに表示
                if "type" in response_data and response_data["type"] \
                        == "response.audio_transcript.delta":
                    if self.active_audio_received == "":
                        print(f"\n{self.name}: ", end="", flush=True)
                    print(response_data["delta"], end="", flush=True)
                    self.active_audio_received += response_data["delta"]
                    if current_message == "":
                        current_message += f"{self.name}: {response_data["delta"]}"
                        text_area.children.append(html.P(f"{self.name}: {current_message}"))
                    else:
                        current_message += response_data["delta"]
                        text_area.children[len(text_area.children) - 1] = html.P(current_message)


                # サーバからの応答が完了したことを取得
                elif "type" in response_data and response_data["type"] \
                        == "response.audio_transcript.done":
                    print("\n", end="", flush=True)
                    current_message = ""

                    # 設定の更新
                    number_of_audio_received = number_of_audio_received + 1
                    if number_of_audio_received >= self.instructions[current_state]["number_of_executions"]:
                        next_state = self.instructions[current_state]["next_state"]

                        if next_state == "end":
                            # TODO
                            # print(f"\n{self.name}: ラジオを終了します。")
                            # break
                            pass
                        else:
                            current_state = next_state
                            number_of_audio_received = 0
                            print(f"\n{self.name}: 状態が変更されました。-> {current_state}")

                            setting = ""
                            with open(instructions[current_state]["instruction_path"][self.name], encoding="utf-8") as f:
                                setting = f.read()
                                ins = f"あなたはラジオパーソナリティパーソナリティ「{self.name}」です。{setting}"
                                ins += " また、山梨の温泉情報を取得するのに温泉名をtoolsに与えて呼んでください"
                                settings = {
                                    "modalities": ["audio", "text"],
                                    "instructions": ins,
                                    "voice": "shimmer",
                                    "turn_detection": {
                                        "type": "server_vad",
                                        "threshold": 0.5,
                                    },
                                    "tools": [
                                        {
                                            "type": "function",
                                            "name": "get_hotspring_yamanashi_info",
                                            "description": "山梨の温泉情報を取得します。",
                                            "parameters": {
                                                "type": "object",
                                                "properties": {
                                                    "query": {
                                                        "type": "string",
                                                        "description": "温泉名"
                                                    }
                                                },
                                            },
                                            "required": ["query"]
                                        },
                                    ]
                                }
                                # print("\n", settings)
                            await self.websocket.send(json.dumps(settings))

                    # 応答が完了したら、その応答をTCP/IPで相手のAIに送信する
                    socket_client = socket.socket(socket.AF_INET,
                                                  socket.SOCK_STREAM)
                    socket_client.connect(("localhost", self.dst_port))
                    socket_client.sendall(str.encode(
                        self.active_audio_received))
                    socket_client.close()
                    self.active_audio_received = ""

                # こちらの発話がスタートしたことをサーバが取得したことを確認する
                elif "type" in response_data and response_data["type"] \
                        == "input_audio_buffer.speech_started":
                    # すでに存在する取得したAI発話音声をリセットする
                    while not self.audio_receive_queue.empty():
                        self.audio_receive_queue.get()

                # サーバーからの音声データをキューに格納
                elif "type" in response_data and response_data["type"] \
                        == "response.audio.delta":
                    base64_audio_response = response_data["delta"]
                    if base64_audio_response:
                        pcm16_audio = self.base64_to_pcm16(
                            base64_audio_response
                        )
                        self.audio_receive_queue.put(pcm16_audio)
                
                elif "type" in response_data and response_data["type"] \
                        == "response.output_item.done":
                    if "item" in response_data and "type" in response_data["item"] and \
                            response_data["item"]["type"] == "function_call":
                        
                        print(f"\n{self.name}: output_item.done")
                        print(response_data)

                        arguments = response_data["item"]["arguments"]
                        ret = get_hotspring_yamanashi_info(arguments[0])
                        
                        await self.websocket.send({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": "item.call_id",
                                "output": json.dumps({ret}),
                            },
                        })
                        await self.websocket.send({
                            "type": "response.create",
                        })                        

            await asyncio.sleep(0)

    # サーバーからの音声を再生する関数
    def play_audio_from_queue(self, output_stream):
        if self.is_first_agent:
            while True:
                pcm16_audio = self.audio_receive_queue.get()
                if pcm16_audio:
                    output_stream.write(pcm16_audio)

    # マイクからの音声を取得し、WebSocketで送信しながらサーバーからの音声応答を再生する非同期関数
    async def stream_audio_and_receive_response(self):
        # WebSocketに接続
        async with websockets.connect(
             WS_URL, extra_headers=HEADERS
        ) as websocket:
            print(f"\n{self.name}: WebSocketに接続しました。")
            text_area.children.append(html.P())
            text_area.children.append(html.P(f"{self.name}: WebSocketに接続しました。"))

            update_request = {
                "type": "session.update",
                "session": self.settings
            }
            await websocket.send(json.dumps(update_request))

            self.websocket = websocket

            # PyAudioの設定
            OUTPUT_CHUNK = 2400
            FORMAT = pyaudio.paInt16
            CHANNELS = 1
            OUTPUT_RATE = 24000

            # PyAudioインスタンス
            p = pyaudio.PyAudio()

            # サーバーからの応答音声を再生するためのストリームを初期化
            output_stream = p.open(format=FORMAT, channels=CHANNELS,
                                   rate=OUTPUT_RATE, output=True,
                                   frames_per_buffer=OUTPUT_CHUNK)

            # サーバーからの音声再生をスレッドで開始
            threading.Thread(target=self.play_audio_from_queue,
                             args=(output_stream,),
                             daemon=True).start()

            try:
                # 音声受信タスクを非同期で実行
                receive_task = asyncio.create_task(
                                self.receive_audio_to_queue())

                if (self.is_first_agent):
                    # 初回会話開始のトリガを送信する
                    trigger_event = {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "ラジオを開始してください。"
                                }
                            ]
                        }
                    }
                    await websocket.send(json.dumps(trigger_event))
                    await websocket.send(json.dumps(
                        {"type": "response.create"}
                    ))

                # タスクが終了するまで待機
                await asyncio.gather(receive_task)

            except KeyboardInterrupt:
                print("終了します...")
            except Exception as e:
                print(f"\n{self.name}: エラーが発生しました: {e}")
                # スタックトレースを表示
                import traceback
                traceback.print_exc()
            finally:
                print(f"\n{self.name}: WebSocketを閉じます。")
                output_stream.stop_stream()
                output_stream.close()
                p.terminate()


def get_hotspring_yamanashi_info(hotspring_name: str):
    data = \
    {
        "ほったらかし温泉" : "山梨県北杜市にある温泉です。",
        "石和温泉" : "山梨県甲州市にある温泉です。",
    }
    if hotspring_name in data:
        return data[hotspring_name]
    else:
        return "その温泉情報はありません。"


async def main():

    try:
        # やまがたのプロンプトを読み込む
        agent1_instructions = ""
        with open(instructions["initial"]["instruction_path"]["やまがた"], "r", encoding="utf-8") as f:
            agent1_instructions = f.read()

        ins = f"あなたはラジオパーソナリティパーソナリティ「やまがた」です。{agent1_instructions}"
        ins += " また、山梨の温泉情報を取得するのに温泉名をtoolsに与えて呼んでください"

        agent1_settings = {
                        "modalities": ["audio", "text"],
                        "instructions": ins,
                        "voice": "alloy",
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                        },
                        "tools": [
                            {
                                "type": "function",
                                "name": "get_hotspring_yamanashi_info",
                                "description": "山梨の温泉情報を取得します。",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "query": {
                                            "type": "string",
                                            "description": "温泉名"
                                        }
                                    },
                                }
                            }
                        ],
                        "tool_choice": "auto"
                    }
        
        agent1 = RealTimeAgent(
            WS_URL, HEADERS, agent1_settings, "やまがた",
            15000, 15001, is_first_agent=True, instructions=instructions)
        
        # まさかげのプロンプトを読み込む    
        agent2_instructions = ""
        with open(instructions["initial"]["instruction_path"]["まさかげ"], "r", encoding="utf-8") as f:
            agent2_instructions = f.read()

        ins = f"あなたはラジオパーソナリティパーソナリティ「まさかげ」です。{agent2_instructions}"
        ins += " また、山梨の温泉情報を取得するのに温泉名をtoolsに与えて呼んでください"

        agent2_settings = {
                        "modalities": ["audio", "text"],
                        "instructions": ins,
                        "voice": "shimmer",
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                        },
                        "tools": [
                            {
                                "type": "function",
                                "name": "get_hotspring_yamanashi_info",
                                "description": "山梨の温泉情報を取得します。",
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "query": {
                                            "type": "string",
                                            "description": "温泉名"
                                        }
                                    },
                                },"required": ["query"]
                            }
                        ]
                    }
        
        agent2 = RealTimeAgent(
            WS_URL, HEADERS, agent2_settings, "まさかげ",
            15001, 15000, is_first_agent=False, instructions=instructions)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(agent1.stream_audio_and_receive_response())
            tg.create_task(agent1.receive_from_ai())
            tg.create_task(agent2.stream_audio_and_receive_response())
            tg.create_task(agent2.receive_from_ai())

    except Exception as e:
        import traceback
        text_area.children.append(html.P(str(e)))
        text_area.children.append(html.P(traceback.format_stack()))


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


def run():
    global is_started
    while not is_started:
        time.sleep(1)
    asyncio.run(main())

if __name__ == "__main__":

    threading.Thread(target=run).start()
    
    # print ("Server is running...")
    app.run()    



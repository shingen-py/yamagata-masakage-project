
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

                    # 応答が完了したら、その応答をTCP/IPで相手のAIに送信する
                    socket_client = socket.socket(socket.AF_INET,
                                                  socket.SOCK_STREAM)
                    socket_client.connect(("localhost", self.dst_port))
                    socket_client.sendall(str.encode(
                        self.active_audio_received))
                    socket_client.close()
                    self.active_audio_received = ""

                # こちらの発話がスタートしたことをサーバが取得したことを確認する
                if "type" in response_data and response_data["type"] \
                        == "input_audio_buffer.speech_started":
                    # すでに存在する取得したAI発話音声をリセットする
                    while not self.audio_receive_queue.empty():
                        self.audio_receive_queue.get()

                # サーバーからの音声データをキューに格納
                if "type" in response_data and response_data["type"] \
                        == "response.audio.delta":
                    base64_audio_response = response_data["delta"]
                    if base64_audio_response:
                        pcm16_audio = self.base64_to_pcm16(
                            base64_audio_response
                        )
                        self.audio_receive_queue.put(pcm16_audio)

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


async def main():
    prompt = """
    ラジオ番組「やまがた まさかげの温泉ラジオ」のパーソナリティ「やまがた」と「まさかげ」として進行してください。

    主に「やまがた」が5000文字程度、山梨の温泉情報を語ります。その内容には温泉そのものの魅力だけでなく、
    周辺環境、文化、歴史、アクセス、食事、おすすめの楽しみ方も含めます。最後に「やまがた」が「まさかげ」に話を振る形で終わります。

    トークの内容は以下の条件を満たしてください：
    - ユーザーが提供する特定の温泉情報をもとに構築する。
    - 「やまがた」の話し方は熱意があり、親しみやすい語り口調を採用する。
    - 温泉に関する詳細情報や魅力的なエピソードを織り交ぜる。
    - 「まさかげ」は最後に登場し、次の展開を促す形で話を受け取る。

    # Steps

    1. **イントロダクション**
    「やまがた」がリスナーに温泉トピックを親しみやすく紹介。山梨の魅力や今回取り上げる温泉の期待感を高める。

    2. **メインセグメント**
    - 「やまがた」が以下の内容を盛り込んで語る：
        - 温泉の基本情報（所在地、歴史、泉質、効能）。
        - 温泉の特長やユニークな点。
        - 周辺地域の見どころや観光スポット。
        - 季節ごとの楽しみ方やイベント。
        - 温泉の利用方法やマナー。
    - 情景描写や具体的なエピソードを交えながら、熱意を持って語る。

    3. **まさかげへの話振りで終わる**
    「やまがた」が最後に「まさかげ」に話を振り、次の展開を期待させる形で締める。

    # Output Format

    - **語り形式**：一貫して「やまがた」が語り、最後に「まさかげ」に話を振る形とする。
    - **自然な話し言葉**：ラジオ放送中の口調を模倣し、リスナーに語りかける形を保つ。
    - **段落構成**：読みやすくリズムのある段落で構成する。

    # Examples

    ---

    みなさん、こんにちは！「やまがた まさかげの温泉ラジオ」へようこそ！今日も元気いっぱいで
    お届けしますよ～！さて、本日はですね、山梨の隠れた名湯をご紹介します。その名も「[温泉名]」。ここは歴史ある温泉で、実は…（ここから200文字程度でトークを展開）

    …さてさて、こんな感じで語ってきたけど、まさかげさんはどう思う？

    ---

    （例は短縮版です。実際のトークはこれを拡大し、詳細な情報を加えて構成してください。）

    # Notes

    - **語りの一貫性**：「やまがた」が話を進行し、「まさかげ」に話を振るタイミングは一番最後とする。
    - **テンションの維持**：熱意と親しみやすさを持ち、リスナーを飽きさせない。
    - **ユーザー提供情報の活用**：温泉情報を深掘りし、それを軸に周辺トピックを広げる
    """

    agent1_settings = {
                    "modalities": ["audio", "text"],
                    "instructions": f"あなたはラジオパーソナリティパーソナリティ「やまがた」です。{prompt}",
                    "voice": "alloy",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                    }
                }

    agent2_settings = {
                    "modalities": ["audio", "text"],
                    "instructions":  f"あなたはラジオパーソナリティパーソナリティ「まさかげ」です。{prompt}",
                    "voice": "shimmer",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                    }
                }
    
    try:
        agent1 = RealTimeAgent(
            WS_URL, HEADERS, agent1_settings, "やまがた",
            15000, 15001, is_first_agent=True)
        agent2 = RealTimeAgent(
            WS_URL, HEADERS, agent2_settings, "まさかげ",
            15001, 15000, is_first_agent=False)

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




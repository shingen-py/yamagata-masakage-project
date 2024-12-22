import asyncio
import websockets
import base64
import json
import socket
import threading
from queue import Queue
import pyaudio
from dash import html


class RealTimeAgent:
    """
    WebSocketを利用してOpenAIの音声応答をやり取りするためのクラス
    音声データの送受信やステートマシンに基づく状態遷移などを行う。
    """

    def __init__(
        self,
        ws_url: str,
        headers: dict,
        settings: dict,
        name: str,
        port: int,
        dst_port: int,
        is_first_agent: bool,
        instructions: dict,
        text_area,  # main.py で定義したDashコンポーネントを受け取り
    ):
        """
        コンストラクタで必要な情報を受け取る
        """
        self.ws_url = ws_url
        self.headers = headers
        self.settings = settings
        self.name = name
        self.port = port
        self.dst_port = dst_port
        self.is_first_agent = is_first_agent
        self.instructions = instructions
        self.text_area = text_area  # 参照を保持する
        self.audio_receive_queue = Queue()
        self.active_audio_received = ""
        self.websocket = None

    def base64_to_pcm16(self, base64_audio: str) -> bytes:
        """
        Base64文字列をPCM16のバイナリデータにデコードする
        """
        audio_data = base64.b64decode(base64_audio)
        return audio_data

    async def receive_from_ai(self):
        """
        他のAIからTCPで送られてきたメッセージを受信し、
        OpenAIのWebSocketへ送信する
        """
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

                    size = round(len(recv_data) / 1024, 1)
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

                    await asyncio.sleep(1)
            except Exception:
                pass
            finally:
                await asyncio.sleep(3)

    async def receive_audio_to_queue(self):
        """
        サーバー(WebSocket)からの音声データやテキストレスポンスを受信し、
        テキストエリアへの表示や音声データのキュー格納を行う
        """
        number_of_audio_received = 0
        current_state = "initial"
        current_message = ""

        while True:
            response = await self.websocket.recv()
            if response:
                response_data = json.loads(response)

                # テキストが届いた場合に出力
                if (response_data.get("type") == "response.audio_transcript.delta"):
                    if self.active_audio_received == "":
                        print(f"\n{self.name}: ", end="", flush=True)

                    print(response_data["delta"], end="", flush=True)
                    self.active_audio_received += response_data["delta"]

                    # Dashの表示にも反映
                    if current_message == "":
                        current_message += f"{self.name}: {response_data['delta']}"
                        self.text_area.children.append(
                            html.P(f"{self.name}: {current_message}")
                        )
                    else:
                        current_message += response_data["delta"]
                        self.text_area.children[-1] = html.P(current_message)

                # テキスト応答が完了した場合
                elif (response_data.get("type") == "response.audio_transcript.done"):
                    print("\n", end="", flush=True)
                    current_message = ""

                    number_of_audio_received += 1
                    # 状態遷移
                    if number_of_audio_received >= self.instructions[current_state]["number_of_executions"]:
                        next_state = self.instructions[current_state]["next_state"]
                        if next_state != "end":
                            current_state = next_state
                            number_of_audio_received = 0
                            print(f"\n{self.name}: 状態が変更されました。-> {current_state}")

                            # 次ステートのプロンプトを送信
                            setting = ""
                            with open(
                                self.instructions[current_state]["instruction_path"][self.name],
                                encoding="utf-8"
                            ) as f:
                                setting = f.read()

                            settings = {
                                "modalities": ["audio", "text"],
                                "instructions": f"あなたはラジオパーソナリティパーソナリティ「{self.name}」です。{setting}",
                                "voice": "shimmer",
                                "turn_detection": {
                                    "type": "server_vad",
                                    "threshold": 0.5,
                                }
                            }
                            await self.websocket.send(json.dumps(settings))

                    # 応答完了後、TCPで次のAgentへ音声内容を送る
                    socket_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    socket_client.connect(("localhost", self.dst_port))
                    socket_client.sendall(str.encode(self.active_audio_received))
                    socket_client.close()
                    self.active_audio_received = ""

                # こちらの発話開始をサーバーが取得した合図
                if response_data.get("type") == "input_audio_buffer.speech_started":
                    while not self.audio_receive_queue.empty():
                        self.audio_receive_queue.get()

                # 音声データが届いた場合にキューに格納
                if response_data.get("type") == "response.audio.delta":
                    base64_audio_response = response_data["delta"]
                    if base64_audio_response:
                        pcm16_audio = self.base64_to_pcm16(base64_audio_response)
                        self.audio_receive_queue.put(pcm16_audio)

            await asyncio.sleep(0)

    def play_audio_from_queue(self, output_stream):
        """
        キューにある音声データを取り出し、リアルタイムに出力ストリームに書き込む
        """
        if self.is_first_agent:
            while True:
                pcm16_audio = self.audio_receive_queue.get()
                if pcm16_audio:
                    output_stream.write(pcm16_audio)

    async def stream_audio_and_receive_response(self):
        """
        WebSocketに接続し、音声受信＆送信処理を実行するメインループ
        """
        try:
            async with websockets.connect(self.ws_url, extra_headers=self.headers) as websocket:
                print(f"\n{self.name}: WebSocketに接続しました。")
                self.text_area.children.append(html.P())
                self.text_area.children.append(
                    html.P(f"{self.name}: WebSocketに接続しました。")
                )

                # セッション初期設定をサーバーに送信
                update_request = {
                    "type": "session.update",
                    "session": self.settings
                }
                await websocket.send(json.dumps(update_request))

                self.websocket = websocket

                # PyAudio設定
                OUTPUT_CHUNK = 2400
                FORMAT = pyaudio.paInt16
                CHANNELS = 1
                OUTPUT_RATE = 24000
                p = pyaudio.PyAudio()

                # 再生用ストリーム
                output_stream = p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=OUTPUT_RATE,
                    output=True,
                    frames_per_buffer=OUTPUT_CHUNK
                )

                # 音声再生スレッド起動
                threading.Thread(
                    target=self.play_audio_from_queue,
                    args=(output_stream,),
                    daemon=True
                ).start()

                # AIからの応答を受け取るタスク
                receive_task = asyncio.create_task(self.receive_audio_to_queue())

                # is_first_agentの場合、ラジオ開始トリガを送信
                if self.is_first_agent:
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
                    await websocket.send(json.dumps({"type": "response.create"}))

                # タスクが終了するまで待機
                await asyncio.gather(receive_task)

                # 終了処理
                print(f"\n{self.name}: WebSocketを閉じます。")
                output_stream.stop_stream()
                output_stream.close()
                p.terminate()

        except KeyboardInterrupt:
            print("終了します...")

        except Exception as e:
            print(f"\n{self.name}: エラーが発生しました: {e}")
            import traceback
            traceback.print_exc()

# yamagata-masakage-project

## ローカル実行手順

### リポジトリをダウンロード

```
git clone https://github.com/shingen-py/yamagata-masakage-project.git
```

### api.env に以下の内容を保存

```
OPENAI_API_KEY=<< OpenAI API Key >>
```

### 必要なパッケージをインストール

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### アプリを実行

```
python main.py
```
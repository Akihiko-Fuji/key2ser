# key2ser

Keyboard to Virtual Serial port for Raspberry Pi OS

## 概要

Raspberry Pi OS で HID デバイス（バーコードリーダーなど）を特定の VID/PID に限定し、取得したキー入力を仮想シリアルポートへ送信します。

## モジュール構成

- `key2ser/__init__.py`: パッケージの公開モジュールを定義します。
- `key2ser/cli.py`: CLI引数の解析と実行エントリポイントを担います。
- `key2ser/config.py`: `config.ini` を読み込み、設定値を検証してデータクラスにまとめます。
- `key2ser/keymap.py`: キーコードと送信文字のマッピング（英数/かな）を管理します。
- `key2ser/runner.py`: evdevのイベントループを起動し、キー入力をシリアルへ送信します。

## 動作環境

- Linux (evdev に対応している環境)
  - 動作確認: Raspberry Pi OS
  - そのほかの Debian/Ubuntu などの Linux でも、`/dev/input/event*` を利用できる環境であれば動作します。
- Windows/macOS は非対応です。

## なにが悲しくてこのようなものをつくらないといけなかったのか
Bluetoothのシリアル通信 (SPP) デバイスを使用する際、現行のBlueZはSPPプロトコルを標準でサポートしていません。そのため、設定を変更してもSPPデバイスが適切に動作しない場合があります。
加えて、多くのBluetoothでシリアル通信をおこなうハードウェアはLinux上のSPP接続サポートせず、HIDデバイスとしての接続のみサポートしている状況です。

本ツールは、この問題を回避するため、まずデバイスをHIDデバイスとしてペアリングし、そのHID入力を仮想シリアルポートに転送することでシリアル通信を実現することを目的としています。
主な用途として、1次元・2次元バーコードリーダーでの利用を想定しています。機器のレジュームでデバイスが失われた後に、復帰処理を追加しました。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## GitHub からのインストール

```bash
git clone https://github.com/Akihiko-Fuji/key2ser.git
cd key2ser
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 仮想シリアルポートの作成例
事前に `sudo apt install socat`してください。

```bash
sudo socat -d -d pty,raw,echo=0,link=/dev/ttyV0,mode=660,group=dialout \
  pty,raw,echo=0,link=/dev/ttyV1,mode=660,group=dialout
```

送信側が `/dev/ttyV0`、受信側が `/dev/ttyV1` になります。

#### これまでの流れ（仮想TTY作成〜実行）

1. 仮想シリアルポートを作成します。
   ```bash
   sudo socat -d -d pty,raw,echo=0,link=/dev/ttyV0,mode=660,group=dialout \
     pty,raw,echo=0,link=/dev/ttyV1,mode=660,group=dialout
   ```
2. 必要に応じて実行ユーザーを `dialout` グループへ追加します。
   ```bash
   sudo usermod -aG dialout pi
   ```
   反映には再ログインが必要です。
3. `config.ini` の `serial.port` を `/dev/ttyV0` に設定し、起動します。
   ```bash
   python3 key2ser.py --config config.ini
   ```

#### ttyV0 への送信確認コマンド

以下のコマンドで `ttyV0` に送った内容が `ttyV1` に届くか確認できます。

```bash
cat /dev/ttyV1
```

別ターミナルで次を実行します。

```bash
echo -n "TEST" > /dev/ttyV0
```

`cat /dev/ttyV1` 側に `TEST` が表示されれば、仮想TTYのリダイレクトが正しく動作しています。


## 設定ファイル

`config.ini` を編集して、入力デバイスと送信先を指定します。

```ini
[input]
mode=evdev
# device=/dev/input/event3
vendor_id=0x1234
product_id=0xabcd
# grab=true
reconnect_interval_seconds=3

[serial]
port=/dev/ttyV0
baudrate=9600
timeout=1
bytesize=8
parity=none
stopbits=1
xonxoff=false
rtscts=false
dsrdtr=false
emulate_modem_signals=false
emulate_timing=false

[output]
encoding=utf-8
line_end_mode=escape
line_end=\r\n
send_on_enter=true
send_mode=on_enter
idle_timeout_seconds=0.5
dedup_window_seconds=0.2
```

- `vendor_id` と `product_id` を両方指定すると該当デバイスのみを使用します。
- `device` を指定すると特定の `/dev/input/event*` を優先します。
- `reconnect_interval_seconds` は入力デバイスやシリアルの読み取りに失敗した際に再接続を試みる間隔（秒）です。0 にすると再試行しません。
- `bytesize` はデータビット長（5/6/7/8）を指定します。
- `parity` はパリティビット（none/odd/even/mark/space）を指定します。
- `stopbits` はストップビット（1/1.5/2）を指定します。
- `xonxoff` はソフトウェアフロー制御の有無を指定します。
- `rtscts` はRTS/CTSのハードウェアフロー制御を有効にします。
- `dsrdtr` はDSR/DTRのハードウェアフロー制御を有効にします。
- `emulate_modem_signals` はDTR/RTSを明示的にONにして仮想ポートでもハードウェアらしく振る舞わせます。
- `emulate_timing` は仮想TTYで実際の通信速度が再現されない場合に、設定された通信パラメータに合わせて送信間隔を調整します。
- `send_mode` は送信タイミングを指定します。
  - `on_enter`: Enter を受信したタイミングでバッファを送信します（バーコードリーダー向けの既定値）。Enter キー自体は送信せず、末尾には `line_end` を付与します。
  - `per_char`: 入力された文字を都度送信します。
  - `idle_timeout`: 入力が止まってから `idle_timeout_seconds` 秒経過したら送信します。
- `line_end_mode` は `line_end` の解釈方法を指定します。
  - `literal`: そのまま送信します。
  - `escape`: `\r` や `\n` といったエスケープシーケンスを実際の改行として解釈します。
- `send_on_enter` は `send_mode=on_enter` のときのみ有効で、Enter のみが入力された場合でも空文字を送信するかどうかを指定します。
- `idle_timeout_seconds` は `send_mode=idle_timeout` のときに使用する待機時間（秒）です。
- `dedup_window_seconds` は直近の送信と同じ内容が連続した場合に抑止する時間（秒）です。0 を指定すると抑止しません。
- `mode=evdev` は、Linux の evdev（`/dev/input/event*`）経由で入力イベントを読む方式を指定しています。値は evdev を前提にしており、現時点で他の値を想定していません。

## 実行方法

```bash
python3 key2ser.py --config config.ini
```

## 起動時に常駐する方法（systemd）

1. サービスファイルを作成します。

```bash
sudo tee /etc/systemd/system/key2ser.service > /dev/null <<'EOF'
[Unit]
Description=key2ser HID to Serial bridge
After=network.target

[Service]
Type=simple
User=pi
Group=input
WorkingDirectory=/path/to/key2ser
ExecStart=/path/to/key2ser/.venv/bin/python /path/to/key2ser/key2ser.py --config /path/to/key2ser/config.ini
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
```

2. 反映して起動します。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now key2ser.service
```

3. 設定変更後は再起動で反映します。

```bash
sudo systemctl restart key2ser.service
```

常駐を解除（無効化）する場合は次を実行します。

```bash
sudo systemctl disable --now key2ser.service
```

サービスファイル自体も削除する場合は次を実行します。

```bash
sudo rm /etc/systemd/system/key2ser.service
sudo systemctl daemon-reload
```

ログ確認は次で行えます。

```bash
journalctl -u key2ser.service -f
```


## 権限

`/dev/input/event*` へアクセスするため、`input` グループへユーザーを追加するか、root で実行してください。

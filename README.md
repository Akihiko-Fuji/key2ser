# key2ser

Keyboard to Virtual Serial port for Raspberry Pi OS

## 概要

Raspberry Pi OS で HID デバイス（バーコードリーダーなど）を特定の VID/PID に限定し、取得したキー入力を仮想シリアルポートへ送信します。

## なにが悲しくてこのようなものをつくらないといけなかったのか
Bluetoothのシリアル通信(spp)デバイスを利用する際に、現行のbluezがsppプロトコルを標準状態でサポートがありません。設定の変更をおこなっても、適切にsppデバイスが動作しないことがあることが原因です。
一旦、HIDデバイスとしてペアリングをおこなったうえで、このツールを利用して、HID入力を仮想シリアルポートに転送し、それでシリアル通信をおこなうというのが目的です。
主に１次元、２次元バーコードリーダーでの利用を想定しています。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 仮想シリアルポートの作成例

```bash
socat -d -d pty,raw,echo=0,link=/dev/ttyV0 pty,raw,echo=0,link=/dev/ttyV1
```

送信側が `/dev/ttyV0`、受信側が `/dev/ttyV1` になります。

## 設定ファイル

`config.ini` を編集して、入力デバイスと送信先を指定します。

```ini
[input]
mode=evdev
# device=/dev/input/event3
vendor_id=0x1234
product_id=0xabcd
# grab=true

[serial]
port=/dev/ttyV0
baudrate=9600
timeout=1

[output]
encoding=utf-8
line_end=\r\n
send_on_enter=true
send_mode=on_enter
idle_timeout_seconds=0.5
```

- `vendor_id` と `product_id` を両方指定すると該当デバイスのみを使用します。
- `device` を指定すると特定の `/dev/input/event*` を優先します。

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

ログ確認は次で行えます。

```bash
journalctl -u key2ser.service -f
```


## 権限

`/dev/input/event*` へアクセスするため、`input` グループへユーザーを追加するか、root で実行してください。

# LINE → local Codex bridge

LINE Messaging APIのWebhookを受け、同じRaspberry Pi上の `codex exec` に指示を渡す、依存パッケージなしのPythonサービスです。

```text
LINE ──Webhook──▶ Raspberry Pi bridge ──stdin──▶ codex exec
  ▲                                                    │
  └──────────────── LINE Push API ◀────────────────────┘
```

## 安全上の既定値

- LINE署名を検証します。
- `LINE_ALLOWED_USER_IDS` に登録したユーザーだけ実行できます。空の許可リストでは起動しません。
- 既定では1対1トークだけを受け付けます。グループ／ルームは明示的な設定なしでは実行できません。
- Codexジョブは同時に1件だけ実行します。
- Codexは固定された `CODEX_WORKDIR` で `workspace-write` サンドボックスを使います。
- LINEの秘密情報をCodex子プロセスの環境変数に渡しません。
- `--dangerously-bypass-approvals-and-sandbox` は使用しません。

それでもCodexは `CODEX_WORKDIR` 内のファイルを変更できます。重要なリポジトリでは、専用ユーザー、バックアップ、Git、コンテナ等を併用してください。

このサービスをインターネットへ直接公開せず、TLSを終端するリバースプロキシまたはトンネルの背後に置いてください。

## 1. ローカル確認

```bash
codex --version
codex exec --ephemeral --sandbox read-only --skip-git-repo-check \
  -C /path/to/workspace 'このディレクトリを一文で説明して'
```

サービスを実行するLinuxユーザーでCodexへのログインが済んでいる必要があります。

## 2. 環境設定

実値をリポジトリ内の `.env` に保存しないでください。systemdを使う場合は、rootだけが読める `/etc/codex-line.env` を作ります。

```bash
sudo install -m 600 -o root -g root /dev/null /etc/codex-line.env
sudoedit /etc/codex-line.env
```

内容は [.env.example](.env.example) を参照してください。`LINE_ALLOWED_USER_IDS` はカンマ区切りです。グループで使う場合も、送信者の `userId` が許可リストに必要です。

## 3. 手動起動とテスト

```bash
# 実値はシェル履歴に残さない方法で一時的に設定してください。
export LINE_CHANNEL_SECRET='...'
export LINE_CHANNEL_ACCESS_TOKEN='...'
export LINE_ALLOWED_USER_IDS='U...'
export CODEX_WORKDIR='/path/to/workspace'
export CODEX_BIN='/home/YOUR_USER/.local/bin/codex'
# グループ利用が本当に必要な場合だけ true にします。
export ALLOW_GROUPS='false'
python3 -m codex_line.server
```

`/etc/codex-line.env` をroot専用の権限にした後は、手動で `source` せずsystemdから読み込ませます。

別のターミナルから:

```bash
curl http://127.0.0.1:8080/healthz
python3 -m unittest discover -v
```

## 4. HTTPS公開

LINE Developers ConsoleのWebhook URLには、公開HTTPS URLを指定します。

```text
https://YOUR_HOST/webhook
```

このPythonサーバーは既定で `127.0.0.1:8080` のHTTPだけを待ち受けます。Caddy、nginx、Cloudflare Tunnelなど、利用中のHTTPS入口からそこへ転送してください。TLSをこのプロセスへ直接持たせない構成を推奨します。

## 5. systemd

`deploy/codex-line.service` の以下を実機に合わせて変更します。

- `User` / `Group`: Codexにログイン済みのユーザー
- `WorkingDirectory`: このリポジトリの配置先
- `ReadWritePaths`: `CODEX_WORKDIR` と同じパス
- `CODEX_BIN`: `/etc/codex-line.env` 側の実際のCodexパス

その後:

```bash
sudo cp deploy/codex-line.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now codex-line
sudo systemctl status codex-line
journalctl -u codex-line -f
```

## 動作

1. LINEへテキストを送信
2. Botが「受け付けました」と即時返信
3. `codex exec` が固定ワークスペースで実行
4. 完了結果をLINE Pushメッセージで通知

各LINEメッセージは独立した一回のCodex実行です。会話履歴の継続、承認フロー、複数リポジトリ切替は初版には含めていません。

## Optional: domain-free Quick Tunnel

`cloudflared` のQuick Tunnelを使うと、独自ドメインなしで一時的な公開HTTPS URLを取得できます。`codex_line.quick_tunnel` はURLが変わるたびにLINE Webhookを自動更新します。

```bash
python3 -m codex_line.quick_tunnel
```

常駐例は `deploy/codex-line-quick-tunnel.service` を参照してください。Quick TunnelはCloudflare公式でも開発・テスト用途とされ、URLはプロセス再起動ごとに変わり、稼働率保証はありません。

## License

[MIT License](LICENSE)

## Security

脆弱性を見つけた場合は公開Issueに秘密情報を記載せず、GitHubのPrivate vulnerability reportingを利用してください。詳細は [SECURITY.md](SECURITY.md) を参照してください。

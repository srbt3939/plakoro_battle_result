# plakoro_battle_result

## Discord Bot

依存関係をインストールします。

```bash
python -m pip install -r requirements.txt
```

`.env.example` を参考に環境変数を設定して起動します。

```bash
export DISCORD_BOT_TOKEN="Botトークン"
python discord_bot.py
```

Bot の Discord Developer Portal で **Message Content Intent** を有効にしてください。
戦績メッセージの投稿者を Player 1 とし、本文でメンションされた最初のユーザーを Player 2 として登録します。メンションを使わない場合は `DEFAULT_PLAYER2_ID` を設定してください。

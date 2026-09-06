# plakoro_battle_result

# ポケモンプラコロ 対戦戦績管理

ポケモンプラコロの対戦結果を記録・集計するためのWebアプリケーションです。

Discord上で報告された対戦結果をもとに、ポケモンごとの使用率・勝率・対戦相性などを確認できるようにすることを目的としています。

## 概要

主な機能：

- 対戦メッセージの登録
- 対戦結果の登録
- ポケモン情報の管理
- プレイヤー情報の管理
- ポケモンごとの使用率・勝率の集計
- ポケモン同士の対戦成績の集計
- 先攻・後攻別の成績集計
- GitHub Pagesを利用した戦績公開

将来的な機能：
- Discord Botとの連携

## システム構成

```text
Discord

## ディレクトリ構成

```text
app/
├── battle_parser.py   # Discordメッセージ解析・登録
├── discord_bot.py     # Discord Bot
├── insert_app.py      # 対戦結果登録画面
├── result.py          # 集計確認画面
└── templates/         # Flaskテンプレート
data/
├── pokemon.db         # 運用データベース
└── pokemon_backup.db  # DBバックアップ
scripts/
└── output_result.py   # GitHub Pages用JSON生成
docs/                  # GitHub Pages公開ファイル
```

## 実行

```bash
# 対戦結果登録画面
python app/insert_app.py

# 集計確認画面
python app/result.py

# GitHub Pages用データ生成
python scripts/output_result.py

# Discord Bot
python -m app.discord_bot
```
   │
   │ 対戦結果
   ▼
Discord Bot（現在は手動）
   │
   ▼
SQLite
(pokemon.db)
   │
   ├── players
   ├── pokemon
   ├── battles
   └── battle_messages
   │
   ▼
JSONデータ生成
   │
   ▼
GitHub Pages
   │
   ▼
戦績確認サイト

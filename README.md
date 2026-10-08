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
   │ 対戦結果
   ▼
Discord Bot（現在は手動）
   │
   ▼
SQLite (data/pokemon.db)
   │
   ▼
JSONデータ生成 (scripts/output_result.py)
   │
   ▼
GitHub Pages (docs/)
```

## ディレクトリ構成

```text
app/
├── battle_parser.py
├── discord_bot.py
├── insert_app.py
└── templates/
      ├── battle_form.html
      ├── insert_index.html
      ├── message_form.html
      └── player_form.html
data/
└── pokemon.db
scripts/
└── output_result.py
docs/
├── index.html                 # GitHub Pagesのトップ
├── recent_battles.html
├── input_form.html
├── assets/
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   ├── app.js
│   │   ├── recent_battles.js
│   │   ├── characters/
│   │   │   └── character.js
│   │   └── players/
│   │       ├── player_list.js
│   │       └── player_detail.js
│   └── images/
│       ├── emoji_images/
│       └── type_images/
├── data/                      # 集計JSONとemoji.json
├── characters/                # キャラクター共通の対戦詳細ページ
└── players/                   # プレイヤー別ページ
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

# plakoro_battle_result

# ポケモンプラコロ 対戦戦績管理

ポケモンプラコロの対戦結果を記録・集計するためのWebアプリケーションです。

Discord上で報告された対戦結果をもとに、ポケモンごとの使用率・勝率・対戦相性などを確認できるようにすることを目的としています。

## 概要

主な機能：

- 対戦メッセージの登録
- 対戦結果の登録
- メッセージ本文の絵文字から対戦結果をフォームへ仮入力
- ポケモン情報の管理
- プレイヤー情報の管理
- ポケモンごとの使用率・勝率の集計
- ポケモン同士の対戦成績の集計
- 先攻・後攻別の成績集計
- 通常対戦・バトルチャレンジ別の戦績集計
- GitHub Pagesを利用した戦績公開

Discord Botは通常対戦を自動登録し、バトルチャレンジの投稿は元メッセージだけを保存します。大会対戦は登録画面でシーズンとゲーム結果を確認してから登録します。

## システム構成

```
Discord
   │ 対戦結果
   ▼
Discord Bot / 管理画面
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
├── battle_matches.py
├── discord_bot.py
├── insert_app.py
└── templates/
      ├── battle_form.html
      ├── battle_challenge_form.html
      ├── insert_index.html
      ├── message_form.html
      └── player_form.html
data/
└── pokemon.db
scripts/
├── migrate_battle_challenges.py
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
# 既存DBをバックアップしてからBO3対応スキーマへ移行（初回のみ）
python scripts/migrate_battle_challenges.py

# 対戦結果登録画面
python app/insert_app.py

# 集計確認画面
python app/result.py

# GitHub Pages用データ生成
python scripts/output_result.py

# Discord Bot
python -m app.discord_bot
```

対戦メッセージを登録すると、戦績登録画面へ移動し、本文の絵文字から読み取れた対戦結果が入力欄に仮入力されます。登録済みのメッセージも戦績登録画面で選び直して仮入力できます。読み取り結果を確認・修正してから登録してください。

通常対戦の投稿は従来どおり自動登録されます。対戦枠の見出し「【通常対戦】」「【バトルチャレンジ】」は従来の登録画面で初期値に反映されます。通常の統計は既存どおり両方の対戦枠を合算し、プレイヤー詳細では対戦枠を切り替えて確認できます。

「バトルチャレンジ」「バトチャレ」「バトチャレンジ」を含むメッセージは大会対戦として扱います。Discord Botまたは管理画面でメッセージを保存した後、管理画面の「バトルチャレンジ（BO3）登録」からシーズンを選択し、最大3ゲームを登録してください。大会名・対戦相手は任意です。大会シリーズは通常対戦と分けてJSON出力され、直近対戦履歴で切り替えて表示できます。

BO3対応DBへの移行は既存レコードを保ったまま実施し、実行前に `data/pokemon.db.backup-<UTC日時>` を作成します。各ゲームは既存の `battles.json`、使用率・ポケモン戦績・対戦相性・試合数の集計にも含めます。シリーズ情報は `battle_matches.json`、大会専用および対戦区分別の集計は `battle_challenge_stats.json` にも出力します。プレイヤー別に表示するため、大会対戦登録時に投稿者を選択してください。

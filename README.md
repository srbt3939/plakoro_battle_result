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

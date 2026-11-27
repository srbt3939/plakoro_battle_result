"""Generate anonymized SQLite fixture and golden JSONs for comparison tests (手順6 §6).

Strictly strips all real player names, Discord user IDs, Discord message IDs,
opponent names, tournament comments, and raw message prose before writing to
``tests/fixtures/``.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.battle_matches import is_battle_challenge_message  # noqa: E402
from app.battle_parser import CUSTOM_EMOJI_PATTERN  # noqa: E402
from scripts import output_result  # noqa: E402

FIXTURES_DIR = BASE_DIR / "tests" / "fixtures"
ANONYMIZED_DB_PATH = FIXTURES_DIR / "anonymized.db"
GOLDEN_DIR = FIXTURES_DIR / "golden"
FIXED_TODAY = date(2026, 11, 26)


def sanitize_raw_content(raw_content: str | None) -> str:
    """Keep ONLY custom emojis and synthetic challenge/normal headers; drop all prose/names."""
    if not raw_content:
        return ""
    is_challenge = is_battle_challenge_message(raw_content)
    lines_out: list[str] = []
    if is_challenge:
        lines_out.append("バトルチャレンジ")
    for line in raw_content.splitlines():
        stripped = line.strip()
        if stripped in ("【バトルチャレンジ】", "バトルチャレンジ", "【通常対戦】", "通常対戦"):
            lines_out.append(stripped)
            continue
        emojis = [m.group(0) for m in CUSTOM_EMOJI_PATTERN.finditer(line)]
        if emojis:
            lines_out.append(" ".join(emojis))
    if not lines_out:
        lines_out.append("バトルチャレンジ" if is_challenge else "匿名対戦報告")
    res = "\n".join(lines_out)
    assert is_battle_challenge_message(res) == is_challenge
    return res


def build_anonymized_db(source_db: Path = BASE_DIR / "data" / "pokemon.db") -> Path:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    if ANONYMIZED_DB_PATH.exists():
        ANONYMIZED_DB_PATH.unlink()
    shutil.copyfile(source_db, ANONYMIZED_DB_PATH)

    with sqlite3.connect(ANONYMIZED_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        players = conn.execute("SELECT id, created_at FROM players ORDER BY id").fetchall()
        for idx, p in enumerate(players, start=1):
            pid = p["id"]
            syn_name = f"プレイヤー{idx:02d}"
            syn_discord_id = f"90000000000000{idx:04d}"
            conn.execute(
                "UPDATE players SET name = ?, discord_user_id = ? WHERE id = ?",
                (syn_name, syn_discord_id, pid),
            )

        messages = conn.execute("SELECT id, raw_content FROM battle_messages ORDER BY id").fetchall()
        for idx, m in enumerate(messages, start=1):
            mid = m["id"]
            syn_msg_id = f"80000000000000{idx:04d}"
            clean_raw = sanitize_raw_content(m["raw_content"])
            conn.execute(
                "UPDATE battle_messages SET discord_message_id = ?, raw_content = ? WHERE id = ?",
                (syn_msg_id, clean_raw, mid),
            )

        matches = conn.execute("SELECT match_id, tournament_name, opponent FROM battle_matches ORDER BY match_id").fetchall()
        for idx, bm in enumerate(matches, start=1):
            tname = f"テスト大会{idx:02d} バトルチャレンジ" if bm["tournament_name"] else None
            opp = f"ゲスト対戦相手{idx:02d}" if bm["opponent"] else None
            conn.execute(
                "UPDATE battle_matches SET tournament_name = ?, opponent = ? WHERE match_id = ?",
                (tname, opp, bm["match_id"]),
            )
        conn.commit()
        conn.execute("VACUUM")

    return ANONYMIZED_DB_PATH


def build_golden_outputs(db_path: Path = ANONYMIZED_DB_PATH) -> None:
    if GOLDEN_DIR.exists():
        shutil.rmtree(GOLDEN_DIR)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    orig_base = output_result.BASE_DIR
    orig_db = output_result.DB_PATH
    orig_out = output_result.OUTPUT_DIR
    orig_date = output_result.date

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    try:
        # Redirect BASE_DIR/docs/data -> GOLDEN_DIR via temporary structure
        temp_root = FIXTURES_DIR / "_tmp_export"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        (temp_root / "docs" / "data").mkdir(parents=True, exist_ok=True)

        output_result.BASE_DIR = temp_root
        output_result.DB_PATH = db_path
        output_result.date = _FixedDate

        output_result.export_players(conn)
        for season_id in output_result.SEASONS_TO_EXPORT:
            output_result.configure_season(season_id)
            output_result.export_battles(conn)
            matches = output_result.export_battle_matches(conn)
            output_result.export_battle_challenge_stats(conn, matches)
            output_result.export_battle_trend(conn)
            output_result.export_usage(conn)
            output_result.export_pokemon_stats(conn)
            output_result.export_matchups(conn)

        for item in (temp_root / "docs" / "data").iterdir():
            shutil.move(str(item), str(GOLDEN_DIR / item.name))
        shutil.rmtree(temp_root)
    finally:
        output_result.BASE_DIR = orig_base
        output_result.DB_PATH = orig_db
        output_result.OUTPUT_DIR = orig_out
        output_result.date = orig_date
        conn.close()

    # Generate golden JS calculations using Node.js executing the exact logic of
    # player_list.js, player_detail.js, and character.js (手順6 §6-3).
    node_script = """
const fs = require('fs');
const path = require('path');

const goldenDir = process.argv[1];
const players = JSON.parse(fs.readFileSync(path.join(goldenDir, 'players.json'), 'utf8')).players;
const scopes = ['season_1', 'season_2', 'season_3', 'all'];
const battleTypes = ['all', 'normal', 'challenge'];

function computePlayerListJS(playersList, battles) {
    const usageByPlayer = new Map();
    battles.forEach(battle => {
        [battle.player1, battle.player2].forEach(side => {
            if (side.id == null) return;
            if (!usageByPlayer.has(side.id)) usageByPlayer.set(side.id, new Map());
            const usage = usageByPlayer.get(side.id);
            const pokemon = usage.get(side.pokemon_id) || {
                pokemon_id: side.pokemon_id,
                name: side.pokemon,
                count: 0
            };
            pokemon.count += 1;
            usage.set(side.pokemon_id, pokemon);
        });
    });
    return playersList.map(p => {
        const top = [...(usageByPlayer.get(p.id)?.values() || [])]
            .sort((a, b) => b.count - a.count)[0] || null;
        return { id: p.id, name: p.name, top_pokemon: top };
    });
}

function getPlayerSideJS(battle, playerId) {
    if (battle.player1.id === playerId) {
        return { battle, self: battle.player1, opponent: battle.player2, isFirst: battle.first_player === 1 };
    }
    return { battle, self: battle.player2, opponent: battle.player1, isFirst: battle.first_player === 2 };
}

function computePlayerDetailJS(player, allBattles, battleType) {
    const myBattles = allBattles.filter(b => b.player1.id === player.id || b.player2.id === player.id);
    const filtered = battleType === 'all'
        ? myBattles
        : myBattles.filter(b => (b.battle_type || 'normal') === battleType);
    const records = filtered.map(b => getPlayerSideJS(b, player.id));
    const wins = records.filter(r => r.self.result === 'win').length;
    const usageMap = new Map();
    records.forEach(r => {
        const key = r.self.pokemon_id;
        if (!usageMap.has(key)) {
            usageMap.set(key, { pokemon_id: r.self.pokemon_id, name: r.self.pokemon, count: 0 });
        }
        usageMap.get(key).count += 1;
    });
    const topPokemon = [...usageMap.values()].sort((a, b) => b.count - a.count)[0] || null;

    const recent10 = [...records].sort((a, b) => b.battle.id - a.battle.id).slice(0, 10);
    const recent10Wins = recent10.filter(r => r.self.result === 'win').length;

    const firstRecs = records.filter(r => r.isFirst);
    const secondAndUnknownRecs = records.filter(r => !r.isFirst);

    const pUsageMap = new Map();
    records.forEach(r => {
        const key = r.self.pokemon_id;
        if (!pUsageMap.has(key)) {
            pUsageMap.set(key, {
                pokemon_id: r.self.pokemon_id,
                name: r.self.pokemon,
                type1: r.self.type1,
                type2: r.self.type2,
                battles: 0,
                wins: 0
            });
        }
        const e = pUsageMap.get(key);
        e.battles += 1;
        if (r.self.result === 'win') e.wins += 1;
    });
    const pokemonUsage = [...pUsageMap.values()].sort((a, b) => b.battles - a.battles);
    const recent8 = [...records].sort((a, b) => b.battle.id - a.battle.id).slice(0, 8).map(r => r.battle.id);

    return {
        summary: { battles: records.length, wins, top_pokemon: topPokemon },
        recent: { battles: recent10.length, wins: recent10Wins },
        legacy_order: {
            first: { battles: firstRecs.length, wins: firstRecs.filter(r => r.self.result === 'win').length },
            second_plus_unknown: {
                battles: secondAndUnknownRecs.length,
                wins: secondAndUnknownRecs.filter(r => r.self.result === 'win').length
            },
            null_first_player_count: records.filter(r => r.battle.first_player == null).length
        },
        pokemon_usage: pokemonUsage,
        recent_battle_ids: recent8
    };
}

function getCharacterSideJS(battle, characterId) {
    if (battle.player1.pokemon_id === characterId) {
        return { player: battle.player1, opponent: battle.player2, isFirst: battle.first_player === 1 };
    }
    return { player: battle.player2, opponent: battle.player1, isFirst: battle.first_player === 2 };
}

function computeCharacterDetailJS(characterId, allBattles, allMatchups, allUsage, playersList) {
    const battles = allBattles.filter(b => b.player1.pokemon_id === characterId || b.player2.pokemon_id === characterId);
    const matchups = allMatchups.filter(m => m.pokemon1.id === characterId || m.pokemon2.id === characterId);
    const usage = allUsage.find(p => p.pokemon_id === characterId);
    const records = battles.map(b => getCharacterSideJS(b, characterId));
    const wins = records.filter(r => r.player.result === 'win').length;
    const sortedMatchups = [...matchups].sort((a, b) => b.battles - a.battles);
    const topMatchup = sortedMatchups[0] || null;
    const topOpponent = topMatchup
        ? (topMatchup.pokemon1.id === characterId ? topMatchup.pokemon2 : topMatchup.pokemon1)
        : null;

    const firstRecs = records.filter(r => r.isFirst);
    const secondAndUnknownRecs = records.filter(r => !r.isFirst && r.player.result);

    const playerNames = new Map(playersList.map(p => [p.id, p.name]));
    const usageByPlayer = new Map();
    battles.forEach(b => {
        const { player } = getCharacterSideJS(b, characterId);
        if (player.id == null || !playerNames.has(player.id)) return;
        usageByPlayer.set(player.id, (usageByPlayer.get(player.id) || 0) + 1);
    });
    const playerCounts = Object.fromEntries(usageByPlayer.entries());
    const recent8 = [...battles].sort((a, b) => b.id - a.id).slice(0, 8).map(b => b.id);

    return {
        usage_count: usage ? usage.usage_count : 0,
        summary: {
            battles: records.length,
            wins,
            top_opponent_id: topOpponent ? topOpponent.id : null,
            top_opponent_battles: topMatchup ? topMatchup.battles : null
        },
        legacy_order: {
            first: { battles: firstRecs.length, wins: firstRecs.filter(r => r.player.result === 'win').length },
            second_plus_unknown: {
                battles: secondAndUnknownRecs.length,
                wins: secondAndUnknownRecs.filter(r => r.player.result === 'win').length
            },
            null_first_player_count: battles.filter(b => b.first_player == null).length
        },
        player_counts: playerCounts,
        matchup_pairs: sortedMatchups.map(m => [m.pokemon1.id, m.pokemon2.id, m.battles]),
        recent_battle_ids: recent8
    };
}

const jsGolden = { player_list: {}, player_detail: {}, character_detail: {} };
for (const scope of scopes) {
    const battles = JSON.parse(fs.readFileSync(path.join(goldenDir, scope, 'battles.json'), 'utf8')).battles;
    const matchups = JSON.parse(fs.readFileSync(path.join(goldenDir, scope, 'matchups.json'), 'utf8')).matchups;
    const usage = JSON.parse(fs.readFileSync(path.join(goldenDir, scope, 'usage.json'), 'utf8')).pokemon;

    jsGolden.player_list[scope] = computePlayerListJS(players, battles);
    jsGolden.player_detail[scope] = {};
    for (const p of players) {
        jsGolden.player_detail[scope][p.id] = {};
        for (const bt of battleTypes) {
            jsGolden.player_detail[scope][p.id][bt] = computePlayerDetailJS(p, battles, bt);
        }
    }
    jsGolden.character_detail[scope] = {};
    for (let cid = 1; cid <= 23; cid++) {
        jsGolden.character_detail[scope][cid] = computeCharacterDetailJS(cid, battles, matchups, usage, players);
    }
}
fs.writeFileSync(path.join(goldenDir, 'js_golden.json'), JSON.stringify(jsGolden, null, 2), 'utf8');
"""
    subprocess.run(
        ["node", "-e", node_script, str(GOLDEN_DIR)],
        check=True,
    )


if __name__ == "__main__":
    db = build_anonymized_db()
    build_golden_outputs(db)
    print(f"Generated anonymized DB at {db} and golden JSONs at {GOLDEN_DIR}")

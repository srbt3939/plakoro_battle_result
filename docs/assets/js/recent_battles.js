// =========================
// グローバル変数
// =========================

let emojiData = null;
const DATA_VERSION = "2026-11-10-battle-challenge";

const typeImageNames = {
    "くさ": "grass",
    "ほのお": "fire",
    "みず": "water",
    "かみなり": "electric",
    "ちょう": "psychic",
    "とう": "fighting",
    "あく": "dark",
    "はがね": "steel",
    "ドラゴン": "dragon",
    "そら": "flying",
    "むしょく": "normal"
};


// =========================
// JSON読み込み
// =========================

async function loadJSON(file) {

    const response = await fetch(
        `data/all/${file}?v=${DATA_VERSION}`
    );

    return await response.json();
}


// =========================
// emoji.json読み込み
// =========================

async function loadEmojiData() {

    try {

        const response =
            await fetch(`data/emoji.json?v=${DATA_VERSION}`);

        if (!response.ok) {
            throw new Error(
                "emoji.jsonの読み込みに失敗"
            );
        }

        emojiData =
            await response.json();

    } catch (error) {

        console.error(error);

    }

}


// =========================
// ヘルパー関数：ポケモンIDから絵文字情報取得
// =========================

function getEmojiByPokemonId(pokemonId) {

    if (!emojiData || !emojiData.pokemon) {
        return null;
    }

    return emojiData.pokemon.find(
        p => p.id === pokemonId
    );

}


// =========================
// ヘルパー関数：ポケモン名と画像のHTML生成
// =========================

function getPokemonIdentityHTML(name, pokemonId) {

    const emoji = getEmojiByPokemonId(
        pokemonId
    );

    let pokemonImage = "";

    if (emoji) {
        const match = emoji.emoji.match(
            /<:([^:]+):(\d+)>/
        );

        if (match) {
            pokemonImage = `
                <img
                    src="assets/images/emoji_images/${match[2]}.webp"
                    alt="${escapeHTML(name)}"
                    class="pokemon-emoji"
                    onerror="this.style.display='none'"
                >
            `;
        }
    }

    const identityHTML = `
        ${pokemonImage}
        <span>${escapeHTML(name)}</span>
    `;

    return `<a class="pokemon-detail-link" href="characters/character.html?id=${encodeURIComponent(pokemonId)}">${identityHTML}</a>`;

}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[character]);
}


function getTypeImagesHTML(types) {

    return [...new Set(types)]
        .map(type => {
            const imageName = typeImageNames[type];

            if (!imageName) {
                return "";
            }

            return `
                <span class="type-item">
                    <img
                        src="assets/images/type_images/${imageName}.webp"
                        alt="${type}"
                        class="type-emoji"
                        onerror="this.style.display='none'"
                    >
                </span>
            `;
        })
        .join("");

}


// =========================
// 1試合分のHTML生成
// =========================

function formatBattleEntry(battle) {

    const firstOrder = battle.first_player === 1 ? "先攻" : battle.first_player === 2 ? "後攻" : "不明";
    const secondOrder = battle.first_player === 1 ? "後攻" : battle.first_player === 2 ? "先攻" : "不明";

    const playerHTML = (player, order) => `
        <div class="recent-battle-player ${player.result === "win" ? "is-win" : "is-lose"}">
            <span class="recent-battle-result">${player.result === "win" ? "WIN" : "LOSE"}</span>
            ${player.name ? `<span class="recent-battle-name">${escapeHTML(player.name)}</span>` : ""}
            ${getPokemonIdentityHTML(player.pokemon, player.pokemon_id)}
            <span class="pokemon-type">${getTypeImagesHTML([player.type1, player.type2].filter(Boolean))}</span>
            <span class="recent-battle-order">${order}</span>
        </div>`;

    return `
        <div class="recent-battle-entry">
            <div class="recent-battle-number">第${battle.id}戦</div>
            <div class="recent-battle-players">
                ${playerHTML(battle.player1, firstOrder)}
                <span class="vs">VS</span>
                ${playerHTML(battle.player2, secondOrder)}
            </div>
        </div>`;

}


function formatBattleMatch(match) {

    const resultLabel = match.match_result === "win"
        ? "WIN"
        : match.match_result === "lose" ? "LOSE" : "勝敗未確定";
    const resultClass = match.match_result === "win" ? "is-win" : "is-lose";
    const gamesHTML = match.games.map(game => {
        const firstOrder = game.first_player === 1 ? "先攻" : game.first_player === 2 ? "後攻" : "不明";
        const secondOrder = game.first_player === 1 ? "後攻" : game.first_player === 2 ? "先攻" : "不明";
        const playerHTML = (player, order) => `
            <div class="recent-battle-player ${player.result === "win" ? "is-win" : "is-lose"}">
                <span class="recent-battle-result">${player.result === "win" ? "WIN" : "LOSE"}</span>
                ${getPokemonIdentityHTML(player.pokemon, player.pokemon_id)}
                <span class="pokemon-type">${getTypeImagesHTML([player.type1, player.type2].filter(Boolean))}</span>
                <span class="recent-battle-order">${order}</span>
            </div>`;
        return `
            <div class="battle-match-game">
                <h4>GAME ${game.game_number}</h4>
                <div class="recent-battle-players">
                    ${playerHTML(game.player1, firstOrder)}
                    <span class="vs">VS</span>
                    ${playerHTML(game.player2, secondOrder)}
                </div>
            </div>`;
    }).join("");

    return `
        <article class="battle-match-card">
            <div class="battle-match-heading">
                <strong>${escapeHTML(match.tournament_name || "大会名未記入")}</strong>
                <span class="battle-match-official">公式大会 · BO3</span>
                <time>${escapeHTML(match.match_date || "")}</time>
            </div>
            <div class="battle-match-result ${resultClass}">
                <span>${escapeHTML(match.opponent || "対戦相手未記入")}</span>
                <strong>${resultLabel}</strong>
                <b>${match.player_score} - ${match.opponent_score}</b>
            </div>
            <div class="battle-match-games">${gamesHTML}</div>
        </article>`;
}


// =========================
// メイン
// =========================

async function main() {

    await loadEmojiData();

    const container =
        document.getElementById(
            "battle-history-list"
        );

    const [battlesData, matchesData, stats] = await Promise.all([
        loadJSON("battles.json"),
        loadJSON("battle_matches.json"),
        loadJSON("battle_challenge_stats.json"),
    ]);

    const series = stats.series;
    const games = stats.games;
    document.getElementById("battle-challenge-summary").innerHTML = `
        <span>大会シリーズ ${series.series_count}件</span>
        <span>${series.wins}勝 ${series.losses}敗${series.undecided ? `・未確定 ${series.undecided}件` : ""}（勝率 ${series.win_rate}%）</span>
        <span>ゲーム ${games.games}戦 ${games.wins}勝 ${games.losses}敗（勝率 ${games.win_rate}%）</span>`;

    const entries = [
        ...battlesData.battles.map(battle => ({
            kind: "normal",
            sortId: battle.battle_message_id,
            itemId: battle.battle_number,
            battle,
        })),
        ...matchesData.matches.map(match => ({
            kind: "challenge",
            sortId: match.battle_message_id,
            itemId: match.match_id,
            match,
        })),
    ];
    entries.sort((a, b) => b.sortId - a.sortId || b.itemId - a.itemId);

    function render(filter) {
        const recent = entries
            .filter(entry => filter === "all" || entry.kind === filter)
            .slice(0, 30);
        container.innerHTML = recent.map(entry =>
            entry.kind === "challenge"
                ? formatBattleMatch(entry.match)
                : formatBattleEntry(entry.battle)
        ).join("") || "<p>試合データがありません。</p>";
    }

    document.querySelectorAll("[data-battle-filter]").forEach(button => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-battle-filter]").forEach(item =>
                item.classList.toggle("is-active", item === button)
            );
            render(button.dataset.battleFilter);
        });
    });
    render("all");

}


main();
const ASSET_PATH = "";
const DATA_VERSION = "aws-v1";

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

let emojiData = null;

async function loadEmojiData() {
    const response = await fetch(`data/emoji.json?v=${DATA_VERSION}`);
    if (response.ok) {
        emojiData = await response.json();
    }
}

function getEmojiByPokemonId(pokemonId) {
    return emojiData?.pokemon?.find(p => p.id === pokemonId) || null;
}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[ch]);
}

function getPokemonIdentityHTML(name, pokemonId) {
    const emoji = getEmojiByPokemonId(pokemonId);
    let pokemonImage = "";
    if (emoji) {
        const match = emoji.emoji.match(/<:([^:]+):(\d+)>/);
        if (match) {
            pokemonImage = `<img src="assets/images/emoji_images/${match[2]}.webp" alt="${escapeHTML(name)}" class="pokemon-emoji" onerror="this.style.display='none'">`;
        }
    }
    return `<a class="pokemon-detail-link" href="characters/character.html?id=${encodeURIComponent(pokemonId)}">${pokemonImage}<span>${escapeHTML(name)}</span></a>`;
}

function getTypeImagesHTML(types) {
    return [...new Set(types)].map(type => {
        const imageName = typeImageNames[type];
        return imageName
            ? `<span class="type-item"><img src="assets/images/type_images/${imageName}.webp" alt="${type}" class="type-emoji" onerror="this.style.display='none'"></span>`
            : "";
    }).join("");
}

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

async function loadRecentFeed(filter) {
    const container = document.getElementById("battle-history-list");
    try {
        const data = await apiFetch(`/api/recent-battles?type=${encodeURIComponent(filter)}&limit=30`);
        const series = data.battle_challenge_stats.series;
        const games = data.battle_challenge_stats.games;
        document.getElementById("battle-challenge-summary").innerHTML = `
            <span>大会シリーズ ${series.series_count}件</span>
            <span>${series.wins}勝 ${series.losses}敗${series.undecided ? `・未確定 ${series.undecided}件` : ""}（勝率 ${series.win_rate}%）</span>
            <span>ゲーム ${games.games}戦 ${games.wins}勝 ${games.losses}敗（勝率 ${games.win_rate}%）</span>`;

        container.innerHTML = data.items.map(entry =>
            entry.kind === "challenge"
                ? formatBattleMatch(entry.match)
                : formatBattleEntry(entry.battle)
        ).join("") || "<p>試合データがありません。</p>";
    } catch (err) {
        renderApiError(container, err, () => loadRecentFeed(filter));
    }
}

async function main() {
    await Promise.all([loadEmojiData(), initMemberBar("")]);
    document.querySelectorAll("[data-battle-filter]").forEach(button => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-battle-filter]").forEach(item =>
                item.classList.toggle("is-active", item === button)
            );
            loadRecentFeed(button.dataset.battleFilter);
        });
    });
    await loadRecentFeed("all");
}

main();

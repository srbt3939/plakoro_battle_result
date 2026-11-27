const ASSET_PATH = "../";
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
let playerId = null;

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[ch]);
}

async function loadEmojiData() {
    const response = await fetch(`${ASSET_PATH}data/emoji.json?v=${DATA_VERSION}`);
    if (response.ok) {
        emojiData = await response.json();
    }
}

function getEmoji(pokemonId) {
    return emojiData?.pokemon?.find(p => p.id === pokemonId);
}

function getIdentityHTML(name, pokemonId, className = "") {
    const emoji = getEmoji(pokemonId);
    const imageMatch = emoji?.emoji?.match(/<:([^:]+):(\d+)>/);
    const image = imageMatch
        ? `<img src="${ASSET_PATH}assets/images/emoji_images/${imageMatch[2]}.webp" alt="${escapeHTML(name)}" class="pokemon-emoji" onerror="this.style.display='none'">`
        : "";
    return `<span class="pokemon-name-bold ${className}">${image}<span>${escapeHTML(name)}</span></span>`;
}

function getTypeHTML(types) {
    return [...new Set(types.filter(Boolean))].map(type => {
        const imageName = typeImageNames[type];
        return imageName
            ? `<img src="${ASSET_PATH}assets/images/type_images/${imageName}.webp" alt="${type}" class="type-emoji">`
            : "";
    }).join("");
}

function formatRate(wins, battles) {
    return battles ? (wins / battles * 100).toFixed(1) : "0.0";
}

function formatPercentage(value) {
    return Number(value || 0).toFixed(1);
}

function setPlayerName(name) {
    document.querySelectorAll("span[data-player-name]").forEach(el => {
        el.textContent = name;
    });
    document.title = `${name} 対戦データ | ON PLA!`;
}

function renderSummary(summary) {
    const battles = summary.battles;
    const wins = summary.wins;
    const topPokemon = summary.top_pokemon;

    document.getElementById("player-win-rate").textContent = `${formatRate(wins, battles)}%`;
    document.getElementById("player-record").textContent = `${wins}勝 ${battles - wins}敗`;
    document.getElementById("player-battles").textContent = `${battles}試合`;
    document.getElementById("player-top-pokemon").innerHTML =
        topPokemon ? getIdentityHTML(topPokemon.name, topPokemon.pokemon_id) : "-";
    document.getElementById("player-top-pokemon-note").textContent =
        topPokemon ? `${topPokemon.count}戦使用` : "-";
    document.getElementById("player-identity").innerHTML = `<span class="player-avatar">🚹</span>`;
}

function renderRecentWinRate(recent) {
    const battles = recent.battles;
    const wins = recent.wins;
    document.getElementById("player-recent-win-rate").textContent = `${formatRate(wins, battles)}%`;
    document.getElementById("player-recent-record").textContent = `${battles}戦中 ${wins}勝 ${battles - wins}敗`;
}

function renderOrderStats(order) {
    const groups = [
        { label: "先攻", data: order.first },
        { label: "後攻", data: order.second },
    ];
    if (order.unknown && order.unknown.battles > 0) {
        groups.push({ label: "不明", data: order.unknown });
    }
    document.getElementById("player-order-stats").innerHTML = groups.map(group => {
        const b = group.data.battles;
        const w = group.data.wins;
        const rate = formatRate(w, b);
        return `<div class="order-stat"><div class="order-stat-top"><strong>${group.label}</strong><b>${rate}%</b></div><div class="detail-meter"><span style="width:${rate}%"></span></div><span>${b}戦 ${w}勝 ${b - w}敗</span></div>`;
    }).join("");
}

function renderPokemonUsage(pokemonUsage) {
    document.getElementById("player-pokemon-usage").innerHTML = pokemonUsage.length
        ? pokemonUsage.map(entry => {
            const rate = formatRate(entry.wins, entry.battles);
            const losses = entry.battles - entry.wins;
            return `<article class="character-matchup-row"><div class="character-matchup-main"><div>${getIdentityHTML(entry.name, entry.pokemon_id)}<span class="matchup-types">${getTypeHTML([entry.type1, entry.type2])}</span></div><span class="matchup-count">${entry.battles}戦</span><strong>${formatPercentage(rate)}%</strong></div><div class="detail-meter matchup-meter-large"><span style="width:${rate}%"></span></div><div class="character-matchup-foot"><span>${entry.wins}勝</span><span>${losses}敗</span></div></article>`;
        }).join("")
        : "<p>対戦データがありません。</p>";
}

function formatBattleEntry(battle) {
    const isSelfP1 = battle.player1.id === playerId;
    const self = isSelfP1 ? battle.player1 : battle.player2;
    const opponent = isSelfP1 ? battle.player2 : battle.player1;
    const selfOrder = battle.first_player == null
        ? "不明"
        : ((isSelfP1 && battle.first_player === 1) || (!isSelfP1 && battle.first_player === 2) ? "先攻" : "後攻");
    const oppOrder = battle.first_player == null
        ? "不明"
        : (selfOrder === "先攻" ? "後攻" : "先攻");

    const battleTypeLabel = battle.battle_type === "challenge"
        ? [
            battle.tournament_name || "バトルチャレンジ",
            battle.game_number ? `GAME ${battle.game_number}` : "",
            battle.player_score != null ? `${battle.player_score} - ${battle.opponent_score}` : ""
        ].filter(Boolean).join(" · ")
        : "通常対戦";

    const sideHTML = (side, orderLabel) => `
        <div class="recent-battle-player ${side.result === "win" ? "is-win" : "is-lose"}">
            <span class="recent-battle-result">${side.result === "win" ? "WIN" : "LOSE"}</span>
            ${getIdentityHTML(side.pokemon, side.pokemon_id)}
            <span class="pokemon-type">${getTypeHTML([side.type1, side.type2])}</span>
            <span class="recent-battle-order">${orderLabel}</span>
        </div>`;

    return `
        <div class="recent-battle-entry">
            <div class="recent-battle-number">第${battle.id}戦</div>
            <span class="battle-type-label">${escapeHTML(battleTypeLabel)}</span>
            <div class="recent-battle-players">
                ${sideHTML(self, selfOrder)}
                <span class="vs">VS</span>
                ${sideHTML(opponent, oppOrder)}
            </div>
        </div>`;
}

async function loadAndRender(season, battleType) {
    const mainEl = document.querySelector("main");
    try {
        const data = await apiFetch(
            `/api/players/${encodeURIComponent(playerId)}?scope=${encodeURIComponent(season)}&type=${encodeURIComponent(battleType)}`
        );
        setPlayerName(data.player.name);
        renderSummary(data.summary);
        renderRecentWinRate(data.recent);
        renderOrderStats(data.order);
        renderPokemonUsage(data.pokemon_usage);
        document.getElementById("player-recent-battles").innerHTML = data.recent_battles.length
            ? data.recent_battles.map(formatBattleEntry).join("")
            : "<p>試合データがありません。</p>";
    } catch (error) {
        renderApiError(mainEl, error, () => loadAndRender(season, battleType));
    }
}

async function main() {
    playerId = Number(new URLSearchParams(location.search).get("id"));
    if (!playerId) {
        document.querySelector("main").innerHTML = "<p class=\"load-error\">プレイヤーが指定されていません。</p>";
        return;
    }
    await Promise.all([loadEmojiData(), initMemberBar("../")]);
    const seasonSelect = document.getElementById("season-select");
    const battleTypeSelect = document.getElementById("battle-type-select");
    const reload = () => loadAndRender(
        seasonSelect ? seasonSelect.value : "all",
        battleTypeSelect ? battleTypeSelect.value : "all"
    );
    await reload();
    seasonSelect?.addEventListener("change", reload);
    battleTypeSelect?.addEventListener("change", reload);
}

main();

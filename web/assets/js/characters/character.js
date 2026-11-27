const requestedCharacterId = new URLSearchParams(window.location.search).get("id");
const characterId = /^\d+$/.test(requestedCharacterId || "") ? Number(requestedCharacterId) : null;
let characterName = "";
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

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[ch]);
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
            ? `<span class="type-item"><img src="${ASSET_PATH}assets/images/type_images/${imageName}.webp" alt="${type}" class="type-emoji" onerror="this.style.display='none'"></span>`
            : "";
    }).join("");
}

function getPokemonDisplayHTML(pokemon) {
    const typeHTML = getTypeHTML([pokemon.type1, pokemon.type2]);
    const weaknessHTML = getTypeHTML(pokemon.weaknesses || []);
    return `${getIdentityHTML(pokemon.name, pokemon.id)}`
        + `${typeHTML ? `<span class="pokemon-meta">${typeHTML}</span>` : ""}`
        + `${weaknessHTML ? `<span class="pokemon-meta"><span class="pokemon-meta-label">弱点：</span>${weaknessHTML}</span>` : ""}`;
}

function formatRate(wins, battles) {
    return battles ? (wins / battles * 100).toFixed(1) : "0.0";
}

function formatPercentage(value) {
    return Number(value || 0).toFixed(1);
}

function setCharacterNames() {
    document.querySelectorAll("span[data-character-name]").forEach(el => {
        el.textContent = characterName;
    });
    document.title = `${characterName} 対戦データ | ON PLA!`;
}

function renderSummary(summary, usageCount) {
    const wins = summary.wins;
    const battles = summary.battles;
    const topOpponent = summary.top_opponent;

    document.getElementById("character-win-rate").textContent = `${formatRate(wins, battles)}%`;
    document.getElementById("character-record").textContent = `${wins}勝 ${battles - wins}敗`;
    document.getElementById("character-battles").textContent = `${usageCount}試合`;
    document.getElementById("character-top-opponent").innerHTML =
        topOpponent ? getIdentityHTML(topOpponent.name, topOpponent.id) : "-";
    document.getElementById("character-top-opponent-note").textContent =
        topOpponent ? `${topOpponent.battles}戦` : "-";
    document.getElementById("character-identity").innerHTML =
        getIdentityHTML(characterName, characterId, "detail-pokemon-name");
}

function renderOrderStats(order) {
    const groups = [
        { label: "先攻", data: order.first },
        { label: "後攻", data: order.second },
    ];
    if (order.unknown && order.unknown.battles > 0) {
        groups.push({ label: "不明", data: order.unknown });
    }
    document.getElementById("character-order-stats").innerHTML = groups.map(group => {
        const b = group.data.battles;
        const w = group.data.wins;
        const rate = formatRate(w, b);
        return `<div class="order-stat"><div class="order-stat-top"><strong>${group.label}</strong><b>${rate}%</b></div><div class="detail-meter"><span style="width:${rate}%"></span></div><span>${b}戦 ${w}勝 ${b - w}敗</span></div>`;
    }).join("");
}

function renderPlayerRanking(playerUsage) {
    const ranking = [...playerUsage]
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "ja"))
        .slice(0, 3);
    document.getElementById("character-player-ranking").innerHTML = ranking.length
        ? ranking.map((player, index) =>
            `<a class="character-player-card" href="../players/player.html?id=${encodeURIComponent(player.id)}"><span class="character-player-rank">${index + 1}位</span><span class="character-player-name">${escapeHTML(player.name)}</span><span class="character-player-usage">${player.count}試合</span></a>`
        ).join("")
        : "<p>プレイヤー別の使用データがありません。</p>";
}

function renderMatchups(matchups) {
    document.getElementById("character-matchups").innerHTML = matchups.length ? matchups.map(matchup => {
        const characterIsFirst = matchup.pokemon1.id === characterId;
        const pokemon1 = characterIsFirst ? matchup.pokemon1 : matchup.pokemon2;
        const pokemon2 = characterIsFirst ? matchup.pokemon2 : matchup.pokemon1;
        const pokemon1WinRate = characterIsFirst ? matchup.pokemon1_win_rate : matchup.pokemon2_win_rate;
        const pokemon2WinRate = characterIsFirst ? matchup.pokemon2_win_rate : matchup.pokemon1_win_rate;
        const pokemon1IsLower = pokemon1WinRate < pokemon2WinRate;
        const pokemon2IsLower = pokemon2WinRate < pokemon1WinRate;
        return `
            <div class="matchup-row">
                <span class="matchup-pokemon">
                    <span class="pokemon-name-bold">${getPokemonDisplayHTML(pokemon1)}</span><br>
                    <span class="pokemon-rate">${formatPercentage(pokemon1WinRate)}%</span>
                </span>
                <span class="vs">VS<br>${matchup.battles}戦</span>
                <span class="matchup-pokemon">
                    <span class="pokemon-name-bold">${getPokemonDisplayHTML(pokemon2)}</span><br>
                    <span class="pokemon-rate">${formatPercentage(pokemon2WinRate)}%</span>
                </span>
                <div class="matchup-meter" role="img" aria-label="${escapeHTML(pokemon1.name)} ${formatPercentage(pokemon1WinRate)}%、${escapeHTML(pokemon2.name)} ${formatPercentage(pokemon2WinRate)}%">
                    <span class="matchup-meter-pokemon1${pokemon1IsLower ? " is-lower" : ""}" style="width: ${pokemon1WinRate}%"></span>
                    <span class="matchup-meter-pokemon2${pokemon2IsLower ? " is-lower" : ""}" style="width: ${pokemon2WinRate}%"></span>
                </div>
            </div>`;
    }).join("") : "<p>対戦データがありません。</p>";
}

function renderRecentBattles(recentBattles) {
    document.getElementById("character-recent-battles").innerHTML = recentBattles.length ? recentBattles.map(battle => {
        const isP1 = battle.player1.pokemon_id === characterId;
        const player = isP1 ? battle.player1 : battle.player2;
        const opponent = isP1 ? battle.player2 : battle.player1;
        const orderLabel = battle.first_player == null
            ? "不明"
            : ((isP1 && battle.first_player === 1) || (!isP1 && battle.first_player === 2) ? "先攻" : "後攻");
        const result = player.result === "win" ? "WIN" : "LOSE";
        const resultClass = player.result === "win" ? "is-win" : "is-lose";
        return `<div class="character-recent-row"><span class="recent-battle-number">第${battle.id}戦</span><span class="recent-result ${resultClass}">${result}</span>${getIdentityHTML(opponent.pokemon, opponent.pokemon_id)}<span class="recent-opponent-type">${getTypeHTML([opponent.type1, opponent.type2])}</span><span class="recent-order">${orderLabel}</span></div>`;
    }).join("") : "<p>試合データがありません。</p>";
}

async function renderSeason(season) {
    const mainEl = document.querySelector("main");
    try {
        const data = await apiFetch(
            `/api/characters/${encodeURIComponent(characterId)}?scope=${encodeURIComponent(season)}`
        );
        characterName = data.pokemon.name;
        setCharacterNames();
        renderSummary(data.summary, data.usage_count);
        renderOrderStats(data.order);
        renderPlayerRanking(data.player_usage);
        renderMatchups(data.matchups);
        renderRecentBattles(data.recent_battles);
    } catch (error) {
        renderApiError(mainEl, error, () => renderSeason(season));
    }
}

async function main() {
    if (!Number.isSafeInteger(characterId)) {
        document.querySelector("main").innerHTML = '<p class="load-error">キャラクターIDが指定されていません。</p>';
        return;
    }
    const emojiResp = await fetch(`${ASSET_PATH}data/emoji.json?v=${DATA_VERSION}`);
    if (emojiResp.ok) {
        emojiData = await emojiResp.json();
    }
    await initMemberBar("../");
    const seasonSelect = document.getElementById("season-select");
    await renderSeason(seasonSelect.value);
    seasonSelect.addEventListener("change", () => renderSeason(seasonSelect.value));
}

main();

const requestedCharacterId = new URLSearchParams(window.location.search).get("id");
const characterId = /^\d+$/.test(requestedCharacterId || "")
    ? Number(requestedCharacterId)
    : null;
let characterName = "";
const DATA_PATH = "../data";
const ASSET_PATH = "../";
const DATA_VERSION = "2026-10-30-character-usage-fix";

const typeImageNames = {
    "あく": "dark",
    "かみなり": "electric",
    "くさ": "grass",
    "ちょう": "psychic",
    "ほのお": "fire",
    "みず": "water",
    "はがね": "steel",
    "とう": "fighting",
    "そら": "flying",
    "むしょく": "normal"
};

let emojiData = null;

async function loadJSON(file, basePath = DATA_PATH) {
    const response = await fetch(`${basePath}/${file}?v=${DATA_VERSION}`);
    if (!response.ok) {
        throw new Error(`${file}の読み込みに失敗しました`);
    }
    return response.json();
}

function getCharacterDataPath(season) {
    return `${DATA_PATH}/${season}`;
}

function getEmoji(pokemonId) {
    return emojiData?.pokemon?.find(pokemon => pokemon.id === pokemonId);
}

function getIdentityHTML(name, pokemonId, className = "") {
    const emoji = getEmoji(pokemonId);
    const imageMatch = emoji?.emoji?.match(/<:([^:]+):(\d+)>/);
    const image = imageMatch
        ? `<img src="${ASSET_PATH}assets/images/emoji_images/${imageMatch[2]}.webp" alt="${name}" class="pokemon-emoji" onerror="this.style.display='none'">`
        : "";

    return `<span class="pokemon-name-bold ${className}">${image}<span>${name}</span></span>`;
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

function getCharacterSide(battle) {
    if (battle.player1.pokemon_id === characterId) {
        return { player: battle.player1, opponent: battle.player2, isFirst: battle.first_player === 1 };
    }
    return { player: battle.player2, opponent: battle.player1, isFirst: battle.first_player === 2 };
}

function formatRate(wins, battles) {
    return battles ? (wins / battles * 100).toFixed(1) : "0.0";
}

function formatPercentage(value) {
    return Number(value || 0).toFixed(1);
}

function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[character]);
}

function setCharacterNames() {
    document.querySelectorAll("span[data-character-name]").forEach(element => {
        element.textContent = characterName;
    });
    document.title = `${characterName} 対戦データ | ON PLA!`;
}

function renderSummary(battles, matchups, usageCount) {
    const records = battles.map(getCharacterSide);
    const wins = records.filter(record => record.player.result === "win").length;
    const topMatchup = [...matchups].sort((a, b) => b.battles - a.battles)[0];
    const opponent = topMatchup
        ? (topMatchup.pokemon1.id === characterId ? topMatchup.pokemon2 : topMatchup.pokemon1)
        : null;

    document.getElementById("character-win-rate").textContent = `${formatRate(wins, records.length)}%`;
    document.getElementById("character-record").textContent = `${wins}勝 ${records.length - wins}敗`;
    document.getElementById("character-battles").textContent = `${usageCount}試合`;
    document.getElementById("character-top-opponent").innerHTML = opponent ? getIdentityHTML(opponent.name, opponent.id) : "-";
    document.getElementById("character-top-opponent-note").textContent = opponent ? `${topMatchup.battles}戦` : "-";
    document.getElementById("character-identity").innerHTML = getIdentityHTML(characterName, characterId, "detail-pokemon-name");
}

function renderOrderStats(battles) {
    const records = battles.map(getCharacterSide);
    const groups = [
        { label: "先攻", records: records.filter(record => record.isFirst) },
        { label: "後攻", records: records.filter(record => !record.isFirst && record.player.result) }
    ];

    document.getElementById("character-order-stats").innerHTML = groups.map(group => {
        const wins = group.records.filter(record => record.player.result === "win").length;
        const rate = formatRate(wins, group.records.length);
        return `<div class="order-stat"><div class="order-stat-top"><strong>${group.label}</strong><b>${rate}%</b></div><div class="detail-meter"><span style="width:${rate}%"></span></div><span>${group.records.length}戦 ${wins}勝 ${group.records.length - wins}敗</span></div>`;
    }).join("");
}

function renderPlayerRanking(battles, players) {
    const playerNames = new Map(players.map(player => [player.id, player.name]));
    const usageByPlayer = new Map();

    battles.forEach(battle => {
        const { player } = getCharacterSide(battle);
        if (player.id == null || !playerNames.has(player.id)) {
            return;
        }

        usageByPlayer.set(player.id, (usageByPlayer.get(player.id) || 0) + 1);
    });

    const ranking = [...usageByPlayer.entries()]
        .map(([id, count]) => ({ id, count, name: playerNames.get(id) }))
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "ja"))
        .slice(0, 3);

    document.getElementById("character-player-ranking").innerHTML = ranking.length
        ? ranking.map((player, index) => {
            return `<a class="character-player-card" href="../players/player.html?id=${encodeURIComponent(player.id)}"><span class="character-player-rank">${index + 1}位</span><span class="character-player-name">${escapeHTML(player.name)}</span><span class="character-player-usage">${player.count}試合</span></a>`;
        }).join("")
        : "<p>プレイヤー別の使用データがありません。</p>";
}

function renderMatchups(matchups) {
    const sorted = [...matchups].sort((a, b) => b.battles - a.battles);
    document.getElementById("character-matchups").innerHTML = sorted.length ? sorted.map(matchup => {
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
                    <span class="pokemon-name-bold">${getPokemonDisplayHTML(pokemon1)}</span>
                    <br>
                    <span class="pokemon-rate">${formatPercentage(pokemon1WinRate)}%</span>
                </span>
                <span class="vs">VS<br>${matchup.battles}戦</span>
                <span class="matchup-pokemon">
                    <span class="pokemon-name-bold">${getPokemonDisplayHTML(pokemon2)}</span>
                    <br>
                    <span class="pokemon-rate">${formatPercentage(pokemon2WinRate)}%</span>
                </span>
                <div class="matchup-meter" role="img" aria-label="${pokemon1.name} ${formatPercentage(pokemon1WinRate)}%、${pokemon2.name} ${formatPercentage(pokemon2WinRate)}%">
                    <span class="matchup-meter-pokemon1${pokemon1IsLower ? " is-lower" : ""}" style="width: ${pokemon1WinRate}%"></span>
                    <span class="matchup-meter-pokemon2${pokemon2IsLower ? " is-lower" : ""}" style="width: ${pokemon2WinRate}%"></span>
                </div>
            </div>
        `;
    }).join("") : "<p>対戦データがありません。</p>";
}

function renderRecentBattles(battles) {
    const recent = [...battles].sort((a, b) => b.id - a.id).slice(0, 8);
    document.getElementById("character-recent-battles").innerHTML = recent.length ? recent.map(battle => {
        const record = getCharacterSide(battle);
        const result = record.player.result === "win" ? "WIN" : "LOSE";
        const resultClass = record.player.result === "win" ? "is-win" : "is-lose";
        return `<div class="character-recent-row"><span class="recent-battle-number">第${battle.id}戦</span><span class="recent-result ${resultClass}">${result}</span>${getIdentityHTML(record.opponent.pokemon, record.opponent.pokemon_id)}<span class="recent-opponent-type">${getTypeHTML([record.opponent.type1, record.opponent.type2])}</span><span class="recent-order">${record.isFirst ? "先攻" : "後攻"}</span></div>`;
    }).join("") : "<p>試合データがありません。</p>";
}

async function renderSeason(season, players) {
    const characterDataPath = getCharacterDataPath(season);
    const [battleData, matchupData, usageData] = await Promise.all([
        loadJSON("battles.json", characterDataPath),
        loadJSON("matchups.json", characterDataPath),
        loadJSON("usage.json", characterDataPath)
    ]);

    const battles = battleData.battles.filter(battle =>
        battle.player1.pokemon_id === characterId || battle.player2.pokemon_id === characterId
    );
    const matchups = matchupData.matchups.filter(matchup =>
        matchup.pokemon1.id === characterId || matchup.pokemon2.id === characterId
    );
    const usage = usageData.pokemon.find(pokemon => pokemon.pokemon_id === characterId);

    renderSummary(battles, matchups, usage?.usage_count || 0);
    renderOrderStats(battles);
    renderPlayerRanking(battles, players);
    renderMatchups(matchups);
    renderRecentBattles(battles);
}

async function main() {
    try {
        const [emoji, playerData] = await Promise.all([
            loadJSON("emoji.json"),
            loadJSON("players.json")
        ]);
        emojiData = emoji;
        const character = emojiData.pokemon?.find(pokemon => pokemon.id === characterId);
        if (!Number.isSafeInteger(characterId) || !character) {
            throw new Error("キャラクターIDが指定されていないか、存在しないキャラクターです");
        }
        characterName = character.name;
        setCharacterNames();

        const seasonSelect = document.getElementById("season-select");
        const initialSeason = seasonSelect.value;
        await renderSeason(initialSeason, playerData.players);
        seasonSelect.addEventListener("change", () =>
            renderSeason(seasonSelect.value, playerData.players)
        );
    } catch (error) {
        console.error(error);
        const message = error.message.includes("キャラクターID")
            ? error.message
            : "データを読み込めませんでした。";
        document.querySelector("main").innerHTML = `<p class="load-error">${message}</p>`;
    }
}

main();

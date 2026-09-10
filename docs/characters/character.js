const page = document.body;
const characterId = Number(page.dataset.characterId);
const characterName = page.dataset.characterName;
const DATA_PATH = "../data";
const ASSET_PATH = "../";

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
    const response = await fetch(`${basePath}/${file}`);
    if (!response.ok) {
        throw new Error(`${file}の読み込みに失敗しました`);
    }
    return response.json();
}

function getEmoji(pokemonId) {
    return emojiData?.pokemon?.find(pokemon => pokemon.id === pokemonId);
}

function getIdentityHTML(name, pokemonId, className = "") {
    const emoji = getEmoji(pokemonId);
    const imageMatch = emoji?.emoji?.match(/<:([^:]+):(\d+)>/);
    const image = imageMatch
        ? `<img src="${ASSET_PATH}emoji_images/${imageMatch[2]}.webp" alt="${name}" class="pokemon-emoji" onerror="this.style.display='none'">`
        : "";

    return `<span class="pokemon-name-bold ${className}">${image}<span>${name}</span></span>`;
}

function getTypeHTML(types) {
    return [...new Set(types.filter(Boolean))].map(type => {
        const imageName = typeImageNames[type];
        return imageName
            ? `<img src="${ASSET_PATH}type_images/${imageName}.webp" alt="${type}" class="type-emoji">`
            : "";
    }).join("");
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

function setCharacterNames() {
    document.querySelectorAll("span[data-character-name]").forEach(element => {
        element.textContent = characterName;
    });
    document.title = `${characterName} 対戦データ | ON PLA!`;
}

function renderSummary(battles, matchups) {
    const records = battles.map(getCharacterSide);
    const wins = records.filter(record => record.player.result === "win").length;
    const topMatchup = [...matchups].sort((a, b) => b.battles - a.battles)[0];
    const opponent = topMatchup
        ? (topMatchup.pokemon1.id === characterId ? topMatchup.pokemon2 : topMatchup.pokemon1)
        : null;

    document.getElementById("character-win-rate").textContent = `${formatRate(wins, records.length)}%`;
    document.getElementById("character-record").textContent = `${wins}勝 ${records.length - wins}敗`;
    document.getElementById("character-battles").textContent = `${records.length}試合`;
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

function renderMatchups(matchups) {
    const sorted = [...matchups].sort((a, b) => b.battles - a.battles);
    document.getElementById("character-matchups").innerHTML = sorted.length ? sorted.map(matchup => {
        const characterIsFirst = matchup.pokemon1.id === characterId;
        const opponent = characterIsFirst ? matchup.pokemon2 : matchup.pokemon1;
        const characterWins = characterIsFirst ? matchup.pokemon1_wins : matchup.pokemon2_wins;
        const opponentWins = matchup.battles - characterWins;
        const rate = characterIsFirst ? matchup.pokemon1_win_rate : matchup.pokemon2_win_rate;
        const opponentRate = 100 - rate;
        return `<article class="character-matchup-row"><div class="character-matchup-main"><div>${getIdentityHTML(opponent.name, opponent.id)}<span class="matchup-types">${getTypeHTML([opponent.type1, opponent.type2])}</span></div><span class="matchup-count">${matchup.battles}戦</span><strong>${formatPercentage(rate)}%</strong></div><div class="detail-meter matchup-meter-large"><span style="width:${rate}%"></span></div><div class="character-matchup-foot"><span>${characterName} ${characterWins}勝</span><span>${opponent.name} ${opponentWins}勝（${formatPercentage(opponentRate)}%）</span></div></article>`;
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

async function main() {
    try {
        const [emoji, battleData, matchupData] = await Promise.all([
            loadJSON("emoji.json", ".."),
            loadJSON("battles.json"),
            loadJSON("matchups.json")
        ]);
        emojiData = emoji;
        setCharacterNames();

        const battles = battleData.battles.filter(battle =>
            battle.player1.pokemon_id === characterId || battle.player2.pokemon_id === characterId
        );
        const matchups = matchupData.matchups.filter(matchup =>
            matchup.pokemon1.id === characterId || matchup.pokemon2.id === characterId
        );

        renderSummary(battles, matchups);
        renderOrderStats(battles);
        renderMatchups(matchups);
        renderRecentBattles(battles);
    } catch (error) {
        console.error(error);
        document.querySelector("main").innerHTML = "<p class=\"load-error\">データを読み込めませんでした。</p>";
    }
}

main();

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
let playerId = null;


// =========================
// JSON読み込み
// =========================

async function loadJSON(file, season) {

    const response = await fetch(
        `${DATA_PATH}/${season}/${file}`
    );

    if (!response.ok) {
        throw new Error(`${file}の読み込みに失敗しました`);
    }

    return response.json();

}


async function loadEmojiData() {

    const response =
        await fetch(`${ASSET_PATH}emoji.json`);

    if (!response.ok) {
        throw new Error("emoji.jsonの読み込みに失敗");
    }

    emojiData = await response.json();

}


// =========================
// ヘルパー関数
// =========================

function getEmoji(pokemonId) {
    return emojiData?.pokemon?.find(
        pokemon => pokemon.id === pokemonId
    );
}


function getIdentityHTML(name, pokemonId, className = "") {

    const emoji = getEmoji(pokemonId);

    const imageMatch = emoji?.emoji?.match(
        /<:([^:]+):(\d+)>/
    );

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


function formatRate(wins, battles) {
    return battles ? (wins / battles * 100).toFixed(1) : "0.0";
}


function formatPercentage(value) {
    return Number(value || 0).toFixed(1);
}


// =========================
// このプレイヤー視点での自分/相手を取り出す
// =========================

function getPlayerSide(battle) {

    if (battle.player1.id === playerId) {
        return {
            battle,
            self: battle.player1,
            opponent: battle.player2,
            isFirst: battle.first_player === 1
        };
    }

    return {
        battle,
        self: battle.player2,
        opponent: battle.player1,
        isFirst: battle.first_player === 2
    };

}


function setPlayerName(name) {

    document.querySelectorAll("span[data-player-name]").forEach(element => {
        element.textContent = name;
    });

    document.title = `${name} 対戦データ | ON PLA!`;

}


// =========================
// 総合戦績
// =========================

function renderSummary(records) {

    const wins =
        records.filter(record => record.self.result === "win").length;

    const usageMap = new Map();

    records.forEach(record => {

        const key = record.self.pokemon_id;

        if (!usageMap.has(key)) {
            usageMap.set(key, {
                pokemon_id: record.self.pokemon_id,
                name: record.self.pokemon,
                count: 0
            });
        }

        usageMap.get(key).count += 1;

    });

    const topPokemon =
        [...usageMap.values()]
            .sort((a, b) => b.count - a.count)[0] || null;

    document.getElementById("player-win-rate").textContent =
        `${formatRate(wins, records.length)}%`;

    document.getElementById("player-record").textContent =
        `${wins}勝 ${records.length - wins}敗`;

    document.getElementById("player-battles").textContent =
        `${records.length}試合`;

    document.getElementById("player-top-pokemon").innerHTML =
        topPokemon ? getIdentityHTML(topPokemon.name, topPokemon.pokemon_id) : "-";

    document.getElementById("player-top-pokemon-note").textContent =
        topPokemon ? `${topPokemon.count}戦使用` : "-";

    document.getElementById("player-identity").innerHTML =
        `<span class="player-avatar">🧑</span>`;

}


// =========================
// 先攻・後攻の戦績
// =========================

function renderOrderStats(records) {

    const groups = [
        { label: "先攻", records: records.filter(record => record.isFirst) },
        { label: "後攻", records: records.filter(record => !record.isFirst) }
    ];

    document.getElementById("player-order-stats").innerHTML =
        groups.map(group => {

            const wins =
                group.records.filter(record => record.self.result === "win").length;

            const rate = formatRate(wins, group.records.length);

            return `<div class="order-stat"><div class="order-stat-top"><strong>${group.label}</strong><b>${rate}%</b></div><div class="detail-meter"><span style="width:${rate}%"></span></div><span>${group.records.length}戦 ${wins}勝 ${group.records.length - wins}敗</span></div>`;

        }).join("");

}


// =========================
// 使用ポケモン別成績
// =========================

function renderPokemonUsage(records) {

    const usageMap = new Map();

    records.forEach(record => {

        const key = record.self.pokemon_id;

        if (!usageMap.has(key)) {
            usageMap.set(key, {
                pokemon_id: record.self.pokemon_id,
                name: record.self.pokemon,
                type1: record.self.type1,
                type2: record.self.type2,
                battles: 0,
                wins: 0
            });
        }

        const entry = usageMap.get(key);

        entry.battles += 1;

        if (record.self.result === "win") {
            entry.wins += 1;
        }

    });

    const sorted =
        [...usageMap.values()]
            .sort((a, b) => b.battles - a.battles);

    document.getElementById("player-pokemon-usage").innerHTML =
        sorted.length
            ? sorted.map(entry => {

                const rate = formatRate(entry.wins, entry.battles);
                const losses = entry.battles - entry.wins;

                return `<article class="character-matchup-row"><div class="character-matchup-main"><div>${getIdentityHTML(entry.name, entry.pokemon_id)}<span class="matchup-types">${getTypeHTML([entry.type1, entry.type2])}</span></div><span class="matchup-count">${entry.battles}戦</span><strong>${formatPercentage(rate)}%</strong></div><div class="detail-meter matchup-meter-large"><span style="width:${rate}%"></span></div><div class="character-matchup-foot"><span>${entry.wins}勝</span><span>${losses}敗</span></div></article>`;

            }).join("")
            : "<p>対戦データがありません。</p>";

}


// =========================
// 直近の試合
// =========================

function formatBattleEntry(record) {

    const { battle, self, opponent, isFirst } = record;

    const sideHTML = (side, isSideFirst) => `
        <div class="recent-battle-player ${side.result === "win" ? "is-win" : "is-lose"}">
            <span class="recent-battle-result">${side.result === "win" ? "WIN" : "LOSE"}</span>
            ${side.name ? `<span class="recent-battle-name">${side.name}</span>` : ""}
            ${getIdentityHTML(side.pokemon, side.pokemon_id)}
            <span class="pokemon-type">${getTypeHTML([side.type1, side.type2])}</span>
            <span class="recent-battle-order">${isSideFirst ? "先攻" : "後攻"}</span>
        </div>`;

    return `
        <div class="recent-battle-entry">
            <div class="recent-battle-number">第${battle.id}戦</div>
            <div class="recent-battle-players">
                ${sideHTML(self, isFirst)}
                <span class="vs">VS</span>
                ${sideHTML(opponent, !isFirst)}
            </div>
        </div>`;

}


function renderRecentBattles(records) {

    const recent =
        [...records]
            .sort((a, b) => b.battle.id - a.battle.id)
            .slice(0, 8);

    document.getElementById("player-recent-battles").innerHTML =
        recent.length
            ? recent.map(formatBattleEntry).join("")
            : "<p>試合データがありません。</p>";

}


// =========================
// メイン
// =========================

async function loadAndRender(season) {

    const battleData =
        await loadJSON("battles.json", season);

    const myBattles =
        battleData.battles.filter(battle =>
            battle.player1.id === playerId ||
            battle.player2.id === playerId
        );

    const records =
        myBattles.map(getPlayerSide);

    const name =
        records.length
            ? records[0].self.name
            : "プレイヤー";

    setPlayerName(name);
    renderSummary(records);
    renderOrderStats(records);
    renderPokemonUsage(records);
    renderRecentBattles(records);

}


async function main() {

    playerId = Number(
        new URLSearchParams(location.search).get("id")
    );

    if (!playerId) {
        document.querySelector("main").innerHTML =
            "<p class=\"load-error\">プレイヤーが指定されていません。</p>";
        return;
    }

    try {

        await loadEmojiData();

        const seasonSelect =
            document.getElementById("season-select");

        const initialSeason =
            seasonSelect ? seasonSelect.value : "all";

        await loadAndRender(initialSeason);

        if (seasonSelect) {

            seasonSelect.addEventListener(
                "change",
                () => loadAndRender(seasonSelect.value)
            );

        }

    } catch (error) {

        console.error(error);

        document.querySelector("main").innerHTML =
            "<p class=\"load-error\">データを読み込めませんでした。</p>";

    }

}


main();

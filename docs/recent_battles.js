// =========================
// グローバル変数
// =========================

let emojiData = null;

const characterPagePaths = {
    9: "characters/kairos.html"
};

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


// =========================
// JSON読み込み
// =========================

async function loadJSON(file) {

    const response = await fetch(
        `data/${file}`
    );

    return await response.json();
}


// =========================
// emoji.json読み込み
// =========================

async function loadEmojiData() {

    try {

        const response =
            await fetch("emoji.json");

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
                    src="emoji_images/${match[2]}.webp"
                    alt="${name}"
                    class="pokemon-emoji"
                    onerror="this.style.display='none'"
                >
            `;
        }
    }

    const identityHTML = `
        ${pokemonImage}
        <span>${name}</span>
    `;

    const detailPath = characterPagePaths[pokemonId];

    return detailPath
        ? `<a class="pokemon-detail-link" href="${detailPath}">${identityHTML}</a>`
        : identityHTML;

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
                        src="type_images/${imageName}.webp"
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

    const player1First = battle.first_player === 1;

    const playerHTML = (player, isFirst) => `
        <div class="recent-battle-player ${player.result === "win" ? "is-win" : "is-lose"}">
            <span class="recent-battle-result">${player.result === "win" ? "WIN" : "LOSE"}</span>
            ${player.name ? `<span class="recent-battle-name">${player.name}</span>` : ""}
            ${getPokemonIdentityHTML(player.pokemon, player.pokemon_id)}
            <span class="pokemon-type">${getTypeImagesHTML([player.type1, player.type2].filter(Boolean))}</span>
            <span class="recent-battle-order">${isFirst ? "先攻" : "後攻"}</span>
        </div>`;

    return `
        <div class="recent-battle-entry">
            <div class="recent-battle-number">第${battle.id}戦</div>
            <div class="recent-battle-players">
                ${playerHTML(battle.player1, player1First)}
                <span class="vs">VS</span>
                ${playerHTML(battle.player2, !player1First)}
            </div>
        </div>`;

}


// =========================
// メイン
// =========================

async function main() {

    await loadEmojiData();

    const battlesData =
        await loadJSON("battles.json");

    const recent =
        [...battlesData.battles]
            .sort((a, b) => b.id - a.id)
            .slice(0, 30);

    const container =
        document.getElementById(
            "battle-history-list"
        );

    container.innerHTML =
        recent
            .map(battle => formatBattleEntry(battle))
            .join("") ||
        "<p>試合データがありません。</p>";

}


main();

const DATA_PATH = "../data";


// =========================
// JSON読み込み
// =========================

async function loadPlayers() {

    const response =
        await fetch(`${DATA_PATH}/players.json`);

    if (!response.ok) {
        throw new Error("players.jsonの読み込みに失敗しました");
    }

    return response.json();

}


async function loadJSON(path) {

    const response = await fetch(`${DATA_PATH}/${path}`);

    if (!response.ok) {
        throw new Error(`${path}の読み込みに失敗しました`);
    }

    return response.json();

}


// =========================
// 表示
// =========================

function renderPlayerList(players, battles, pokemonData) {

    const container =
        document.getElementById("player-list");

    const usageByPlayer = new Map();

    battles.forEach(battle => {
        [battle.player1, battle.player2].forEach(side => {
            if (side.id == null) {
                return;
            }

            if (!usageByPlayer.has(side.id)) {
                usageByPlayer.set(side.id, new Map());
            }

            const usage = usageByPlayer.get(side.id);
            const pokemon = usage.get(side.pokemon_id) || {
                id: side.pokemon_id,
                name: side.pokemon,
                count: 0
            };
            pokemon.count += 1;
            usage.set(side.pokemon_id, pokemon);
        });
    });

    // あいうえお順（日本語ロケールでの文字列比較）
    const sorted =
        [...players].sort((a, b) =>
            a.name.localeCompare(b.name, "ja")
        );

    container.innerHTML =
        sorted.length
            ? sorted.map(player => {
                const topPokemon = [...(usageByPlayer.get(player.id)?.values() || [])]
                    .sort((a, b) => b.count - a.count)[0];
                const emoji = pokemonData.find(pokemon => pokemon.id === topPokemon?.id)?.emoji;
                const imageMatch = emoji?.match(/<:([^:]+):(\d+)>/);
                const avatar = imageMatch
                    ? `<img src="../assets/images/emoji_images/${imageMatch[2]}.webp" alt="${topPokemon.name}" loading="lazy">`
                    : "🧑";

                return `
                    <a class="player-card" href="player.html?id=${player.id}">
                        <span class="player-card-avatar">${avatar}</span>
                        <span class="player-card-name">${player.name}</span>
                    </a>
                `;
            }).join("")
            : "<p>登録されているプレイヤーがいません。</p>";

}


// =========================
// メイン
// =========================

async function main() {

    try {

        const [data, battleData, emojiData] = await Promise.all([
            loadPlayers(),
            loadJSON("season_2/battles.json"),
            loadJSON("emoji.json")
        ]);

        renderPlayerList(data.players, battleData.battles, emojiData.pokemon);

    } catch (error) {

        console.error(error);

        document.getElementById("player-list").innerHTML =
            "<p class=\"load-error\">データを読み込めませんでした。</p>";

    }

}


main();
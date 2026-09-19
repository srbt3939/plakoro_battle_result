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


// =========================
// 表示
// =========================

function renderPlayerList(players) {

    const container =
        document.getElementById("player-list");

    // あいうえお順（日本語ロケールでの文字列比較）
    const sorted =
        [...players].sort((a, b) =>
            a.name.localeCompare(b.name, "ja")
        );

    container.innerHTML =
        sorted.length
            ? sorted.map(player => `
                <a class="player-index-row" href="player.html?id=${player.id}">
                    ${player.name}
                </a>
            `).join("")
            : "<p>登録されているプレイヤーがいません。</p>";

}


// =========================
// メイン
// =========================

async function main() {

    try {

        const data = await loadPlayers();

        renderPlayerList(data.players);

    } catch (error) {

        console.error(error);

        document.getElementById("player-list").innerHTML =
            "<p class=\"load-error\">データを読み込めませんでした。</p>";

    }

}


main();
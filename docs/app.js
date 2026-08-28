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
// メイン
// =========================

async function main() {

    const usage =
        await loadJSON("usage.json");

    const stats =
        await loadJSON("pokemon_stats.json");

    const matchups =
        await loadJSON("matchups.json");

    const battles =
        await loadJSON("battles.json");


    // =========================
    // 概要
    // =========================

    const totalBattles =
        battles.battles.length;


    document.getElementById(
        "total-battles"
    ).textContent =
        `${totalBattles}試合`;


    // document.getElementById(
    //     "total-usage"
    // ).textContent =
    //     usage.total_usage;


    document.getElementById(
        "pokemon-count"
    ).textContent =
        usage.pokemon.length;


    // =========================
    // 使用率円グラフ
    // =========================

    createUsageChart(
        usage.pokemon
    );


    // =========================
    // 使用率ランキング
    // =========================

    createUsageRanking(
        usage.pokemon
    );


    // =========================
    // 勝率ランキング
    // =========================

    createWinRateRanking(
        stats.pokemon
    );


    // =========================
    // 先攻・後攻
    // =========================

    createFirstSecondRanking(
        stats.pokemon
    );


    // =========================
    // 対戦成績
    // =========================

    createMatchups(
        matchups.matchups
    );

}


// =========================
// 使用率円グラフ
// =========================

function createUsageChart(pokemon) {

    const labels =
        pokemon.map(
            p => p.name
        );


    const data =
        pokemon.map(
            p => p.usage_count
        );


    const ctx =
        document
            .getElementById(
                "usage-chart"
            );


    new Chart(ctx, {

        type: "pie",

        data: {

            labels: labels,

            datasets: [

                {
                    data: data
                }

            ]

        },

        options: {

            responsive: true,

            plugins: {

                legend: {

                    position: "right"

                },

                tooltip: {

                    callbacks: {

                        label:
                            function(context) {

                                const total =
                                    context.dataset
                                        .data
                                        .reduce(
                                            (a, b) =>
                                                a + b,
                                            0
                                        );

                                const value =
                                    context.raw;

                                const rate =
                                    value /
                                    total *
                                    100;

                                return (
                                    context.label +
                                    ": " +
                                    value +
                                    "回 (" +
                                    rate.toFixed(1) +
                                    "%)"
                                );

                            }

                    }

                }

            }

        }

    });

}


// =========================
// 使用率ランキング
// =========================

function createUsageRanking(pokemon) {

    const container =
        document.getElementById(
            "usage-ranking"
        );


    container.innerHTML = `

        <div class="ranking-row">

            <strong>順位</strong>
            <strong>ポケモン</strong>
            <strong class="number">
                使用数
            </strong>
            <strong class="number">
                使用率
            </strong>

        </div>

    `;


    pokemon.forEach((p, index) => {

        container.innerHTML += `

            <div class="ranking-row">

                <span class="rank">
                    ${index + 1}
                </span>

                <span class="pokemon-name">
                    ${p.name}
                </span>

                <span class="number">
                    ${p.usage_count}
                </span>

                <span class="number">
                    ${p.usage_rate}%
                </span>

            </div>

        `;

    });

}


// =========================
// 勝率ランキング
// =========================

function createWinRateRanking(stats) {

    const container =
        document.getElementById(
            "winrate-ranking"
        );


    // 最低5試合以上に限定
    const ranking =
        stats
            .filter(
                p => p.total.battles >= 5
            )
            .sort(
                (a, b) =>
                    b.total.win_rate -
                    a.total.win_rate
            );


    container.innerHTML = `

        <div class="ranking-row">

            <strong>順位</strong>
            <strong>ポケモン</strong>
            <strong class="number">
                試合
            </strong>
            <strong class="number">
                勝率
            </strong>

        </div>

    `;


    ranking.forEach((p, index) => {

        container.innerHTML += `

            <div class="ranking-row">

                <span class="rank">
                    ${index + 1}
                </span>

                <span class="pokemon-name">
                    ${p.name}
                </span>

                <span class="number">
                    ${p.total.battles}
                </span>

                <span class="number">
                    ${p.total.win_rate}%
                </span>

            </div>

        `;

    });

}


// =========================
// 先攻・後攻ランキング
// =========================

function createFirstSecondRanking(stats) {

    const container =
        document.getElementById(
            "first-second-ranking"
        );


    container.innerHTML = `

        <div class="first-second-row header">

            <strong>
                ポケモン
            </strong>

            <strong>
                先攻
            </strong>

            <strong>
                後攻
            </strong>

            <strong>
                不明
            </strong>

        </div>

    `;


    stats.forEach(p => {

        container.innerHTML += `

            <div class="first-second-row">

                <span class="pokemon-name">
                    ${p.name}
                </span>

                <span>
                    ${p.first_player.battles}戦
                    ${p.first_player.win_rate}%
                </span>

                <span>
                    ${p.second_player.battles}戦
                    ${p.second_player.win_rate}%
                </span>

                <span>
                    ${p.unknown.battles}戦
                </span>

            </div>

        `;

    });

}


// =========================
// 対戦成績
// =========================

function createMatchups(matchups) {

    const container =
        document.getElementById(
            "matchup-list"
        );


    // 対戦数が多いものを表示
    const ranking =
        matchups
            .filter(
                m => m.battles >= 2
            )
            .slice(0, 30);


    ranking.forEach(m => {

        container.innerHTML += `

            <div class="matchup-row">

                <span class="matchup-pokemon">

                    ${m.pokemon1.name}

                    <br>

                    ${m.pokemon1_win_rate}%

                </span>


                <span class="vs">

                    VS

                    <br>

                    ${m.battles}戦

                </span>


                <span class="matchup-pokemon">

                    ${m.pokemon2.name}

                    <br>

                    ${m.pokemon2_win_rate}%

                </span>

            </div>

        `;

    });

}


// =========================
// 実行
// =========================

main();
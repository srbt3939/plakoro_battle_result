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
    // 使用率・勝率ランキング
    // =========================

    createPokemonRanking(
        usage.pokemon,
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

    setupMatchupFilter(
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

// function createUsageRanking(pokemon) {

//     const container =
//         document.getElementById(
//             "usage-ranking"
//         );


//     container.innerHTML = `

//         <div class="ranking-row">

//             <strong>順位</strong>
//             <strong>ポケモン</strong>
//             <strong class="number">
//                 使用数
//             </strong>
//             <strong class="number">
//                 使用率
//             </strong>

//         </div>

//     `;


//     pokemon.forEach((p, index) => {

//         container.innerHTML += `

//             <div class="ranking-row">

//                 <span class="rank">
//                     ${index + 1}
//                 </span>

//                 <span class="pokemon-name">
//                     ${p.name}
//                 </span>

//                 <span class="number">
//                     ${p.usage_count}
//                 </span>

//                 <span class="number">
//                     ${p.usage_rate}%
//                 </span>

//             </div>

//         `;

//     });

// }


// // =========================
// // 勝率ランキング
// // =========================

// function createWinRateRanking(stats) {

//     const container =
//         document.getElementById(
//             "winrate-ranking"
//         );


//     // 最低5試合以上に限定
//     const ranking =
//         stats
//             .filter(
//                 p => p.total.battles >= 5
//             )
//             .sort(
//                 (a, b) =>
//                     b.total.win_rate -
//                     a.total.win_rate
//             );


//     container.innerHTML = `

//         <div class="ranking-row">

//             <strong>順位</strong>
//             <strong>ポケモン</strong>
//             <strong class="number">
//                 試合
//             </strong>
//             <strong class="number">
//                 勝率
//             </strong>

//         </div>

//     `;


//     ranking.forEach((p, index) => {

//         container.innerHTML += `

//             <div class="ranking-row">

//                 <span class="rank">
//                     ${index + 1}
//                 </span>

//                 <span class="pokemon-name">
//                     ${p.name}
//                 </span>

//                 <span class="number">
//                     ${p.total.battles}
//                 </span>

//                 <span class="number">
//                     ${p.total.win_rate}%
//                 </span>

//             </div>

//         `;

//     });

// }

// =========================
// ポケモンランキング
// =========================

let pokemonRankingData = [];


// 現在のソート項目
let currentSortKey = "usage_rate";

// 現在のソート方向
let currentSortOrder = "desc";


// =========================
// ランキング作成
// =========================

function createPokemonRanking(
    usage,
    stats
) {

    const ranking = usage.map(p => {

        const stat =
            stats.find(
                s =>
                    s.pokemon_id ===
                    p.pokemon_id
            );


        return {

            pokemon_id:
                p.pokemon_id,

            name:
                p.name,

            usage_count:
                p.usage_count,

            usage_rate:
                p.usage_rate,

            battles:
                stat
                    ? stat.total.battles
                    : 0,

            win_rate:
                stat
                    ? stat.total.win_rate
                    : 0

        };

    });


    pokemonRankingData =
        ranking;


    // 初期表示
    renderPokemonRanking();

}


// =========================
// ソート変更
// =========================

function changeRankingSort(
    sortKey
) {

    // 同じ項目をクリックした場合
    // 昇順・降順を反転
    if (
        currentSortKey ===
        sortKey
    ) {

        currentSortOrder =
            currentSortOrder ===
            "desc"
                ? "asc"
                : "desc";

    }

    // 別の項目をクリックした場合
    else {

        currentSortKey =
            sortKey;

        // 新しい項目は降順から
        currentSortOrder =
            "desc";

    }


    renderPokemonRanking();

}


// =========================
// ランキング表示
// =========================

function renderPokemonRanking() {

    const container =
        document.getElementById(
            "pokemon-ranking"
        );


    // =========================
    // ソート
    // =========================

    const ranking =
        [...pokemonRankingData]
            .filter(p => {

                if (
                    currentSortKey ===
                    "win_rate"
                ) {

                    return p.battles >= 5;

                }

                return true;

            })
            .sort((a, b) => {

                const valueA =
                    a[currentSortKey];

                const valueB =
                    b[currentSortKey];


                if (
                    currentSortOrder ===
                    "desc"
                ) {

                    return valueB - valueA;

                }

                else {

                    return valueA - valueB;

                }

            });


    // =========================
    // 矢印
    // =========================

    const usageArrow =
        currentSortKey ===
        "usage_rate"

            ? (
                currentSortOrder ===
                "desc"
                    ? " ↓"
                    : " ↑"
            )

            : "";


    const winRateArrow =
        currentSortKey ===
        "win_rate"

            ? (
                currentSortOrder ===
                "desc"
                    ? " ↓"
                    : " ↑"
            )

            : "";


    // =========================
    // ヘッダー
    // =========================

    container.innerHTML = `

        <div class="pokemon-ranking-row header">

            <span class="rank">
                順位
            </span>


            <span class="pokemon-name">
                ポケモン
            </span>


            <span class="number">
                使用数
            </span>


            <button
                class="sort-button"
                onclick="
                    changeRankingSort(
                        'usage_rate'
                    )
                "
            >
                使用率${usageArrow}
            </button>


            <button
                class="sort-button"
                onclick="
                    changeRankingSort(
                        'win_rate'
                    )
                "
            >
                勝率${winRateArrow}
            </button>

        </div>


        <div class="ranking-note">

            ※ 勝率は5試合以上のポケモンを対象

        </div>

    `;


    // =========================
    // データ表示
    // =========================

    ranking.forEach(
        (p, index) => {

            // 勝率ソート時は
            // 5試合未満を除外
            if (
                currentSortKey ===
                    "win_rate" &&
                p.battles < 5
            ) {

                return;

            }


            container.innerHTML += `

                <div class="pokemon-ranking-row">

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


                    <span class="number">

                        ${
                            p.battles >= 5
                                ? p.win_rate + "%"
                                : "-"
                        }

                    </span>

                </div>

            `;

        }
    );

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
                    ${p.first_player.wins}勝
                    <br>
                    ${p.first_player.win_rate}%
                </span>


                <span>
                    ${p.second_player.battles}戦
                    ${p.second_player.wins}勝
                    <br>
                    ${p.second_player.win_rate}%
                </span>


                <span>
                    ${p.unknown.battles}戦
                    ${p.unknown.wins}勝
                    <br>
                    ${p.unknown.win_rate}%
                </span>

            </div>

        `;

    });

}


// =========================
// 対戦成績フィルター
// =========================

function getPokemonId(pokemon) {

    return pokemon?.pokemon_id ?? pokemon?.id;

}


function setupMatchupFilter(matchups) {

    const select =
        document.getElementById(
            "matchup-pokemon"
        );


    // 登場するポケモンを取得
    const pokemonMap = new Map();


    matchups.forEach(m => {

        pokemonMap.set(
            getPokemonId(m.pokemon1),
            m.pokemon1.name
        );

        pokemonMap.set(
            getPokemonId(m.pokemon2),
            m.pokemon2.name
        );

    });


    // ポケモン名順
    const pokemonList =
        Array.from(
            pokemonMap.entries()
        ).sort(
            (a, b) =>
                a[1].localeCompare(
                    b[1],
                    "ja"
                )
        );


    pokemonList.forEach(
        ([id, name]) => {

            const option =
                document.createElement(
                    "option"
                );

            option.value = id;

            option.textContent = name;

            select.appendChild(
                option
            );

        }
    );


    // 選択変更時
    select.addEventListener(
        "change",
        function() {

            createMatchups(
                matchups,
                this.value
            );

        }
    );

}

// =========================
// 対戦成績
// =========================

function createMatchups(
    matchups,
    selectedPokemonId = ""
) {

    const container =
        document.getElementById(
            "matchup-list"
        );


    container.innerHTML = "";


    let filtered =
        matchups;


    // =========================
    // フィルター
    // =========================

    if (selectedPokemonId !== "") {

        const id =
            Number(
                selectedPokemonId
            );


        filtered =
            matchups.filter(m =>

                getPokemonId(m.pokemon1) === id ||
                getPokemonId(m.pokemon2) === id

            );

    }


    // =========================
    // 対戦数が多い順
    // =========================

    filtered =
        [...filtered].sort(
            (a, b) =>
                b.battles -
                a.battles
        );


    // =========================
    // 表示
    // =========================

    filtered.forEach(m => {

        let pokemon1 =
            m.pokemon1;

        let pokemon2 =
            m.pokemon2;


        let pokemon1WinRate =
            m.pokemon1_win_rate;

        let pokemon2WinRate =
            m.pokemon2_win_rate;


        // =========================
        // 選択されたポケモンを
        // 左側に固定
        // =========================

        if (
            selectedPokemonId !== "" &&
            String(getPokemonId(pokemon2)) ===
                selectedPokemonId
        ) {

            pokemon1 =
                m.pokemon2;

            pokemon2 =
                m.pokemon1;

            pokemon1WinRate =
                m.pokemon2_win_rate;

            pokemon2WinRate =
                m.pokemon1_win_rate;

        }


        container.innerHTML += `

            <div class="matchup-row">

                <span class="matchup-pokemon">

                    ${pokemon1.name}

                    <br>

                    ${pokemon1WinRate}%

                </span>


                <span class="vs">

                    VS

                    <br>

                    ${m.battles}戦

                </span>


                <span class="matchup-pokemon">

                    ${pokemon2.name}

                    <br>

                    ${pokemon2WinRate}%

                </span>

            </div>

        `;

    });


    // =========================
    // 該当データなし
    // =========================

    if (filtered.length === 0) {

        container.innerHTML = `

            <p>
                対戦データがありません。
            </p>

        `;

    }

}


// =========================
// 実行
// =========================

main();

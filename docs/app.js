// =========================
// グローバル変数
// =========================

let emojiData = null;

const typeImageNames = {
    "あく": "dark",
    "かみなり": "electric",
    "でんき": "electric",
    "くさ": "grass",
    "ちょう": "psychic",
    "ほのお": "fire",
    "みず": "water",
    "はがね": "steel",
    "とう": "steel",
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
// メイン
// =========================

async function main() {

    // emoji.json読み込み
    await loadEmojiData();

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

    createFirstSecondOverall(
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
                    : 0,

            type1:
                stat?.type1,

            type2:
                stat?.type2,

            weaknesses:
                stat?.weaknesses || [],

            type_sort:
                [stat?.type1, stat?.type2]
                    .filter(Boolean)
                    .join("/"),

            weakness_sort:
                (stat?.weaknesses || [])
                    .join("/")

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


                const isTextSort =
                    currentSortKey === "type_sort" ||
                    currentSortKey === "weakness_sort";

                const comparison = isTextSort
                    ? String(valueA).localeCompare(
                        String(valueB),
                        "ja"
                    )
                    : valueA - valueB;

                return currentSortOrder === "desc"
                    ? -comparison
                    : comparison;

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
                    ? " ▼"
                    : " ▲"
            )

            : "▽";


    const winRateArrow =
        currentSortKey ===
        "win_rate"

            ? (
                currentSortOrder ===
                "desc"
                    ? " ▼"
                    : " ▲"
            )

            : "▽";


    const typeArrow =
        currentSortKey === "type_sort"
            ? (
                currentSortOrder === "desc"
                    ? " ▼"
                    : " ▲"
            )
            : "▽";


    const weaknessArrow =
        currentSortKey === "weakness_sort"
            ? (
                currentSortOrder === "desc"
                    ? " ▼"
                    : " ▲"
            )
            : "▽";


    // =========================
    // ヘッダー
    // =========================

    container.innerHTML = `

        <div class="pokemon-ranking-row header">

            <span class="rank">
                順位
            </span>


            <span class="pokemon-name">
                <span>ポケモン</span>

                <button
                    class="sort-button sort-meta-button"
                    onclick="
                        changeRankingSort(
                            'type_sort'
                        )
                    "
                >
                    タイプ${typeArrow}
                </button>

                <button
                    class="sort-button sort-meta-button"
                    onclick="
                        changeRankingSort(
                            'weakness_sort'
                        )
                    "
                >
                    弱点${weaknessArrow}
                </button>
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

                        ${getPokemonDisplayHTML(
                            p.name,
                            p.pokemon_id,
                            p
                        )}

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
                    ${getPokemonDisplayHTML(
                        p.name,
                        p.pokemon_id,
                        p
                    )}
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
// 全体の先攻・後攻勝率
// =========================

function createFirstSecondOverall(stats) {

    const totals = stats.reduce(
        (result, pokemon) => {

            result.first.battles += pokemon.first_player.battles;
            result.first.wins += pokemon.first_player.wins;
            result.second.battles += pokemon.second_player.battles;
            result.second.wins += pokemon.second_player.wins;

            return result;

        },
        {
            first: { battles: 0, wins: 0 },
            second: { battles: 0, wins: 0 }
        }
    );


    const firstWinRate = totals.first.battles > 0
        ? totals.first.wins / totals.first.battles * 100
        : 0;

    const secondWinRate = totals.second.battles > 0
        ? totals.second.wins / totals.second.battles * 100
        : 0;

    const container = document.getElementById(
        "first-second-overall"
    );


    container.innerHTML = `

        <div class="matchup-row first-second-overall">

            <span class="matchup-pokemon">
                先攻
                <br>
                <span class="pokemon-rate">
                    ${firstWinRate.toFixed(1)}%
                </span>
                <br>
                <span class="pokemon-rate">
                    ${totals.first.battles}戦 ${totals.first.wins}勝
                </span>
            </span>

            <span class="vs">
                VS
            </span>

            <span class="matchup-pokemon">
                後攻
                <br>
                <span class="pokemon-rate">
                    ${secondWinRate.toFixed(1)}%
                </span>
                <br>
                <span class="pokemon-rate">
                    ${totals.second.battles}戦 ${totals.second.wins}勝
                </span>
            </span>

            <div
                class="matchup-meter"
                role="img"
                aria-label="先攻 ${firstWinRate.toFixed(1)}%、後攻 ${secondWinRate.toFixed(1)}%"
            >
                <span
                    class="matchup-meter-pokemon1"
                    style="width: ${firstWinRate}%"
                ></span>
                <span
                    class="matchup-meter-pokemon2"
                    style="width: ${secondWinRate}%"
                ></span>
            </div>

        </div>

        <p class="first-second-note">
            「不明」を除外して集計
        </p>

    `;

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

function getPokemonDisplayHTML(name, pokemonId, details = {}) {

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

    const types = [details.type1, details.type2]
        .filter(Boolean);

    const weaknesses = Array.isArray(details.weaknesses)
        ? details.weaknesses
        : [];

    const typeHTML = getTypeImagesHTML(types);
    const weaknessHTML = getTypeImagesHTML(weaknesses);

    return `
        ${pokemonImage}
        <span>${name}</span>
        ${typeHTML ? `<span class="pokemon-meta"><span class="pokemon-meta-label">タイプ:</span>${typeHTML}</span>` : ""}
        ${weaknessHTML ? `<span class="pokemon-meta"><span class="pokemon-meta-label">弱点:</span>${weaknessHTML}</span>` : ""}
    `;

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

                    <span class="pokemon-name-bold">${getPokemonDisplayHTML(
                        pokemon1.name,
                        pokemon1.id,
                        pokemon1
                    )}</span>

                    <br>

                    <span class="pokemon-rate">${pokemon1WinRate}%</span>

                </span>


                <span class="vs">

                    VS

                    <br>

                    ${m.battles}戦

                </span>


                <span class="matchup-pokemon">

                    <span class="pokemon-name-bold">${getPokemonDisplayHTML(
                        pokemon2.name,
                        pokemon2.id,
                        pokemon2
                    )}</span>

                    <br>

                    <span class="pokemon-rate">${pokemon2WinRate}%</span>

                </span>


                <div
                    class="matchup-meter"
                    role="img"
                    aria-label="${pokemon1.name} ${pokemon1WinRate}%、${pokemon2.name} ${pokemon2WinRate}%"
                >

                    <span
                        class="matchup-meter-pokemon1"
                        style="width: ${pokemon1WinRate}%"
                    ></span>

                    <span
                        class="matchup-meter-pokemon2"
                        style="width: ${pokemon2WinRate}%"
                    ></span>

                </div>

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

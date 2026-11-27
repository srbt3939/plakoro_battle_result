const DATA_VERSION = "aws-v1";

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    })[ch]);
}

async function loadEmojiData() {
    const response = await fetch(`../data/emoji.json?v=${DATA_VERSION}`);
    if (!response.ok) return { pokemon: [] };
    return response.json();
}

function renderPlayerList(players, pokemonData) {
    const container = document.getElementById("player-list");
    // Sort names in browser via localeCompare("ja") per Q6
    const sorted = [...players].sort((a, b) => a.name.localeCompare(b.name, "ja"));

    container.innerHTML = sorted.length
        ? sorted.map(player => {
            const topPokemon = player.top_pokemon;
            const emoji = pokemonData.find(p => p.id === topPokemon?.pokemon_id)?.emoji;
            const imageMatch = emoji?.match(/<:([^:]+):(\d+)>/);
            const avatar = imageMatch
                ? `<img src="../assets/images/emoji_images/${imageMatch[2]}.webp" alt="${escapeHTML(topPokemon.name)}" loading="lazy">`
                : "🧑";
            return `
                <a class="player-card" href="player.html?id=${encodeURIComponent(player.id)}">
                    <span class="player-card-avatar">${avatar}</span>
                    <span class="player-card-name">${escapeHTML(player.name)}</span>
                </a>
            `;
        }).join("")
        : "<p>登録されているプレイヤーがいません。</p>";
}

async function main() {
    const container = document.getElementById("player-list");
    try {
        const [emojiData, _me, data] = await Promise.all([
            loadEmojiData(),
            initMemberBar("../"),
            apiFetch("/api/players?scope=season_3"),
        ]);
        renderPlayerList(data.players, emojiData.pokemon || []);
    } catch (error) {
        renderApiError(container, error, () => main());
    }
}

main();

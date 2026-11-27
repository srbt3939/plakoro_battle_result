// Shared API client, error handling, and member navigation bar for AWS frontend (手順7 §4・§5)

class PlakoroApiClientError extends Error {
    constructor(status, code, message, requestId, retryAfter = null) {
        super(message);
        this.name = "PlakoroApiClientError";
        this.status = status;
        this.code = code;
        this.requestId = requestId;
        this.retryAfter = retryAfter;
    }
}

function escapeApiHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[ch]);
}

function renderNotAMemberScreen() {
    const main = document.querySelector("main") || document.body;
    main.innerHTML = `
        <section class="card" style="text-align:center; padding: 36px 20px;">
            <h2>🔒 アクセス制限</h2>
            <p>このサイトはDiscordサーバーのメンバー限定です。</p>
            <p style="margin-top:16px;">
                <a href="/api/auth/login" style="font-weight:bold;">別のDiscordアカウントでログインし直す</a>
            </p>
        </section>
    `;
}

function renderApiError(container, error, onRetry = null) {
    if (!container) return;
    if (error instanceof PlakoroApiClientError) {
        if (error.status === 403 && error.code === "NOT_A_MEMBER") {
            renderNotAMemberScreen();
            return;
        }
        if (error.status === 403 && error.code === "PLAYER_PRIVATE") {
            container.innerHTML = `
                <section class="card" style="text-align:center; padding: 28px 16px;">
                    <h2>🔒 非公開</h2>
                    <p>このプレイヤーの戦績は非公開です。</p>
                </section>
            `;
            return;
        }
        if (error.status === 404) {
            container.innerHTML = `<p class="load-error">データが見つかりません。</p>`;
            return;
        }
        if (error.status === 429 || error.status === 503) {
            container.innerHTML = `
                <div class="load-error" style="text-align:center;">
                    <p>しばらくしてからやり直してください。</p>
                    ${onRetry ? '<button type="button" id="api-retry-btn" style="margin-top:10px;padding:8px 16px;cursor:pointer;">再試行</button>' : ""}
                </div>
            `;
            if (onRetry) {
                document.getElementById("api-retry-btn")?.addEventListener("click", onRetry);
            }
            return;
        }
        const idNote = error.requestId ? `（ID：${escapeApiHTML(error.requestId)}）` : "";
        container.innerHTML = `<p class="load-error">エラーが発生しました${idNote}</p>`;
        return;
    }
    container.innerHTML = `<p class="load-error">通信できませんでした。</p>`;
}

async function apiFetch(path, options = {}, _retried = false) {
    const method = (options.method || "GET").toUpperCase();
    const headers = { ...(options.headers || {}) };
    if ((method === "POST" || method === "PATCH" || method === "PUT") && options.body && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
    }

    let response;
    try {
        response = await fetch(path, {
            ...options,
            method,
            headers,
            credentials: "same-origin",
        });
    } catch (networkErr) {
        throw networkErr;
    }

    const requestId = response.headers.get("X-Request-Id") || "";

    if (response.status === 401) {
        const returnTo = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/api/auth/login?returnTo=${returnTo}`;
        throw new PlakoroApiClientError(401, "UNAUTHENTICATED", "ログインが必要です。", requestId);
    }

    let payload = null;
    try {
        payload = await response.json();
    } catch (_e) {
        payload = null;
    }

    if (!response.ok) {
        const errObj = payload?.error || {};
        const code = errObj.code || (response.status >= 500 ? "INTERNAL_ERROR" : "ERROR");
        const message = errObj.message || "エラーが発生しました。";
        const retryAfter = Number(errObj.retryAfter || response.headers.get("Retry-After") || 2);

        if ((response.status === 429 || response.status === 503) && !_retried) {
            const waitMs = Math.min(5000, Math.max(300, retryAfter * 1000));
            await new Promise(resolve => setTimeout(resolve, waitMs));
            return apiFetch(path, options, true);
        }

        if (response.status === 403 && code === "NOT_A_MEMBER") {
            renderNotAMemberScreen();
        }
        throw new PlakoroApiClientError(
            response.status,
            code,
            message,
            errObj.requestId || requestId,
            retryAfter
        );
    }

    return payload;
}

let _mePromise = null;
function fetchCurrentMember() {
    if (!_mePromise) {
        _mePromise = apiFetch("/api/me");
    }
    return _mePromise;
}

async function initMemberBar(rootPrefix = "") {
    const params = new URLSearchParams(window.location.search);
    if (params.get("auth_error") === "not_a_member") {
        renderNotAMemberScreen();
        return null;
    }
    try {
        const meData = await fetchCurrentMember();
        const header = document.querySelector("header");
        if (header && meData?.player && !document.getElementById("member-nav-bar")) {
            const bar = document.createElement("nav");
            bar.id = "member-nav-bar";
            bar.style.cssText = "margin-top:10px; font-size:0.9rem; display:flex; gap:12px; justify-content:center; align-items:center; flex-wrap:wrap;";
            bar.innerHTML = `
                <span>👤 ${escapeApiHTML(meData.player.name)}（${meData.player.isPublic ? "公開" : "非公開"}）</span>
                <a href="${rootPrefix}players/player.html?id=${encodeURIComponent(meData.player.id)}">マイ戦績</a>
                <a href="${rootPrefix}mypage.html">設定・リセット申請</a>
                <button type="button" id="member-logout-btn" style="padding:3px 10px; cursor:pointer;">ログアウト</button>
            `;
            header.appendChild(bar);
            document.getElementById("member-logout-btn")?.addEventListener("click", async () => {
                await apiFetch("/api/auth/logout", { method: "POST" });
                window.location.href = "/api/auth/login";
            });
        }
        return meData;
    } catch (_err) {
        return null;
    }
}

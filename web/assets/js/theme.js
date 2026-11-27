(function () {
    const STORAGE_KEY = "plakoro-theme";
    const darkMediaQuery = window.matchMedia
        ? window.matchMedia("(prefers-color-scheme: dark)")
        : null;

    function getStoredTheme() {
        try {
            const value = localStorage.getItem(STORAGE_KEY);
            return value === "dark" || value === "light" ? value : null;
        } catch {
            return null;
        }
    }

    function setStoredTheme(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch {
            // Ignore storage errors (e.g. private browsing restrictions)
        }
    }

    function resolveInitialTheme() {
        const stored = getStoredTheme();
        if (stored) {
            return stored;
        }
        return darkMediaQuery && darkMediaQuery.matches ? "dark" : "light";
    }

    function updateToggleButtons(theme) {
        const isDark = theme === "dark";
        const icon = isDark ? "☀️" : "🌙";
        const label = isDark ? "ライト" : "ダーク";
        const ariaLabel = isDark ? "ライトモードに切り替え" : "ダークモードに切り替え";

        document.querySelectorAll("[data-theme-toggle]").forEach(button => {
            button.setAttribute("aria-pressed", String(isDark));
            button.setAttribute("aria-label", ariaLabel);
            button.setAttribute("title", ariaLabel);

            const iconEl = button.querySelector(".theme-toggle-icon");
            const labelEl = button.querySelector(".theme-toggle-label");
            if (iconEl) {
                iconEl.textContent = icon;
            } else {
                button.textContent = icon;
            }
            if (labelEl) {
                labelEl.textContent = label;
            }
        });
    }

    function applyTheme(theme, notify = true) {
        document.documentElement.dataset.theme = theme;
        updateToggleButtons(theme);
        if (notify) {
            window.dispatchEvent(new CustomEvent("themechange", { detail: { theme } }));
        }
    }

    function toggleTheme() {
        const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
        const next = current === "dark" ? "light" : "dark";
        setStoredTheme(next);
        applyTheme(next);
    }

    applyTheme(resolveInitialTheme(), false);

    if (darkMediaQuery && typeof darkMediaQuery.addEventListener === "function") {
        darkMediaQuery.addEventListener("change", event => {
            if (!getStoredTheme()) {
                applyTheme(event.matches ? "dark" : "light");
            }
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        updateToggleButtons(document.documentElement.dataset.theme || "light");
        document.querySelectorAll("[data-theme-toggle]").forEach(button => {
            button.addEventListener("click", toggleTheme);
        });
    });
})();

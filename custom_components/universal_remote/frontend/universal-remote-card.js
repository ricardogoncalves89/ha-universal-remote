/**
 * Universal Remote Card v0.6.1
 *
 * Physical-remote-style Lovelace card for the Universal Remote
 * Home Assistant integration. Mobile-first, light/dark themed.
 *
 * Two configuration modes:
 *
 *   Single TV (legacy):
 *     type: custom:universal-remote-card
 *     entity: remote.tv_escritorio
 *     media_player_entity: media_player.tv_escritorio
 *     title: TV Escritório
 *
 *   Multi-TV with dropdown (new):
 *     type: custom:universal-remote-card
 *     remotes:
 *       - entity: remote.tv_escritorio
 *         media_player_entity: media_player.tv_escritorio
 *         label: Escritório
 *       - entity: remote.tv_sala
 *         media_player_entity: media_player.tv_sala
 *         label: Sala
 *
 * In multi-TV mode the title becomes a dropdown <select> with all
 * configured TVs. The selected TV is persisted in localStorage and
 * survives refreshes / app restarts.
 */

// -----------------------------------------------------------------
// UI icons
// -----------------------------------------------------------------
const ICONS = {
    list: `<path d="M3 5h18v2H3zm0 6h18v2H3zm0 6h18v2H3z" />`,
    power: `<path d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42A6.92 6.92 0 0 1 19 12a7 7 0 0 1-14 0 6.92 6.92 0 0 1 2.59-5.41L6.17 5.17A9 9 0 1 0 21 12a8.94 8.94 0 0 0-3.17-6.83z"/>`,
    keypad: `<path d="M5 7h2v2H5V7zm6 0h2v2h-2V7zm6 0h2v2h-2V7zM5 11h2v2H5v-2zm6 0h2v2h-2v-2zm6 0h2v2h-2v-2zM5 15h2v2H5v-2zm6 0h2v2h-2v-2zm6 0h2v2h-2v-2z"/>`,
    home: `<path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/>`,
    volume: `<path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/>`,
    plus: `<path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>`,
    minus: `<path d="M19 13H5v-2h14v2z"/>`,
    up: `<path d="M7 14l5-5 5 5z"/>`,
    down: `<path d="M7 10l5 5 5-5z"/>`,
    left: `<path d="M14 7l-5 5 5 5z"/>`,
    right: `<path d="M10 17l5-5-5-5z"/>`,
    play: `<path d="M8 5v14l11-7z"/>`,
    pause: `<path d="M6 5h4v14H6zm8 0h4v14h-4z"/>`,
    prev: `<path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/>`,
    next: `<path d="M6 18l8.5-6L6 6v12zm10-12v12h2V6h-2z"/>`,
    tv: `<path d="M21 3H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h5v2h8v-2h5c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 14H3V5h18v12z"/>`,
    chevron: `<path d="M7 10l5 5 5-5z"/>`,
    close: `<path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>`,
};

const svg = (path, opts = {}) => {
    const { fill = "currentColor", vb = "0 0 24 24" } = opts;
    return `<svg viewBox="${vb}" fill="${fill}">${path}</svg>`;
};

// -----------------------------------------------------------------
// Brand logos via dashboard-icons (Homarr Labs, CC0)
// -----------------------------------------------------------------
const DI = (slug) =>
    `https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/${slug}.svg`;

const SOURCE_MATCHERS = [
    { re: /\b(live ?tv|^tv$|tuner|antena)\b/i, kind: "icon", icon: "tv" },
    { re: /\bhdmi/i, kind: "text" },
    { re: /netflix/i, kind: "img", url: DI("netflix") },
    { re: /youtube/i, kind: "img", url: DI("youtube") },
    { re: /(prime ?video|amazon)/i, kind: "img", url: DI("prime-video") },
    { re: /disney/i, kind: "img", url: DI("disney-plus") },
    { re: /(rtp|sic|tvi|cmtv|porto canal)/i, kind: "icon", icon: "tv" },
];

function sourceVisual(source) {
    for (const m of SOURCE_MATCHERS) {
        if (m.re.test(source)) {
            if (m.kind === "icon") return { type: "icon", icon: ICONS[m.icon] };
            if (m.kind === "img") return { type: "img", url: m.url };
            if (m.kind === "text") return { type: "text" };
        }
    }
    const letters = source
        .replace(/[^A-Za-z0-9 ]/g, "")
        .trim()
        .split(/\s+/)
        .map((w) => w[0])
        .join("")
        .slice(0, 2)
        .toUpperCase();
    return { type: "badge", letter: letters || "?" };
}

// -----------------------------------------------------------------
// Config normalisation — accepts both single-TV and multi-TV configs.
// -----------------------------------------------------------------
function normaliseConfig(config) {
    if (Array.isArray(config.remotes) && config.remotes.length > 0) {
        const remotes = config.remotes.map((r, i) => {
            if (!r || !r.entity) {
                throw new Error(
                    `remotes[${i}]: 'entity' is required`,
                );
            }
            return {
                entity: r.entity,
                media_player_entity: r.media_player_entity || null,
                label: r.label || `TV ${i + 1}`,
            };
        });
        return { mode: "multi", remotes };
    }
    if (config.entity) {
        return {
            mode: "single",
            remotes: [
                {
                    entity: config.entity,
                    media_player_entity: config.media_player_entity || null,
                    label: config.title || null,
                },
            ],
        };
    }
    throw new Error(
        "Universal Remote Card: either 'entity' (single TV) or 'remotes' (multi-TV) is required",
    );
}

// =================================================================
// Main card
// =================================================================

class UniversalRemoteCard extends HTMLElement {
    static getStubConfig() {
        return { entity: "", media_player_entity: "" };
    }

    static getConfigElement() {
        return document.createElement("universal-remote-card-editor");
    }

    setConfig(config) {
        const { mode, remotes } = normaliseConfig(config);
        this._config = { ...config };
        this._mode = mode;
        this._remotes = remotes;
        this._currentIndex = this._restoreSelection();
        this._lastSourceList = null;
        this._lastPlayingState = null;
        this._render();
    }

    set hass(hass) {
        this._hass = hass;
        this._updateState();
    }

    getCardSize() {
        return 11;
    }

    connectedCallback() {
        if (this._statusTimer) clearInterval(this._statusTimer);
        this._statusTimer = setInterval(() => this._updateStatus(), 30000);
    }

    disconnectedCallback() {
        if (this._statusTimer) {
            clearInterval(this._statusTimer);
            this._statusTimer = null;
        }
    }

    // ---------------------------------------------------------
    // Multi-TV selection
    // ---------------------------------------------------------
    _currentRemote() {
        return this._remotes[this._currentIndex] || this._remotes[0];
    }

    _storageKey() {
        // Use the sorted list of entity IDs as anchor so the same
        // multi-TV card always restores the same selection, regardless
        // of order in YAML.
        const ids = this._remotes
            .map((r) => r.entity)
            .filter(Boolean)
            .sort()
            .join("|");
        return `urc:selected:${ids}`;
    }

    _restoreSelection() {
        if (this._mode !== "multi") return 0;
        try {
            const saved = parseInt(
                window.localStorage.getItem(this._storageKey()),
                10,
            );
            if (
                !isNaN(saved) &&
                saved >= 0 &&
                saved < this._remotes.length
            ) {
                return saved;
            }
        } catch (e) {
            /* localStorage may be disabled; ignore */
        }
        return 0;
    }

    _saveSelection() {
        if (this._mode !== "multi") return;
        try {
            window.localStorage.setItem(
                this._storageKey(),
                String(this._currentIndex),
            );
        } catch (e) {
            /* ignore */
        }
    }

    _onSelectChange(ev) {
        const newIdx = parseInt(ev.target.value, 10);
        if (isNaN(newIdx) || newIdx === this._currentIndex) return;
        this._currentIndex = newIdx;
        this._saveSelection();
        // Force re-render of sources and play/pause state
        this._lastSourceList = null;
        this._lastPlayingState = null;
        this._updateState();
    }

    // ---------------------------------------------------------
    // Service callers — all operate on the current remote
    // ---------------------------------------------------------
    _sendCommand(cmd) {
        if (!this._hass) return;
        const r = this._currentRemote();
        if (!r || !r.entity) return;
        this._haptic();
        this._hass.callService("remote", "send_command", {
            entity_id: r.entity,
            command: cmd,
        });
    }

    _toggle() {
        if (!this._hass) return;
        const r = this._currentRemote();
        if (!r || !r.entity) return;
        this._haptic();
        this._hass.callService("remote", "toggle", { entity_id: r.entity });
    }

    _selectSource(source) {
        if (!this._hass) return;
        const r = this._currentRemote();
        if (!r || !r.media_player_entity) {
            console.warn(
                "universal-remote-card: media_player_entity not configured for",
                r && r.entity,
            );
            return;
        }
        this._haptic();
        this._hass.callService("media_player", "select_source", {
            entity_id: r.media_player_entity,
            source,
        });
    }

    _playPause() {
        if (!this._hass) return;
        const r = this._currentRemote();
        if (!r) return;
        this._haptic();
        if (r.media_player_entity) {
            this._hass.callService("media_player", "media_play_pause", {
                entity_id: r.media_player_entity,
            });
        } else if (r.entity) {
            this._hass.callService("remote", "send_command", {
                entity_id: r.entity,
                command: "PLAY",
            });
        }
    }

    _haptic() {
        this.dispatchEvent(
            new Event("haptic", { bubbles: true, composed: true }),
        );
    }

    // ---------------------------------------------------------
    // State updates
    // ---------------------------------------------------------
    _updateState() {
        if (!this.shadowRoot || !this._hass) return;

        const r = this._currentRemote();
        const remoteState = r ? this._hass.states[r.entity] : null;

        // Power button visual
        const isOn = remoteState && remoteState.state === "on";
        const powerBtn = this.shadowRoot.querySelector(".btn-power");
        if (powerBtn) powerBtn.classList.toggle("is-on", isOn);

        // Single-TV title (use friendly_name if no explicit title)
        if (this._mode === "single") {
            const titleEl = this.shadowRoot.querySelector(".title");
            if (titleEl && !this._config.title) {
                const friendly =
                    remoteState &&
                    remoteState.attributes &&
                    remoteState.attributes.friendly_name;
                titleEl.textContent = friendly || r.entity;
            }
        }
        // Multi-TV dropdown: keep selected option in sync (in case
        // localStorage restore differs from the rendered initial value)
        if (this._mode === "multi") {
            const sel = this.shadowRoot.querySelector(".title-select");
            if (sel && parseInt(sel.value, 10) !== this._currentIndex) {
                sel.value = String(this._currentIndex);
            }
        }

        // Sources list (from current TV's media_player)
        const mp = r && r.media_player_entity;
        const mpState = mp ? this._hass.states[mp] : null;
        const sources =
            mpState &&
            mpState.attributes &&
            Array.isArray(mpState.attributes.source_list)
                ? mpState.attributes.source_list
                : [];

        const sourcesKey = `${this._currentIndex}::${sources.join("|")}`;
        if (sourcesKey !== this._lastSourceList) {
            this._lastSourceList = sourcesKey;
            this._renderSources(sources);
        }

        // Play/Pause icon based on media_player state
        const playing = mpState && mpState.state === "playing";
        if (playing !== this._lastPlayingState) {
            this._lastPlayingState = playing;
            const ppBtn = this.shadowRoot.querySelector(".btn-playpause");
            if (ppBtn) {
                ppBtn.innerHTML = svg(playing ? ICONS.pause : ICONS.play);
                ppBtn.setAttribute(
                    "aria-label",
                    playing ? "Pause" : "Play",
                );
            }
        }

        this._updateStatus();
    }

    _updateStatus() {
        if (!this.shadowRoot || !this._hass) return;

        const r = this._currentRemote();
        const remoteState = r ? this._hass.states[r.entity] : null;
        const statusEl = this.shadowRoot.querySelector(".status");
        const stateEl = this.shadowRoot.querySelector(".status-state");
        const timeEl = this.shadowRoot.querySelector(".status-time");
        if (!statusEl || !stateEl || !timeEl) return;

        if (!remoteState) {
            statusEl.style.display = "none";
            return;
        }

        statusEl.style.display = "flex";
        const isOn = remoteState.state === "on";
        statusEl.classList.toggle("is-on", isOn);
        statusEl.classList.toggle("is-off", !isOn);
        stateEl.textContent = isOn ? "ON" : "OFF";

        if (remoteState.last_changed) {
            timeEl.textContent = this._formatRelative(remoteState.last_changed);
            timeEl.style.display = "inline";
        } else {
            timeEl.style.display = "none";
        }
    }

    _getLocale() {
        if (this._hass) {
            if (this._hass.locale && this._hass.locale.language)
                return this._hass.locale.language;
            if (this._hass.language) return this._hass.language;
        }
        return navigator.language || "pt-PT";
    }

    _formatRelative(isoString) {
        if (!isoString) return "";
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return "";
        const diffSec = (date.getTime() - Date.now()) / 1000;
        const locale = this._getLocale();
        try {
            const rtf = new Intl.RelativeTimeFormat(locale, {
                numeric: "auto",
                style: "short",
            });
            const abs = Math.abs(diffSec);
            if (abs < 60) return rtf.format(Math.round(diffSec), "second");
            if (abs < 3600)
                return rtf.format(Math.round(diffSec / 60), "minute");
            if (abs < 86400)
                return rtf.format(Math.round(diffSec / 3600), "hour");
            if (abs < 604800)
                return rtf.format(Math.round(diffSec / 86400), "day");
            return date.toLocaleDateString(locale, {
                day: "2-digit",
                month: "short",
            });
        } catch (e) {
            return date.toLocaleString(locale);
        }
    }

    // ---------------------------------------------------------
    // Sources grid
    // ---------------------------------------------------------
    _renderSources(sources) {
        const slot = this.shadowRoot.querySelector(".sources-grid");
        if (!slot) return;

        if (!sources.length) {
            slot.innerHTML = `
                <div class="sources-empty">
                    No sources available — make sure media_player_entity
                    is reporting a source_list.
                </div>`;
            return;
        }

        slot.innerHTML = sources
            .map((source) => {
                const vis = sourceVisual(source);
                const safe = this._escape(source);

                if (vis.type === "img") {
                    const initials = source
                        .replace(/[^A-Za-z0-9 ]/g, "")
                        .trim()
                        .split(/\s+/)
                        .map((w) => w[0])
                        .join("")
                        .slice(0, 2)
                        .toUpperCase();
                    return `
                        <button class="btn btn-source"
                                data-source="${safe}"
                                aria-label="${safe}"
                                title="${safe}">
                            <img class="logo"
                                 src="${vis.url}"
                                 alt="${safe}"
                                 loading="lazy"
                                 onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'badge-fallback',textContent:'${this._escape(initials)}'}))" />
                        </button>`;
                }
                if (vis.type === "icon") {
                    return `
                        <button class="btn btn-source"
                                data-source="${safe}"
                                aria-label="${safe}"
                                title="${safe}">
                            ${svg(vis.icon)}
                        </button>`;
                }
                if (vis.type === "text") {
                    return `
                        <button class="btn btn-source btn-source-text"
                                data-source="${safe}"
                                aria-label="${safe}"
                                title="${safe}">
                            <span class="source-label">${safe}</span>
                        </button>`;
                }
                return `
                    <button class="btn btn-source"
                            data-source="${safe}"
                            aria-label="${safe}"
                            title="${safe}">
                        <span class="badge-fallback">${this._escape(vis.letter)}</span>
                    </button>`;
            })
            .join("");

        slot.querySelectorAll("[data-source]").forEach((el) => {
            el.addEventListener("click", () =>
                this._selectSource(el.dataset.source),
            );
        });
    }

    // ---------------------------------------------------------
    // Render shell
    // ---------------------------------------------------------
    _render() {
        if (!this.shadowRoot) {
            this.attachShadow({ mode: "open" });
        }

        const titleHtml = this._renderTitle();

        this.shadowRoot.innerHTML = `
            <style>${this._css()}</style>
            <ha-card class="card">
                ${titleHtml}
                <div class="status" style="display:none">
                    <span class="status-state"></span>
                    <span class="status-time"></span>
                </div>
                <div class="remote">
                    <div class="row row-3">
                        <button class="btn btn-pill" data-cmd="MENU" aria-label="Menu">${svg(ICONS.list)}</button>
                        <button class="btn btn-pill btn-power" data-toggle aria-label="Power">${svg(ICONS.power)}</button>
                        <button class="btn btn-pill" data-cmd="GUIDE" aria-label="Keypad">${svg(ICONS.keypad)}</button>
                    </div>

                    <div class="dpad">
                        <button class="btn btn-arrow dpad-up" data-cmd="UP" aria-label="Up">${svg(ICONS.up)}</button>
                        <button class="btn btn-arrow dpad-left" data-cmd="LEFT" aria-label="Left">${svg(ICONS.left)}</button>
                        <button class="btn btn-ok dpad-ok" data-cmd="OK">OK</button>
                        <button class="btn btn-arrow dpad-right" data-cmd="RIGHT" aria-label="Right">${svg(ICONS.right)}</button>
                        <button class="btn btn-arrow dpad-down" data-cmd="DOWN" aria-label="Down">${svg(ICONS.down)}</button>
                    </div>

                    <div class="row row-2">
                        <button class="btn btn-pill" data-cmd="BACK">BACK</button>
                        <button class="btn btn-pill" data-cmd="HOME" aria-label="Home">${svg(ICONS.home)}</button>
                    </div>

                    <div class="sources-grid">
                        <div class="sources-empty">Loading sources…</div>
                    </div>

                    <div class="row row-trio">
                        <div class="col col-track">
                            <button class="btn btn-track-top" data-cmd="VOL_UP" aria-label="Volume up">${svg(ICONS.plus)}</button>
                            <button class="btn btn-track-mid" data-cmd="MUTE" aria-label="Mute">${svg(ICONS.volume)}</button>
                            <button class="btn btn-track-bot" data-cmd="VOL_DOWN" aria-label="Volume down">${svg(ICONS.minus)}</button>
                        </div>
                        <div class="col col-center">
                            <button class="btn btn-pill" data-cmd="MENU">MENU</button>
                            <button class="btn btn-pill" data-cmd="MUTE">MUTE</button>
                            <button class="btn btn-pill" data-cmd="INFO">INFO</button>
                        </div>
                        <div class="col col-track">
                            <button class="btn btn-track-top" data-cmd="CH_UP" aria-label="Channel up">${svg(ICONS.up)}</button>
                            <button class="btn btn-track-mid btn-track-label" data-cmd="CH_LIST" aria-label="Channels">P</button>
                            <button class="btn btn-track-bot" data-cmd="CH_DOWN" aria-label="Channel down">${svg(ICONS.down)}</button>
                        </div>
                    </div>

                    <div class="row row-3 row-media">
                        <button class="btn btn-pill" data-cmd="PREVIOUS" aria-label="Previous">${svg(ICONS.prev)}</button>
                        <button class="btn btn-pill btn-playpause" data-playpause aria-label="Play">${svg(ICONS.play)}</button>
                        <button class="btn btn-pill" data-cmd="NEXT" aria-label="Next">${svg(ICONS.next)}</button>
                    </div>
                </div>
            </ha-card>
        `;

        this._wireEvents();
        this._updateState();
    }

    _renderTitle() {
        if (this._mode === "single") {
            return this._config.title === false
                ? ""
                : `<div class="title">${this._config.title || ""}</div>`;
        }
        // Multi-TV: dropdown
        const options = this._remotes
            .map(
                (r, i) =>
                    `<option value="${i}"${i === this._currentIndex ? " selected" : ""}>${this._escape(r.label)}</option>`,
            )
            .join("");
        return `
            <div class="title-wrap">
                <select class="title-select" aria-label="Select TV">
                    ${options}
                </select>
                <span class="title-chevron">${svg(ICONS.chevron)}</span>
            </div>
        `;
    }

    _wireEvents() {
        this.shadowRoot.querySelectorAll("[data-cmd]").forEach((el) => {
            el.addEventListener("click", () =>
                this._sendCommand(el.dataset.cmd),
            );
        });
        this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) => {
            el.addEventListener("click", () => this._toggle());
        });
        this.shadowRoot.querySelectorAll("[data-playpause]").forEach((el) => {
            el.addEventListener("click", () => this._playPause());
        });
        const select = this.shadowRoot.querySelector(".title-select");
        if (select) {
            select.addEventListener("change", (ev) =>
                this._onSelectChange(ev),
            );
        }
    }

    _escape(text) {
        const d = document.createElement("div");
        d.textContent = String(text);
        return d.innerHTML;
    }

    _css() {
        return `
            :host {
                --urc-radius: 28px;
                --urc-radius-btn: 18px;
                --urc-gap: 10px;
                --urc-row-gap: 8px;
                --urc-btn-h: 52px;
                --urc-btn-label-color: var(--primary-text-color);
                --urc-btn-bg: var(--secondary-background-color, rgba(127,127,127,0.12));
                --urc-btn-bg-active: var(--divider-color, rgba(127,127,127,0.25));
                --urc-power-red: #d8454a;
            }
            ha-card.card {
                padding: 16px;
                background: var(--card-background-color);
                border-radius: var(--urc-radius);
            }
            .title {
                font-size: 0.95rem;
                color: var(--secondary-text-color);
                text-align: center;
                margin-bottom: 8px;
                font-weight: 500;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            /* Multi-TV dropdown */
            .title-wrap {
                position: relative;
                display: flex;
                align-items: center;
                justify-content: center;
                margin-bottom: 8px;
            }
            .title-select {
                appearance: none;
                -webkit-appearance: none;
                background: var(--urc-btn-bg);
                color: var(--primary-text-color);
                border: none;
                border-radius: 12px;
                padding: 8px 30px 8px 16px;
                font: inherit;
                font-size: 0.9rem;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-transform: uppercase;
                cursor: pointer;
                text-align: center;
                text-align-last: center;
                min-width: 60%;
                max-width: 100%;
                transition: background-color 120ms ease;
            }
            .title-select:hover {
                background: var(--urc-btn-bg-active);
            }
            .title-select:focus-visible {
                outline: none;
                box-shadow: 0 0 0 2px var(--primary-color, #03a9f4);
            }
            .title-chevron {
                position: absolute;
                right: max(8px, calc(50% - 30% + 4px));
                pointer-events: none;
                color: var(--secondary-text-color);
                display: flex;
            }
            .title-chevron svg {
                width: 18px;
                height: 18px;
            }
            .status {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                margin: 0 auto 12px;
                font-size: 0.8rem;
            }
            .status-state {
                font-weight: 700;
                letter-spacing: 1px;
                padding: 2px 10px;
                border-radius: 10px;
                background: var(--urc-btn-bg);
                color: var(--secondary-text-color);
                line-height: 1.4;
            }
            .status.is-on .status-state {
                background: rgba(76, 175, 80, 0.15);
                color: #2e7d32;
            }
            .status.is-off .status-state {
                background: var(--urc-btn-bg-active);
                color: var(--secondary-text-color);
            }
            .status-time {
                color: var(--secondary-text-color);
                font-size: 0.78rem;
                font-variant-numeric: tabular-nums;
            }
            .status-time::before {
                content: '·';
                margin-right: 6px;
                opacity: 0.5;
            }
            .remote {
                display: flex;
                flex-direction: column;
                gap: var(--urc-row-gap);
            }
            .row {
                display: grid;
                gap: var(--urc-gap);
                align-items: center;
            }
            .row-2 { grid-template-columns: 1fr 1fr; }
            .row-3 { grid-template-columns: 1fr 1fr 1fr; }
            .row-trio {
                grid-template-columns: 1fr 1.2fr 1fr;
                align-items: stretch;
                margin: 4px 0;
            }
            .btn {
                background: var(--urc-btn-bg);
                color: var(--urc-btn-label-color);
                border: none;
                border-radius: var(--urc-radius-btn);
                padding: 0;
                font: inherit;
                font-size: 0.9rem;
                font-weight: 500;
                letter-spacing: 0.5px;
                height: var(--urc-btn-h);
                display: inline-flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                transition: transform 80ms ease, background-color 120ms ease;
                outline: none;
                user-select: none;
                -webkit-tap-highlight-color: transparent;
            }
            .btn svg { width: 22px; height: 22px; }
            .btn:active {
                transform: scale(0.93);
                background-color: var(--urc-btn-bg-active);
            }
            .btn:focus-visible {
                box-shadow: 0 0 0 3px var(--primary-color, #03a9f4);
            }
            .dpad {
                display: grid;
                grid-template-columns: 1fr 1.4fr 1fr;
                grid-template-rows: auto auto auto;
                align-items: center;
                justify-items: center;
                row-gap: 4px;
                column-gap: 4px;
                margin: 8px 0;
            }
            .dpad-up    { grid-area: 1 / 2 / 2 / 3; }
            .dpad-left  { grid-area: 2 / 1 / 3 / 2; justify-self: end; }
            .dpad-ok    { grid-area: 2 / 2 / 3 / 3; }
            .dpad-right { grid-area: 2 / 3 / 3 / 4; justify-self: start; }
            .dpad-down  { grid-area: 3 / 2 / 4 / 3; }
            .btn-arrow {
                background: transparent;
                height: 42px;
                width: 42px;
                border-radius: 50%;
            }
            .btn-arrow:active { background-color: var(--urc-btn-bg-active); }
            .btn-ok {
                height: 88px;
                width: 88px;
                border-radius: 50%;
                font-size: 0.95rem;
                font-weight: 600;
                letter-spacing: 1px;
                background: transparent;
                border: 1.5px solid var(--divider-color, rgba(127,127,127,0.4));
            }
            .btn-ok:active { background-color: var(--urc-btn-bg-active); }
            .btn-power { color: white; background: var(--urc-power-red); }
            .btn-power:active { background: var(--urc-power-red); opacity: 0.85; }
            .btn-power.is-on { box-shadow: 0 0 0 2px rgba(216,69,74,0.25); }
            .sources-grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: var(--urc-gap);
                margin: 8px 0 4px;
            }
            .sources-empty {
                grid-column: 1 / -1;
                text-align: center;
                color: var(--secondary-text-color);
                font-size: 0.85rem;
                padding: 12px;
            }
            .btn-source { height: 56px; padding: 4px; }
            .btn-source svg { width: 28px; height: 28px; fill: var(--primary-text-color); }
            .btn-source img.logo {
                width: 32px; height: 32px;
                object-fit: contain;
                pointer-events: none;
            }
            .btn-source-text { padding: 4px 6px; }
            .btn-source-text .source-label {
                font-weight: 700;
                font-size: 0.85rem;
                color: var(--primary-text-color);
                text-align: center;
                line-height: 1.1;
                word-break: break-word;
            }
            .badge-fallback {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 36px;
                height: 30px;
                padding: 0 8px;
                border-radius: 8px;
                background: var(--urc-btn-bg-active);
                color: var(--primary-text-color);
                font-weight: 700;
                font-size: 0.85rem;
                letter-spacing: 0.5px;
            }
            .col-track {
                display: flex;
                flex-direction: column;
                justify-content: center;
                background: var(--urc-btn-bg);
                border-radius: 28px;
                padding: 6px 0;
                gap: 0;
            }
            .col-track .btn {
                background: transparent;
                height: 44px;
                border-radius: 22px;
                margin: 0 6px;
            }
            .col-track .btn:active { background-color: var(--urc-btn-bg-active); }
            .btn-track-mid { margin: 4px 6px; }
            .btn-track-label { font-size: 1rem; font-weight: 600; }
            .col-center {
                display: flex;
                flex-direction: column;
                gap: var(--urc-row-gap);
                justify-content: space-between;
            }
            .col-center .btn { width: 100%; }
            .row-media .btn { height: 46px; }
        `;
    }
}

customElements.define("universal-remote-card", UniversalRemoteCard);

// =================================================================
// Visual editor
// =================================================================

class UniversalRemoteCardEditor extends HTMLElement {
    constructor() {
        super();
        // Default to {} so accessing this._config.remotes never throws
        // even if a render is somehow triggered before setConfig.
        this._config = {};
        this._initialized = false;
    }

    setConfig(config) {
        // HA's call order is not guaranteed: sometimes setConfig
        // runs before `hass`, sometimes after. We only do the initial
        // full render once both have arrived.
        this._config = { ...config };
        this._initialized = true;
        if (this._hass && !this.shadowRoot) {
            this._render();
        }
    }

    set hass(hass) {
        this._hass = hass;
        if (!this._initialized) {
            // Wait for setConfig — without it we have nothing to render.
            return;
        }
        if (!this.shadowRoot) {
            this._render();
        } else {
            this._refreshPickersHass();
        }
    }

    _isMulti() {
        return Array.isArray(this._config.remotes) && this._config.remotes.length > 0;
    }

    _render() {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.innerHTML = `
            <style>${this._css()}</style>
            <div class="root"></div>
        `;
        this._renderContent();
    }

    _renderContent() {
        const root = this.shadowRoot.querySelector(".root");
        if (!root) return;
        const isMulti = this._isMulti();

        root.innerHTML = `
            <div class="form">
                <label class="toggle-row">
                    <input type="checkbox" id="multi-toggle" ${isMulti ? "checked" : ""}/>
                    <span>Multiple TVs (show dropdown selector)</span>
                </label>

                <div id="single-section" style="${isMulti ? "display:none" : ""}">
                    <div class="row">
                        <ha-entity-picker id="entity-picker"
                                          label="Remote entity (required)"
                                          allow-custom-entity></ha-entity-picker>
                        <div class="help">The remote.* entity from this integration.</div>
                    </div>
                    <div class="row">
                        <ha-entity-picker id="mp-picker"
                                          label="Media player entity"
                                          allow-custom-entity></ha-entity-picker>
                        <div class="help">The corresponding media_player.* entity.</div>
                    </div>
                    <div class="row">
                        <label class="field-label" for="title-input">Title (optional)</label>
                        <input id="title-input" class="text-input" type="text"
                               placeholder="Leave empty to use the remote's friendly name" />
                    </div>
                </div>

                <div id="multi-section" style="${isMulti ? "" : "display:none"}">
                    <div class="field-label">TVs</div>
                    <div id="tv-list"></div>
                    <button type="button" id="add-tv" class="add-btn">+ Add TV</button>
                    <div class="help">
                        Each TV gets an entry in the dropdown at the top of the card.
                        The last selected TV is remembered across refreshes.
                    </div>
                </div>
            </div>
        `;

        // Wire toggle
        const toggle = this.shadowRoot.getElementById("multi-toggle");
        toggle.addEventListener("change", (ev) => this._onModeToggle(ev));

        if (isMulti) {
            this._renderTVList();
            this.shadowRoot
                .getElementById("add-tv")
                .addEventListener("click", () => this._addTV());
        } else {
            this._wireSingleSection();
        }
    }

    _wireSingleSection() {
        const ePicker = this.shadowRoot.getElementById("entity-picker");
        const mPicker = this.shadowRoot.getElementById("mp-picker");
        const tInput = this.shadowRoot.getElementById("title-input");

        ePicker.hass = this._hass;
        ePicker.value = this._config.entity || "";
        ePicker.includeDomains = ["remote"];
        ePicker.addEventListener("value-changed", (ev) => {
            this._updateSingle("entity", ev.detail.value);
        });

        mPicker.hass = this._hass;
        mPicker.value = this._config.media_player_entity || "";
        mPicker.includeDomains = ["media_player"];
        mPicker.addEventListener("value-changed", (ev) => {
            this._updateSingle("media_player_entity", ev.detail.value);
        });

        tInput.value = this._config.title || "";
        tInput.addEventListener("input", (ev) => {
            this._updateSingle("title", ev.target.value);
        });
    }

    _renderTVList() {
        const list = this.shadowRoot.getElementById("tv-list");
        if (!list) return;
        const remotes = this._config.remotes || [];

        list.innerHTML = remotes
            .map(
                (r, i) => `
            <div class="tv-slot" data-index="${i}">
                <div class="tv-slot-header">
                    <span class="tv-slot-num">TV #${i + 1}</span>
                    <button type="button" class="remove-btn" data-remove="${i}" aria-label="Remove TV">
                        ${svg(ICONS.close)}
                    </button>
                </div>
                <ha-entity-picker class="tv-remote" id="tv-remote-${i}"
                                  label="Remote entity *"
                                  allow-custom-entity></ha-entity-picker>
                <ha-entity-picker class="tv-mp" id="tv-mp-${i}"
                                  label="Media player entity"
                                  allow-custom-entity></ha-entity-picker>
                <input class="text-input tv-label" id="tv-label-${i}" type="text"
                       placeholder="Dropdown label (e.g. Sala, Escritório)" />
            </div>
        `,
            )
            .join("");

        remotes.forEach((r, i) => {
            const remotePicker = this.shadowRoot.getElementById(`tv-remote-${i}`);
            const mpPicker = this.shadowRoot.getElementById(`tv-mp-${i}`);
            const labelInput = this.shadowRoot.getElementById(`tv-label-${i}`);

            remotePicker.hass = this._hass;
            remotePicker.value = r.entity || "";
            remotePicker.includeDomains = ["remote"];
            remotePicker.addEventListener("value-changed", (ev) =>
                this._updateTV(i, "entity", ev.detail.value),
            );

            mpPicker.hass = this._hass;
            mpPicker.value = r.media_player_entity || "";
            mpPicker.includeDomains = ["media_player"];
            mpPicker.addEventListener("value-changed", (ev) =>
                this._updateTV(i, "media_player_entity", ev.detail.value),
            );

            labelInput.value = r.label || "";
            labelInput.addEventListener("input", (ev) =>
                this._updateTV(i, "label", ev.target.value),
            );
        });

        // Wire remove buttons
        this.shadowRoot.querySelectorAll("[data-remove]").forEach((btn) => {
            btn.addEventListener("click", (ev) => {
                const idx = parseInt(
                    ev.currentTarget.getAttribute("data-remove"),
                    10,
                );
                this._removeTV(idx);
            });
        });
    }

    _refreshPickersHass() {
        if (!this.shadowRoot || !this._hass) return;
        this.shadowRoot
            .querySelectorAll("ha-entity-picker")
            .forEach((p) => (p.hass = this._hass));
    }

    // -----------------------------------------------------------------
    // Single-TV updates
    // -----------------------------------------------------------------
    _updateSingle(key, value) {
        const config = { ...this._config };
        if (value === undefined || value === null || value === "") {
            delete config[key];
        } else {
            config[key] = value;
        }
        this._config = config;
        this._dispatch();
    }

    // -----------------------------------------------------------------
    // Multi-TV updates
    // -----------------------------------------------------------------
    _updateTV(index, key, value) {
        const remotes = (this._config.remotes || []).map((r) => ({ ...r }));
        if (!remotes[index]) return;
        if (value === undefined || value === null || value === "") {
            delete remotes[index][key];
        } else {
            remotes[index][key] = value;
        }
        this._config = { ...this._config, remotes };
        this._dispatch();
    }

    _addTV() {
        const remotes = [...(this._config.remotes || [])];
        remotes.push({ entity: "", media_player_entity: "", label: "" });
        this._config = { ...this._config, remotes };
        this._renderTVList();
        this._dispatch();
    }

    _removeTV(index) {
        const remotes = [...(this._config.remotes || [])];
        if (index < 0 || index >= remotes.length) return;
        remotes.splice(index, 1);
        this._config = { ...this._config, remotes };
        this._renderTVList();
        this._dispatch();
    }

    // -----------------------------------------------------------------
    // Single <-> Multi mode toggle
    // -----------------------------------------------------------------
    _onModeToggle(ev) {
        const enabling = ev.target.checked;
        if (enabling) {
            // Convert current single config into the first multi slot
            const first = {
                entity: this._config.entity || "",
                media_player_entity:
                    this._config.media_player_entity || "",
                label: this._config.title || "Living room",
            };
            const config = { ...this._config };
            delete config.entity;
            delete config.media_player_entity;
            delete config.title;
            config.remotes = [first];
            this._config = config;
        } else {
            // Collapse the first multi slot into single config
            const first =
                (this._config.remotes && this._config.remotes[0]) || {};
            const config = { ...this._config };
            delete config.remotes;
            if (first.entity) config.entity = first.entity;
            if (first.media_player_entity)
                config.media_player_entity = first.media_player_entity;
            if (first.label) config.title = first.label;
            this._config = config;
        }
        this._renderContent();
        this._dispatch();
    }

    _dispatch() {
        this.dispatchEvent(
            new CustomEvent("config-changed", {
                detail: { config: this._config },
                bubbles: true,
                composed: true,
            }),
        );
    }

    _css() {
        return `
            .form {
                display: flex;
                flex-direction: column;
                gap: 12px;
                padding: 8px 0;
            }
            .toggle-row {
                display: flex;
                align-items: center;
                gap: 10px;
                cursor: pointer;
                padding: 4px 0;
                font-size: 14px;
            }
            .toggle-row input { transform: scale(1.2); }
            .row { display: block; margin-bottom: 8px; }
            .help {
                font-size: 12px;
                color: var(--secondary-text-color);
                margin-top: 4px;
            }
            .field-label {
                display: block;
                font-size: 13px;
                color: var(--secondary-text-color);
                margin-bottom: 4px;
                font-weight: 500;
            }
            .text-input {
                width: 100%;
                font: inherit;
                padding: 10px 12px;
                box-sizing: border-box;
                border: 1px solid var(--divider-color, #ccc);
                border-radius: 6px;
                background: var(--card-background-color, #fff);
                color: var(--primary-text-color, #000);
            }
            #multi-section { display: flex; flex-direction: column; gap: 10px; }
            #tv-list {
                display: flex;
                flex-direction: column;
                gap: 12px;
            }
            .tv-slot {
                border: 1px solid var(--divider-color, #ddd);
                border-radius: 8px;
                padding: 12px;
                display: flex;
                flex-direction: column;
                gap: 8px;
                background: var(--secondary-background-color, transparent);
            }
            .tv-slot-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 4px;
            }
            .tv-slot-num {
                font-weight: 600;
                font-size: 13px;
                color: var(--secondary-text-color);
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            .remove-btn {
                background: transparent;
                border: none;
                cursor: pointer;
                padding: 4px;
                color: var(--secondary-text-color);
                border-radius: 4px;
                display: inline-flex;
            }
            .remove-btn:hover {
                background: rgba(216, 69, 74, 0.1);
                color: #d8454a;
            }
            .remove-btn svg { width: 18px; height: 18px; fill: currentColor; }
            .add-btn {
                background: transparent;
                border: 1px dashed var(--divider-color, #aaa);
                color: var(--primary-color, #03a9f4);
                padding: 10px;
                border-radius: 8px;
                font: inherit;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                transition: background-color 120ms ease;
            }
            .add-btn:hover {
                background: var(--secondary-background-color, rgba(127,127,127,0.06));
            }
        `;
    }
}

customElements.define(
    "universal-remote-card-editor",
    UniversalRemoteCardEditor,
);

window.customCards = window.customCards || [];
window.customCards.push({
    type: "universal-remote-card",
    name: "Universal Remote Card",
    description:
        "Physical-remote-style card for the Universal Remote integration (Samsung / LG / Apple TV). Single TV or multi-TV with dropdown.",
    preview: false,
    documentationURL:
        "https://github.com/ricardogoncalves89/ha-universal-remote#lovelace-card",
});

console.info(
    "%c UNIVERSAL-REMOTE-CARD %c v0.6.1 ",
    "color: white; background: #d8454a; padding: 2px 4px; border-radius: 3px 0 0 3px;",
    "color: white; background: #444; padding: 2px 4px; border-radius: 0 3px 3px 0;",
);

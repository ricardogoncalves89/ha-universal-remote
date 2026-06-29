/**
 * Universal Remote Card v0.3.0
 *
 * Physical-remote-style Lovelace card for the Universal Remote
 * Home Assistant integration. Mobile-first, light/dark themed.
 *
 * Usage:
 *   type: custom:universal-remote-card
 *   entity: remote.tv_escritorio
 *   media_player_entity: media_player.tv_escritorio
 *   title: TV Escritório
 *
 * Sources are pulled live from the media_player's source_list in the
 * order the integration exposes them. Recognised streaming brands
 * (Netflix, YouTube, Amazon Prime Video, Disney+) render with their
 * official logos via CDN. Live TV / HDMI render with TV/plug icons.
 * Anything else falls back to text initials.
 */

// -----------------------------------------------------------------
// Icons (UI)
// -----------------------------------------------------------------
const ICONS = {
    list: `<path d="M3 5h18v2H3zm0 6h18v2H3zm0 6h18v2H3z" />`,
    power: `<path d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42A6.92 6.92 0 0 1 19 12a7 7 0 0 1-14 0 6.92 6.92 0 0 1 2.59-5.41L6.17 5.17A9 9 0 1 0 21 12a8.94 8.94 0 0 0-3.17-6.83z"/>`,
    keypad: `<path d="M5 7h2v2H5V7zm6 0h2v2h-2V7zm6 0h2v2h-2V7zM5 11h2v2H5v-2zm6 0h2v2h-2v-2zm6 0h2v2h-2v-2zM5 15h2v2H5v-2zm6 0h2v2h-2v-2zm6 0h2v2h-2v-2z"/>`,
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
    hdmi: `<path d="M19 9h-2V7h-2v2h-2V7h-2v2H9V7H7v2H5c-1.1 0-2 .9-2 2v3c0 1.1.9 2 2 2h6v4h2v-4h6c1.1 0 2-.9 2-2v-3c0-1.1-.9-2-2-2z"/>`,
};

const svg = (path, opts = {}) => {
    const { fill = "currentColor", vb = "0 0 24 24" } = opts;
    return `<svg viewBox="${vb}" fill="${fill}">${path}</svg>`;
};

// -----------------------------------------------------------------
// Source matchers — order matters (first match wins).
// -----------------------------------------------------------------
// Each kind:
//   icon  -> inline SVG (Live TV, HDMI)
//   img   -> external <img> from a CDN (official brand logo)
//   badge -> fallback: 1-2 letter pill in a neutral colour
const SOURCE_MATCHERS = [
    // Native TV functions
    { re: /\b(live ?tv|^tv$|tuner|antena)\b/i, kind: "icon", icon: "tv" },
    { re: /\bhdmi/i, kind: "icon", icon: "hdmi" },
    // Streaming brands — official logos via simpleicons CDN
    {
        re: /netflix/i,
        kind: "img",
        url: "https://cdn.simpleicons.org/netflix/E50914",
    },
    {
        re: /youtube/i,
        kind: "img",
        url: "https://cdn.simpleicons.org/youtube/FF0000",
    },
    {
        re: /(prime ?video|amazon)/i,
        kind: "img",
        url: "https://cdn.simpleicons.org/primevideo/00A8E1",
    },
    // Disney+ isn't in simpleicons (Disney IP); use SVGRepo CC0 SVG.
    // The brand colour `#0F2D5C` is baked into that SVG file.
    {
        re: /disney/i,
        kind: "img",
        url: "https://www.svgrepo.com/show/504281/disneyplus.svg",
    },
    // Common Portuguese broadcast channels — treat as Live-TV-style
    { re: /(rtp|sic|tvi|cmtv|porto canal)/i, kind: "icon", icon: "tv" },
];

function sourceVisual(source) {
    for (const m of SOURCE_MATCHERS) {
        if (m.re.test(source)) {
            if (m.kind === "icon") {
                return { type: "icon", icon: ICONS[m.icon] };
            }
            if (m.kind === "img") {
                return { type: "img", url: m.url };
            }
        }
    }
    // Fallback: initials in a neutral pill so it's still readable.
    const letters = source
        .replace(/[^A-Za-z0-9 ]/g, "")
        .trim()
        .split(/\s+/)
        .map((w) => w[0])
        .join("")
        .slice(0, 2)
        .toUpperCase();
    return {
        type: "badge",
        letter: letters || "?",
    };
}

// -----------------------------------------------------------------
// Custom element
// -----------------------------------------------------------------

class UniversalRemoteCard extends HTMLElement {
    static getStubConfig() {
        return {
            entity: "remote.your_tv",
            media_player_entity: "media_player.your_tv",
        };
    }

    setConfig(config) {
        if (!config.entity) {
            throw new Error("'entity' (a remote.* entity) is required");
        }
        this._config = { ...config };
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

    _sendCommand(cmd) {
        if (!this._hass || !this._config) return;
        this._haptic();
        this._hass.callService("remote", "send_command", {
            entity_id: this._config.entity,
            command: cmd,
        });
    }

    _toggle() {
        if (!this._hass || !this._config) return;
        this._haptic();
        this._hass.callService("remote", "toggle", {
            entity_id: this._config.entity,
        });
    }

    _selectSource(source) {
        if (!this._hass || !this._config) return;
        const mp = this._config.media_player_entity;
        if (!mp) {
            console.warn(
                "universal-remote-card: media_player_entity not configured",
            );
            return;
        }
        this._haptic();
        this._hass.callService("media_player", "select_source", {
            entity_id: mp,
            source,
        });
    }

    _playPause() {
        if (!this._hass || !this._config) return;
        this._haptic();
        const mp = this._config.media_player_entity;
        if (mp) {
            this._hass.callService("media_player", "media_play_pause", {
                entity_id: mp,
            });
        } else {
            this._hass.callService("remote", "send_command", {
                entity_id: this._config.entity,
                command: "PLAY",
            });
        }
    }

    _haptic() {
        this.dispatchEvent(
            new Event("haptic", { bubbles: true, composed: true }),
        );
    }

    _updateState() {
        if (!this.shadowRoot || !this._hass || !this._config) return;

        const remoteState = this._hass.states[this._config.entity];
        const isOn = remoteState && remoteState.state === "on";
        const powerBtn = this.shadowRoot.querySelector(".btn-power");
        if (powerBtn) powerBtn.classList.toggle("is-on", isOn);

        const titleEl = this.shadowRoot.querySelector(".title");
        if (titleEl && !this._config.title) {
            const friendly =
                remoteState && remoteState.attributes
                    ? remoteState.attributes.friendly_name
                    : null;
            titleEl.textContent = friendly || this._config.entity;
        }

        const mp = this._config.media_player_entity;
        const mpState = mp ? this._hass.states[mp] : null;
        const sources =
            mpState && mpState.attributes && Array.isArray(mpState.attributes.source_list)
                ? mpState.attributes.source_list
                : [];

        const sourcesKey = sources.join("|");
        if (sourcesKey !== this._lastSourceList) {
            this._lastSourceList = sourcesKey;
            this._renderSources(sources);
        }

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
    }

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
                    // <img> with onerror fallback. If the CDN call fails,
                    // we replace it with an initials badge so the user
                    // still sees what the button does.
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
                // badge fallback
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

    _render() {
        if (!this.shadowRoot) {
            this.attachShadow({ mode: "open" });
        }

        this.shadowRoot.innerHTML = `
            <style>${this._css()}</style>
            <ha-card class="card">
                ${this._config.title !== false ? `<div class="title">${this._config.title || ""}</div>` : ""}
                <div class="remote">

                    <!-- Row 1: List | Power | Keypad -->
                    <div class="row row-3">
                        <button class="btn btn-pill" data-cmd="MENU" aria-label="Menu">${svg(ICONS.list)}</button>
                        <button class="btn btn-pill btn-power" data-toggle aria-label="Power">${svg(ICONS.power)}</button>
                        <button class="btn btn-pill" data-cmd="GUIDE" aria-label="Keypad">${svg(ICONS.keypad)}</button>
                    </div>

                    <!-- D-pad: cross layout -->
                    <div class="dpad">
                        <button class="btn btn-arrow dpad-up" data-cmd="UP" aria-label="Up">${svg(ICONS.up)}</button>
                        <button class="btn btn-arrow dpad-left" data-cmd="LEFT" aria-label="Left">${svg(ICONS.left)}</button>
                        <button class="btn btn-ok dpad-ok" data-cmd="OK">OK</button>
                        <button class="btn btn-arrow dpad-right" data-cmd="RIGHT" aria-label="Right">${svg(ICONS.right)}</button>
                        <button class="btn btn-arrow dpad-down" data-cmd="DOWN" aria-label="Down">${svg(ICONS.down)}</button>
                    </div>

                    <!-- Back | Exit -->
                    <div class="row row-2">
                        <button class="btn btn-pill" data-cmd="BACK">BACK</button>
                        <button class="btn btn-pill" data-cmd="EXIT">EXIT</button>
                    </div>

                    <!-- Sources grid (dynamic) -->
                    <div class="sources-grid">
                        <div class="sources-empty">Loading sources…</div>
                    </div>

                    <!-- Volume column | Center column | Channel column -->
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

                    <!-- Single media row: Previous | Play/Pause | Next -->
                    <div class="row row-3 row-media">
                        <button class="btn btn-pill" data-cmd="PREVIOUS" aria-label="Previous">${svg(ICONS.prev)}</button>
                        <button class="btn btn-pill btn-playpause" data-playpause aria-label="Play">${svg(ICONS.play)}</button>
                        <button class="btn btn-pill" data-cmd="NEXT" aria-label="Next">${svg(ICONS.next)}</button>
                    </div>
                </div>
            </ha-card>
        `;

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

        this._updateState();
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
                margin-bottom: 14px;
                font-weight: 500;
                letter-spacing: 0.5px;
                text-transform: uppercase;
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
            .btn svg {
                width: 22px;
                height: 22px;
            }
            .btn:active {
                transform: scale(0.93);
                background-color: var(--urc-btn-bg-active);
            }
            .btn:focus-visible {
                box-shadow: 0 0 0 3px var(--primary-color, #03a9f4);
            }

            /* D-pad cross */
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
            .btn-arrow:active {
                background-color: var(--urc-btn-bg-active);
            }

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
            .btn-ok:active {
                background-color: var(--urc-btn-bg-active);
            }

            .btn-power {
                color: white;
                background: var(--urc-power-red);
            }
            .btn-power:active {
                background: var(--urc-power-red);
                opacity: 0.85;
            }
            .btn-power.is-on {
                box-shadow: 0 0 0 2px rgba(216,69,74,0.25);
            }

            /* Sources grid: 4 columns, dynamic rows */
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
            .btn-source {
                height: 56px;
                padding: 4px;
            }
            .btn-source svg {
                width: 28px;
                height: 28px;
                fill: var(--primary-text-color);
            }
            /* Brand logo from CDN */
            .btn-source img.logo {
                width: 30px;
                height: 30px;
                object-fit: contain;
                pointer-events: none; /* clicks go through to the button */
            }
            /* Fallback initials pill — also used when <img> errors out */
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

            /* Volume / Channel tracks */
            .col-track {
                display: flex;
                flex-direction: column;
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
            .col-track .btn:active {
                background-color: var(--urc-btn-bg-active);
            }
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

window.customCards = window.customCards || [];
window.customCards.push({
    type: "universal-remote-card",
    name: "Universal Remote Card",
    description:
        "Physical-remote-style card for the Universal Remote integration (Samsung / LG / Apple TV).",
    preview: false,
    documentationURL:
        "https://github.com/ricardogoncalves89/ha-universal-remote#lovelace-card",
});

console.info(
    "%c UNIVERSAL-REMOTE-CARD %c v0.3.0 ",
    "color: white; background: #d8454a; padding: 2px 4px; border-radius: 3px 0 0 3px;",
    "color: white; background: #444; padding: 2px 4px; border-radius: 0 3px 3px 0;",
);

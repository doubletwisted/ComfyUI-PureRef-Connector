import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const ROOT = "/pureref_connector";
const { ComfyButton } = window.comfyAPI.button;

app.registerExtension({
    name: "PureRef.Connector",
    async setup() {
        const style = document.createElement("style");
        style.textContent = `
            .pureref-popup { position:fixed; z-index:2000; width:340px; max-width:calc(100vw - 24px); margin:0;
                max-height:calc(100vh - 24px); overflow:auto;
                padding:18px; border:1px solid var(--border-color,#555); border-radius:10px;
                background:var(--comfy-menu-bg,#252525); color:var(--fg-color,#ddd);
                font-family:inherit; font-size:13px; line-height:1.5; box-shadow:0 12px 36px #0006; }
            .pureref-popup::backdrop { background:transparent; }
            .pureref-popup header { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; }
            .pureref-popup h2 { font-size:16px; margin:0; font-weight:600; }
            .pureref-popup label { display:block; margin:12px 0 5px; }
            .pureref-popup input[type=text], .pureref-popup input[type=number], .pureref-popup select { box-sizing:border-box; width:100%; padding:8px;
                border:1px solid var(--border-color,#555); border-radius:5px; background:var(--comfy-input-bg,#181818); color:inherit; }
            .pureref-popup .pureref-check { display:flex; gap:8px; align-items:center; cursor:pointer; }
            .pureref-popup .pureref-check input { accent-color:#50b7ac; margin:0; }
            .pureref-popup .pureref-enabled { display:flex; align-items:center; justify-content:space-between; gap:12px;
                padding:10px; background:var(--comfy-input-bg,#181818); border-radius:6px; margin:0 0 14px; }
            .pureref-popup .pureref-enabled label { margin:0; }
            .pureref-popup small { display:block; color:var(--descrip-text,#aaa); margin-top:5px; }
            .pureref-popup details { margin-top:14px; }
            .pureref-popup summary { cursor:pointer; color:var(--descrip-text,#aaa); }
            .pureref-popup footer { display:flex; gap:8px; margin-top:18px; }
            .pureref-popup button { padding:7px 12px; border:1px solid var(--border-color,#555); border-radius:5px;
                color:inherit; background:var(--comfy-input-bg,#181818); cursor:pointer; }
            .pureref-popup button[type=submit] { background:#286e67; border-color:#438c84; margin-left:auto; color:#fff; }
            .pureref-popup button:disabled { opacity:.5; cursor:wait; }
            .pureref-popup button:focus-visible, .pureref-popup input:focus-visible, .pureref-popup select:focus-visible,
            .pureref-popup summary:focus-visible { outline:2px solid #50b7ac; outline-offset:2px; }
            .pureref-popup .pureref-close { padding:0 5px; border:0; font-size:20px; background:none; }
            .pureref-popup .pureref-status { margin:14px 0 0; padding-top:12px; border-top:1px solid var(--border-color,#555); overflow-wrap:anywhere; }
            .pureref-popup .pureref-status[data-error=true] { color:#f2b18c; }
            .pureref-toolbar-on { color:#75d5c5 !important; }
        `;
        document.head.append(style);

        const dialog = document.createElement("dialog");
        dialog.className = "pureref-popup";
        dialog.setAttribute("aria-label", "PureRef connector");
        dialog.innerHTML = `
            <form>
                <header><h2>PureRef connector</h2><button type="button" class="pureref-close" aria-label="Close">×</button></header>
                <div class="pureref-enabled">
                    <label class="pureref-check"><input name="enabled" type="checkbox"> Enable</label>
                    <label class="pureref-check" title="Enable the connector when ComfyUI starts."><input name="enable_on_startup" type="checkbox"> Enabled by Default</label>
                </div>
                <label for="pureref-document">PureRef document</label>
                <input id="pureref-document" name="document" type="text" placeholder="C:\\References\\My board.pur" spellcheck="false">
                <small>Created automatically if it doesn't exist.</small>
                <label for="pureref-mode">Save images to</label>
                <select id="pureref-mode" name="mode"><option value="both">PureRef + normal output folder</option><option value="pureref_only">PureRef only</option></select>
                <label for="pureref-columns">Images per row</label>
                <input id="pureref-columns" name="images_per_row" type="number" min="1" step="1" required>
                <label for="pureref-closed">When the PureRef window closes</label>
                <select id="pureref-closed" name="when_closed"><option value="reopen">Reopen on the next image</option><option value="pause">Pause and save images normally</option></select>
                <details><summary>PureRef application</summary><label for="pureref-executable">PureRef.exe path</label>
                    <input id="pureref-executable" name="executable" type="text" spellcheck="false"></details>
                <p class="pureref-status" role="status" aria-live="polite">Loading…</p>
                <footer><button type="button" class="pureref-open">Open board</button><button type="submit">Apply</button></footer>
            </form>`;
        document.body.append(dialog);
        const form = dialog.querySelector("form");
        const status = dialog.querySelector(".pureref-status");
        let settings;
        let dirty = false;
        form.addEventListener("input", () => { dirty = true; });

        function update(data) {
            settings = data;
            button.element.classList.toggle("pureref-toolbar-on", data.enabled);
            button.element.setAttribute("aria-pressed", String(data.enabled));
            status.textContent = data.error || data.status;
            status.dataset.error = String(Boolean(data.error));
        }

        async function request(path, body) {
            const response = await api.fetchApi(`${ROOT}${path}`, body === undefined ? {} : {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "PureRef request failed.");
            return data;
        }

        const fields = () => [...form.elements].filter(element => element.name);

        function values() {
            return Object.fromEntries(fields().map(element => [element.name,
                element.type === "checkbox" ? element.checked : element.type === "number" ? element.valueAsNumber : element.value.trim()]));
        }

        async function populate() {
            update(await request("/settings"));
            for (const element of fields()) element[element.type === "checkbox" ? "checked" : "value"] = settings[element.name];
            dirty = false;
        }

        function error(message) {
            status.textContent = message;
            status.dataset.error = "true";
        }

        async function show() {
            if (dialog.open) { dialog.close(); return; }
            const rect = button.element.getBoundingClientRect();
            dialog.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - 376))}px`;
            dialog.style.top = `${Math.min(rect.bottom + 8, Math.max(12, innerHeight - 580))}px`;
            dialog.show();
            button.element.setAttribute("aria-expanded", "true");
            try { await populate(); } catch (e) { error(e.message); }
        }

        const button = new ComfyButton({ icon: "view-grid-outline", content: "PureRef", tooltip: "PureRef connector", action: show });
        button.element.setAttribute("aria-haspopup", "dialog");
        button.element.setAttribute("aria-expanded", "false");
        app.menu.settingsGroup.append(button);
        dialog.querySelector(".pureref-close").onclick = () => dialog.close();
        dialog.addEventListener("close", () => button.element.setAttribute("aria-expanded", "false"));
        document.addEventListener("pointerdown", event => {
            if (dialog.open && !dialog.contains(event.target) && !button.element.contains(event.target)) dialog.close();
        });
        dialog.addEventListener("keydown", event => {
            event.stopPropagation();
            if (event.key === "Escape") { event.preventDefault(); dialog.close(); button.element.focus(); }
        });

        form.onsubmit = async event => {
            event.preventDefault();
            const submit = form.querySelector("[type=submit]");
            submit.disabled = true;
            try {
                update(await request("/settings", values()));
                dirty = false;
                dialog.close();
                button.element.focus();
            } catch (e) { error(e.message); }
            finally { submit.disabled = false; }
        };
        dialog.querySelector(".pureref-open").onclick = async event => {
            event.target.disabled = true;
            try { update(await request("/open", values())); dirty = false; }
            catch (e) { error(e.message); }
            finally { event.target.disabled = false; }
        };

        api.addEventListener("pureref_status", event => {
            update(event.detail);
            if (!dirty) form.elements.enabled.checked = event.detail.enabled;
            if (event.detail.warning) app.extensionManager.toast.add({ severity: "warn", summary: "PureRef", detail: event.detail.error, life: 6000 });
        });
        try { update(await request("/settings")); } catch (e) { console.warn("PureRef:", e.message); }
    },
});

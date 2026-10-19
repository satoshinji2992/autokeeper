"use strict";

(async () => {
    const canvas = document.getElementById("terminal");
    const ctx = canvas.getContext("2d", {alpha: false});
    if (!ctx) return;
    const input = document.getElementById("command");
    const reader = document.getElementById("reader");
    const viewer = document.getElementById("image-viewer");
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    const pages = ["home", "about", "gallery", "modules", "party", "install", "credits"];
    const labels = ["返回标题", "项目介绍", "实机画面", "模组与规则", "人物与联机", "安装开团", "文档与署名"];
    const themes = [
        {name: "琥珀", bg: "#090705", panel: "#100d08", fg: "#e6cda2", dim: "#9b8668", line: "#75582f", key: "#cda464", red: "#d35436"},
        {name: "磷绿", bg: "#030806", panel: "#06100a", fg: "#b9d9b7", dim: "#729078", line: "#315c3d", key: "#69bb7a", red: "#96d6a3"},
        {name: "血红", bg: "#090504", panel: "#150907", fg: "#dcbba6", dim: "#a17f70", line: "#744236", key: "#bc6853", red: "#e06040"},
        {name: "暮紫", bg: "#070609", panel: "#100c15", fg: "#cfc1dc", dim: "#94849e", line: "#594966", key: "#b49ac7", red: "#c99aaf"}
    ];
    const models = [
        {label: "Claude", command: "uv run autokeeper", note: "使用本机 Claude Code 登录，或 ANTHROPIC_API_KEY。"},
        {label: "DeepSeek", command: "uv run autokeeper --preset deepseek", note: "先设置 DEEPSEEK_API_KEY。"},
        {label: "Codex", command: "uv run autokeeper --model codex:default", note: "使用本机安装并已登录的 Codex CLI。"},
        {label: "Ollama", command: "uv run autokeeper --model ollama:qwen3:14b --search none", note: "先下载模型，离线时关闭 AI 画图。"}
    ];
    const state = {page: "home", menu: 1, theme: 0, image: 0, model: 0, scroll: 0, targetScroll: 0, maxScroll: 0, help: false, command: false, suggestion: 0, muted: true, lamp: true, notice: "", noticeUntil: 0, transition: 0, pointer: null, reader: false};
    let art = null, frames = [], artCache = null, brightCache = null, dirty = true;
    let cw = 9, ch = 22, cols = 100, rows = 40, fontSize = 16, width = 0, height = 0;
    let hits = [], layout = null, lastDraw = 0, start = performance.now(), raf = 0, composing = false;
    let audio = null, noiseSource = null, master = null;
    const imageCache = new Map();
    const metric = document.createElement("canvas").getContext("2d");
    const fontFamily = '"AK Pixel", "Noto Sans Mono CJK SC", monospace';

    function palette() { return themes[state.theme]; }
    function wide(char) { const n = char.codePointAt(0); return n >= 0x2e80 && !(n >= 0xff61 && n <= 0xffdc) ? 2 : 1; }
    function length(text) { return [...text].reduce((sum, char) => sum + wide(char), 0); }
    function fit(text, size) { let out = "", used = 0; for (const char of text) { if (used + wide(char) > size) break; out += char; used += wide(char); } return out; }
    function wrap(text, size) {
        const lines = []; let line = "", used = 0;
        const closing = /^[，。；：！？、）》」』】”’…,.!?;:)\]]/;
        const opening = /[（《「『【“‘(\[]$/;
        for (const char of text) {
            if (char === "\n") { lines.push(line); line = ""; used = 0; continue; }
            if (used + wide(char) > size) {
                let carry = "";
                if (closing.test(char) || opening.test(line)) {
                    const previous = [...line]; carry = previous.pop() || "";
                    while (previous.length && closing.test(carry)) carry = previous.pop() + carry;
                    line = previous.join("");
                }
                lines.push(line); line = carry; used = length(carry);
            }
            line += char; used += wide(char);
        }
        if (line || !lines.length) lines.push(line);
        return lines;
    }
    function fill(x, y, w, h, color) { ctx.fillStyle = color; ctx.fillRect(Math.round(x * cw), Math.round(y * ch), Math.ceil(w * cw), Math.ceil(h * ch)); }
    function text(value, x, y, color = palette().fg, max = cols - x, bold = false) {
        ctx.fillStyle = color; ctx.font = `400 ${fontSize}px ${fontFamily}`;
        let column = x;
        for (const char of value) {
            const cells = wide(char);
            if (column + cells > x + max) break;
            if (char !== " ") ctx.fillText(char, Math.round(column * cw), Math.round((y + .78) * ch), cells * cw + .4);
            column += cells;
        }
    }
    function center(value, y, color = palette().fg, within = cols, x = 0) { text(value, Math.max(x, x + Math.floor((within - length(value)) / 2)), y, color, within); }
    function box(x, y, w, h, title = "", color = palette().line) {
        ctx.strokeStyle = color; ctx.lineWidth = 1;
        ctx.strokeRect(Math.round((x + .5) * cw) + .5, Math.round((y + .5) * ch) + .5, Math.round((w - 1) * cw), Math.round((h - 1) * ch));
        if (title) { const label = ` ${title} `; fill(x + 2, y, length(label), 1, palette().bg); text(label, x + 2, y, palette().key, w - 5); }
    }
    function hit(x, y, w, h, action, label, hover = null) { hits.push({x: x * cw, y: y * ch, w: w * cw, h: h * ch, action, label, hover}); }
    function button(label, x, y, action, active = false, max = cols - x) {
        const value = fit(`[ ${label} ]`, max); const w = length(value);
        if (active) fill(x, y, w, 1, palette().key);
        text(value, x, y, active ? palette().bg : palette().key, w, active);
        hit(x, y, w, 1, action, label);
        return w;
    }
    function announce(value, duration = 3200) {
        state.notice = value; state.noticeUntil = performance.now() + duration; dirty = true;
        document.getElementById("announcement").textContent = value;
    }
    function loadImage(index) {
        const frame = frames[index]; if (!frame) return null;
        if (!imageCache.has(frame.src)) {
            const image = new Image(); image.onload = () => { dirty = true; }; image.onerror = () => announce("图片暂时未载入，可按回车打开原图。"); image.src = frame.src; imageCache.set(frame.src, image);
        }
        return imageCache.get(frame.src);
    }
    function screen(page, updateHash = true) {
        if (!pages.includes(page)) return;
        state.page = page; if (page !== "home") state.menu = pages.indexOf(page);
        state.scroll = state.targetScroll = 0; state.maxScroll = 0; state.help = false;
        state.transition = reduced.matches ? 0 : performance.now(); dirty = true;
        if (updateHash) history.replaceState(null, "", `#${page}`);
        canvas.dataset.page = page;
        document.getElementById("announcement").textContent = labels[pages.indexOf(page)];
        if (page === "gallery") loadImage(state.image);
        tone();
    }
    function selectImage(index) { if (!frames.length) return; state.image = (index + frames.length) % frames.length; loadImage(state.image); dirty = true; tone(); }
    function changeTheme() { state.theme = (state.theme + 1) % themes.length; dirty = true; announce(`配色：${palette().name}`); }
    function scroll(amount) { state.targetScroll = Math.max(0, Math.min(state.maxScroll, state.targetScroll + amount)); dirty = true; }
    function openImage() {
        const frame = frames[state.image]; if (!frame) return;
        document.getElementById("viewer-image").src = frame.src;
        document.getElementById("viewer-image").alt = frame.caption;
        document.getElementById("viewer-caption").textContent = frame.caption;
        document.getElementById("viewer-original").href = frame.src;
        viewer.showModal();
    }
    document.getElementById("viewer-close").addEventListener("click", () => viewer.close());
    viewer.addEventListener("close", () => { canvas.focus({preventScroll: true}); dirty = true; });

    function resize() {
        width = innerWidth; height = window.visualViewport?.height || innerHeight;
        fontSize = width < 500 ? 12 : width < 900 ? 16 : 18;
        metric.font = `${fontSize}px ${fontFamily}`; cw = metric.measureText("M").width; ch = Math.round(fontSize * 1.45);
        cols = Math.max(32, Math.floor(width / cw)); rows = Math.max(14, Math.floor(height / ch));
        const dpr = Math.min(devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.textBaseline = "alphabetic";
        input.style.left = `${3 * cw}px`; input.style.top = `${(rows - 3) * ch}px`; input.style.width = `${(cols - 7) * cw}px`; input.style.height = `${ch}px`; input.style.fontSize = `${fontSize}px`;
        artCache = brightCache = null; dirty = true;
        canvas.dataset.columns = cols; canvas.dataset.rows = rows;
    }
    function makeArt() {
        if (!art) return;
        const source = art.scenes[cols < 90 ? "tall" : "wide"];
        const result = document.createElement("canvas"); result.width = Math.ceil(cols * cw); result.height = Math.ceil(rows * ch);
        const painter = result.getContext("2d"); painter.font = `${fontSize}px ${fontFamily}`; painter.textBaseline = "alphabetic";
        const scale = Math.max(cols / source.cols, rows / source.rows);
        const sx = (source.cols - cols / scale) / 2;
        const sy = Math.max(0, Math.min(source.rows - rows / scale, source.rows * .28 - rows * .28 / scale));
        for (let row = 0; row < rows; row++) {
            const sourceRow = source.cells[Math.min(source.rows - 1, Math.floor(sy + row / scale))];
            for (let col = 0; col < cols; col++) {
                const cell = sourceRow?.[Math.min(source.cols - 1, Math.max(0, Math.floor(sx + col / scale)))];
                if (!cell) continue;
                painter.fillStyle = source.palette[cell[2]]; painter.fillRect(col * cw, row * ch, cw + .5, ch + .5);
                if (cell[0] !== " ") { painter.fillStyle = source.palette[cell[1]]; painter.fillText(cell[0], col * cw, (row + .78) * ch, cw + .4); }
            }
        }
        artCache = result;
        brightCache = document.createElement("canvas"); brightCache.width = result.width; brightCache.height = result.height;
        const bright = brightCache.getContext("2d"); bright.filter = "brightness(2.2) saturate(.7)"; bright.drawImage(result, 0, 0);
        canvas.dataset.sceneReady = "true";
    }
    const beam = document.createElement("canvas");
    function background(now) {
        fill(0, 0, cols + 1, rows + 1, palette().bg);
        if (art && !artCache) makeArt();
        if (artCache) {
            ctx.globalAlpha = state.page === "home" ? .92 : .18;
            for (let r = 0; r < rows; r++) {
                const depth = Math.max(0, (r / rows - .4) / .6);
                const shift = reduced.matches ? 0 : Math.round(Math.sin(now / 2300 + r * .22) * depth * 1.1) * cw;
                ctx.drawImage(artCache, 0, r * ch, artCache.width, ch, shift, r * ch, artCache.width, ch + .2);
            }
            ctx.globalAlpha = 1;
            if (state.page === "home" && state.lamp && state.pointer) {
                beam.width = artCache.width; beam.height = artCache.height;
                const lamp = beam.getContext("2d"); lamp.drawImage(brightCache, 0, 0);
                lamp.globalCompositeOperation = "destination-in";
                const radius = Math.min(width, height) * .26;
                const gradient = lamp.createRadialGradient(state.pointer.x, state.pointer.y, 0, state.pointer.x, state.pointer.y, radius);
                gradient.addColorStop(0, "#ffffffcc"); gradient.addColorStop(.35, "#ffffff77"); gradient.addColorStop(1, "#ffffff00");
                lamp.fillStyle = gradient; lamp.fillRect(0, 0, beam.width, beam.height); ctx.drawImage(beam, 0, 0);
            }
        }
        const vignette = ctx.createRadialGradient(width * .52, height * .43, height * .12, width * .5, height * .5, Math.max(width, height) * .69);
        vignette.addColorStop(0, "#05030200"); vignette.addColorStop(1, "#050302d9"); ctx.fillStyle = vignette; ctx.fillRect(0, 0, width, height);
        if (!reduced.matches && state.page === "home") {
            for (let i = 0; i < 34; i++) {
                const x = ((i * 19.7 + now / 1800) % cols);
                const y = rows * (.68 + .18 * Math.sin(i * 1.7 + now / 9000));
                ctx.globalAlpha = .06 + .05 * Math.sin(i + now / 4300); text(i % 3 ? "." : "~", x, y, "#d1bc9a", 1);
            }
            ctx.globalAlpha = 1;
        }
    }
    function home(now) {
        const p = palette(); const contentRows = rows - 5;
        const quote = "我以为，世上最仁慈的事，莫过于人类的心智无法把它所知的一切关联起来。";
        const lines = wrap(quote, Math.min(cols - 10, 76));
        const quoteY = contentRows > 35 ? 4 : 2;
        if (contentRows > 27) {
            wrap("Ph'nglui mglw'nafh Cthulhu R'lyeh wgah'nagl fhtagn.", cols - 6).forEach((line, i) => center(line, quoteY + i, p.dim));
            lines.forEach((line, i) => center(line, quoteY + 2 + i, "#d4bf9d"));
            center("H. P. 洛夫克拉夫特 ·《克苏鲁的呼唤》", quoteY + 3 + lines.length, p.dim);
        }
        const logo = rows < 24 ? ["AUTOKEEPER"] : art?.logos[cols > 110 ? "wide" : "small"] || ["A U T O K E E P E R"];
        const logoWidth = Math.max(...logo.map(length));
        const scale = Math.min(1, (cols - 6) / logoWidth);
        const logoY = rows < 24 ? 2 : contentRows > 35 ? Math.floor(contentRows * .31) : contentRows > 27 ? Math.floor(contentRows * .34) : 3;
        ctx.save(); ctx.translate(Math.round((cols - logoWidth * scale) / 2 * cw), logoY * ch); ctx.scale(scale, scale);
        logo.forEach((line, i) => {
            const red = ["#ed6a43", "#d65434", "#b5412b", "#92301e"][Math.min(3, Math.floor(i * 4 / logo.length))];
            ctx.shadowColor = "#a12a1933"; ctx.shadowBlur = reduced.matches ? 0 : 3;
            text(line, 0, i, state.theme === 0 ? red : p.red, logoWidth);
        });
        ctx.restore();
        const taglineY = Math.ceil(logoY + logo.length * scale + 1);
        if (taglineY < rows - 13) center("自动守秘人 · CoC 7 版中文跑团", taglineY, p.fg);
        const menuY = Math.min(rows - 10, Math.max(taglineY + 2, Math.min(Math.floor(contentRows * .58), rows - 12)));
        const menuW = Math.min(38, cols - 6), menuX = Math.floor((cols - menuW) / 2);
        fill(menuX, menuY - 1, menuW, 8, "#080604c9");
        box(menuX, menuY - 1, menuW, 8, "开始阅读", p.line);
        for (let i = 1; i <= 6; i++) {
            const selected = state.menu === i;
            if (selected) fill(menuX + 2, menuY + i - 1, menuW - 4, 1, p.key);
            const label = `${selected ? ">" : " "} ${i}  ${labels[i]}`;
            text(label, menuX + 3, menuY + i - 1, selected ? p.bg : p.fg, menuW - 6, selected);
            hit(menuX + 2, menuY + i - 1, menuW - 4, 1, () => screen(pages[i]), labels[i], () => { if (state.menu !== i) { state.menu = i; tone(); } });
        }
        if (menuY + 9 < rows - 3) center(cols > 70 ? "自由调查 / 模组编译 / AI 同伴 / 浏览器联机" : "自由调查 · 模组编译 · 好友联机", menuY + 9, p.dim);
    }

    function mainLayout() {
        const sidebar = cols >= 96 ? 25 : 0;
        layout = {x: sidebar ? sidebar + 1 : 1, y: sidebar ? 3 : 4, w: cols - sidebar - 2, h: rows - (sidebar ? 6 : 7)};
        const p = palette();
        if (sidebar) {
            fill(1, 3, sidebar - 1, rows - 6, "#080604e8");
            box(1, 3, sidebar - 1, rows - 6, "目录");
            for (let i = 0; i <= 6; i++) {
                const y = 5 + i * 2;
                if (y >= rows - 5) break;
                const selected = state.page === pages[i];
                if (selected) fill(3, y, sidebar - 5, 1, p.key);
                text(`${selected ? ">" : " "} ${i || "Esc"} ${labels[i]}`, 3, y, selected ? p.bg : p.fg, sidebar - 5);
                hit(3, y, sidebar - 5, 1, () => screen(pages[i]), labels[i]);
            }
            if (rows > 31) {
                text("网页展示 / 0.0.2", 3, rows - 14, p.dim, sidebar - 5);
                text(`T 配色  ${p.name}`, 3, rows - 11, p.key, sidebar - 5);
                hit(3, rows - 11, sidebar - 5, 1, changeTheme, "切换配色");
                text(`M 声景  ${state.muted ? "关闭" : "开启"}`, 3, rows - 9, p.key, sidebar - 5);
                hit(3, rows - 9, sidebar - 5, 1, toggleAudio, "切换声景");
                text("? 操作说明", 3, rows - 7, p.key, sidebar - 5);
                hit(3, rows - 7, sidebar - 5, 1, () => { state.help = true; }, "操作说明");
            }
        } else {
            for (let i = 1; i <= 6; i++) {
                const label = `${i}${["", "介绍", "图集", "模组", "同伴", "安装", "文档"][i]}`;
                const x = 1 + Math.floor((i - 1) * (cols - 2) / 6);
                if (state.page === pages[i]) fill(x, 2, 7, 1, p.key);
                text(label, x, 2, state.page === pages[i] ? p.bg : p.key, 7);
                hit(x, 2, 7, 1, () => screen(pages[i]), labels[i]);
            }
        }
        fill(layout.x, layout.y, layout.w, layout.h, p.panel + "f2");
        box(layout.x, layout.y, layout.w, layout.h, labels[pages.indexOf(state.page)]);
    }
    function pageLines() {
        const section = document.getElementById(state.page);
        const result = [];
        const max = Math.max(14, layout.w - 6);
        for (const node of section.children) {
            if (!["H2", "H3", "P", "PRE"].includes(node.tagName)) continue;
            let value = node.textContent.trim();
            if (node.id === "launch-code") value = models[state.model].command;
            const color = node.tagName.startsWith("H") ? "heading" : node.tagName === "PRE" ? "code" : "body";
            if (node.tagName === "PRE") value = value.split("\n").map(line => `  ${line}`).join("\n");
            wrap(value, max).forEach(line => result.push({text: line, color}));
            if (node.id === "launch-code") wrap(models[state.model].note, max).forEach(line => result.push({text: line, color: "body"}));
            result.push({text: "", color: "body"});
        }
        return result;
    }
    function openLink(href) {
        if (href.startsWith("#")) { screen(href.slice(1)); return; }
        window.open(href, "_blank", "noopener,noreferrer");
    }
    function documentPage(now) {
        const p = palette(); const lines = pageLines();
        const actionRows = [[]]; let actionWidth = 0;
        for (const anchor of document.getElementById(state.page).querySelectorAll("[data-action]")) {
            const width = Math.min(layout.w - 4, length(anchor.textContent) + 4);
            if (actionWidth && actionWidth + width > layout.w - 4) { actionRows.push([]); actionWidth = 0; }
            actionRows.at(-1).push(anchor); actionWidth += width + 2;
        }
        let visible = layout.h - (state.page === "install" ? 8 + actionRows.length : 4 + actionRows.length);
        visible = Math.max(3, visible);
        state.maxScroll = Math.max(0, lines.length - visible);
        state.targetScroll = Math.min(state.maxScroll, state.targetScroll);
        state.scroll = reduced.matches ? state.targetScroll : state.scroll + (state.targetScroll - state.scroll) * .32;
        if (Math.abs(state.scroll - state.targetScroll) < .08) state.scroll = state.targetScroll;
        const offset = Math.round(state.scroll);
        ctx.save(); ctx.beginPath(); ctx.rect((layout.x + 2) * cw, (layout.y + 2) * ch, (layout.w - 4) * cw, visible * ch); ctx.clip();
        for (let i = 0; i < visible; i++) {
            const line = lines[offset + i]; if (!line) break;
            const color = line.color === "heading" ? p.key : line.color === "code" ? "#d6b476" : p.fg;
            if (line.color === "code") fill(layout.x + 2, layout.y + 2 + i, layout.w - 4, 1, "#070503");
            text(line.text, layout.x + 3, layout.y + 2 + i, color, layout.w - 6, line.color === "heading");
        }
        ctx.restore();
        if (state.maxScroll) {
            const sy = layout.y + 2; const track = visible;
            fill(layout.x + layout.w - 2, sy, 1, track, p.bg);
            const thumb = Math.max(1, Math.floor(track * visible / lines.length));
            const at = Math.round((track - thumb) * offset / state.maxScroll);
            fill(layout.x + layout.w - 2, sy + at, 1, thumb, p.line);
            hit(layout.x + layout.w - 2, sy, 1, track / 2, () => scroll(-visible + 1), "上一页");
            hit(layout.x + layout.w - 2, sy + track / 2, 1, track / 2, () => scroll(visible - 1), "下一页");
        }
        let actionY = layout.y + layout.h - 3 - actionRows.length + 1;
        if (state.page === "install") {
            actionY--;
            const half = cols < 70;
            models.forEach((model, index) => {
                const slot = half ? index % 2 : index; const y = actionY - (half ? 3 - Math.floor(index / 2) : 2);
                const x = layout.x + 2 + slot * (half ? Math.floor((layout.w - 4) / 2) : Math.floor((layout.w - 4) / 4));
                button(model.label, x, y, () => { state.model = index; dirty = true; }, state.model === index, Math.floor((layout.w - 4) / (half ? 2 : 4)) - 1);
            });
            button("C 复制安装与启动", layout.x + 2, actionY, copyInstall, false, layout.w - 4);
            actionY++;
        }
        actionRows.forEach((anchors, row) => {
            let x = layout.x + 2;
            for (const anchor of anchors) x += button(anchor.textContent, x, actionY + row, () => openLink(anchor.getAttribute("href")), false, layout.w - 4) + 2;
        });
        text(`${offset + 1}-${Math.min(offset + visible, lines.length)} / ${lines.length} 行`, layout.x + 2, layout.y + layout.h - 2, p.dim, layout.w - 4);
    }
    function galleryPage() {
        const p = palette(); const frame = frames[state.image];
        if (!frame) { text("载入图集…", layout.x + 3, layout.y + 3); return; }
        const counter = `${String(state.image + 1).padStart(2, "0")} / ${frames.length}  ${frame.label}`;
        text(counter, layout.x + 2, layout.y + 2, p.key, layout.w - 4);
        const top = (layout.y + 4) * ch, left = (layout.x + 2) * cw;
        const availableW = (layout.w - 4) * cw, availableH = Math.max(ch * 2, (layout.h - 10) * ch);
        const image = loadImage(state.image);
        if (image?.complete && image.naturalWidth) {
            const scale = Math.min(availableW / frame.width, availableH / frame.height);
            const w = frame.width * scale, h = frame.height * scale;
            ctx.imageSmoothingEnabled = true;
            ctx.drawImage(image, left + (availableW - w) / 2, top + (availableH - h) / 2, w, h);
            hit(left / cw, top / ch, availableW / cw, availableH / ch, openImage, "放大原图");
        } else text("载入实机画面…", layout.x + 3, layout.y + 6, p.dim);
        wrap(frame.caption, layout.w - 5).slice(0, 2).forEach((line, i) => text(line, layout.x + 2, layout.y + layout.h - 5 + i, p.dim, layout.w - 4));
        const y = layout.y + layout.h - 2;
        button("←", layout.x + 2, y, () => selectImage(state.image - 1));
        button("查看原图", layout.x + 9, y, openImage, false, layout.w - 19);
        button("→", layout.x + layout.w - 7, y, () => selectImage(state.image + 1));
        canvas.dataset.frame = state.image;
    }
    function chrome(now) {
        const p = palette();
        fill(0, 0, cols, 1, "#0a0705");
        fill(0, 0, 15, 1, p.key); text(" AUTOKEEPER ", 1, 0, p.bg, 13, true);
        text(cols > 80 ? " CoC 7 / 项目展示 / 0.0.2" : " 项目展示 0.0.2", 16, 0, p.dim, cols - 16);
        if (cols > 72) button("G GitHub", cols - 15, 0, () => openLink("https://github.com/satoshinji2992/autokeeper"), false, 15);
        fill(0, rows - 2, cols, 2, "#0a0705");
        if (now < state.noticeUntil) text(state.notice, 1, rows - 2, p.key, cols - 2);
        const tools = cols > 85 ? [["/ 命令", openCommand], ["T 配色", changeTheme], [state.muted ? "M 声景:关" : "M 声景:开", toggleAudio], ["F 全屏", fullscreen], ["? 帮助", () => { state.help = true; }]] : [["/ 命令", openCommand], ["T 配色", changeTheme], [state.muted ? "M 静音" : "M 声景", toggleAudio], ["? 帮助", () => { state.help = true; }]];
        let x = 1;
        for (const [label, action] of tools) {
            const max = Math.max(0, cols - x - 1); if (max < length(label)) break;
            text(label, x, rows - 1, p.key, max);
            hit(x, rows - 1, length(label), 1, action, label); x += length(label) + (cols < 65 ? 2 : 4);
        }
        canvas.dataset.theme = p.name; canvas.dataset.muted = state.muted; canvas.dataset.lamp = state.lamp;
    }
    function helpPanel() {
        const p = palette(); const w = Math.min(cols - 4, 76), h = Math.min(rows - 4, 24), x = Math.floor((cols - w) / 2), y = Math.floor((rows - h) / 2);
        hits = []; fill(x - 1, y - 1, w + 2, h + 2, "#050302e8"); fill(x, y, w, h, p.panel); box(x, y, w, h, "操作说明", p.key);
        const tips = ["1-6    介绍 / 图集 / 模组 / 同伴 / 安装 / 文档", "方向键  标题页选菜单；正文滚动；图集切图", "Enter   进入菜单 / 放大实机图", "PgUp/Dn 正文翻页；鼠标滚轮和触屏拖动也可", "/       输入命令，Tab 补全", "T       切换琥珀、磷绿、血红、暮紫", "L       手电开关，移动指针照亮字符画", "M       声景开关（默认静音）", "F       全屏；Esc 返回标题或关闭窗口", "C / P   安装页：复制命令 / 切换模型", "G       GitHub 源码", "Tab     可进入文字模式，阅读完整文档"];
        let line = y + 2;
        for (const tip of tips) {
            for (const value of wrap(tip, w - 6)) { if (line < y + h - 3) text(value, x + 3, line++, p.fg, w - 6); }
        }
        button("Esc 关闭", x + 3, y + h - 2, () => { state.help = false; }, false, w - 6);
        canvas.dataset.help = "true";
    }
    const commands = [
        {name: "about", label: "项目介绍", run: () => screen("about")},
        {name: "gallery", label: "十二张实机图", run: () => screen("gallery")},
        {name: "modules", label: "模组编译与插件", run: () => screen("modules")},
        {name: "party", label: "车卡与好友联机", run: () => screen("party")},
        {name: "install", label: "安装与启动", run: () => screen("install")},
        {name: "credits", label: "文档与图片署名", run: () => screen("credits")},
        {name: "theme", label: "切换配色", run: changeTheme},
        {name: "sound", label: "声景开关", run: toggleAudio},
        {name: "home", label: "返回标题", run: () => screen("home")},
        {name: "help", label: "查看全部操作", run: () => { state.help = true; }},
        {name: "github", label: "打开源码", run: () => openLink("https://github.com/satoshinji2992/autokeeper")}
    ];
    function commandMatches() { const value = input.value.replace(/^[/：:]*/, "").toLowerCase().trim(); return commands.filter(item => item.name.startsWith(value) || item.label.includes(value)); }
    function openCommand() { state.command = true; state.suggestion = 0; input.hidden = false; input.value = "/"; input.focus({preventScroll: true}); dirty = true; }
    function closeCommand() { state.command = false; input.hidden = true; input.blur(); canvas.focus({preventScroll: true}); dirty = true; }
    function executeCommand(item = null) { const chosen = item || commandMatches()[state.suggestion]; closeCommand(); if (chosen) chosen.run(); else announce("没有这个命令，试试 /help。"); }
    function commandPanel(now) {
        hits = [];
        const p = palette(); const matches = commandMatches(); const shown = Math.min(6, matches.length); state.suggestion = Math.min(state.suggestion, Math.max(0, matches.length - 1));
        const top = rows - shown - 6;
        fill(1, top, cols - 2, shown + 5, p.panel); box(1, top, cols - 2, shown + 5, "命令 / Tab 补全", p.key);
        const startAt = Math.max(0, state.suggestion - shown + 1);
        matches.slice(startAt, startAt + shown).forEach((item, index) => {
            const selected = state.suggestion === startAt + index;
            if (selected) fill(3, top + 2 + index, cols - 6, 1, p.key);
            text(`/${item.name}  ${item.label}`, 4, top + 2 + index, selected ? p.bg : p.fg, cols - 8);
            hit(3, top + 2 + index, cols - 6, 1, () => executeCommand(item), item.label);
        });
        fill(3, rows - 3, cols - 6, 1, p.bg);
        const visible = input.value.slice(-Math.max(10, cols - 9));
        text(`> ${visible}${Math.floor(now / 550) % 2 ? " " : "_"}`, 3, rows - 3, p.key, cols - 6);
    }
    async function copyInstall() {
        const value = `${document.getElementById("install-code").textContent}\n${models[state.model].command}`;
        try { await navigator.clipboard.writeText(value); announce("安装和启动命令已复制。"); }
        catch { toggleReader(true); const range = document.createRange(); range.selectNodeContents(document.getElementById("install-code")); getSelection().removeAllRanges(); getSelection().addRange(range); announce("请在文字模式中复制命令。"); }
    }
    async function toggleAudio() {
        if (!audio) {
            const Audio = window.AudioContext || window.webkitAudioContext;
            if (!Audio) { announce("当前浏览器不支持声景。"); return; }
            audio = new Audio(); master = audio.createGain(); master.gain.value = 0;
            const filter = audio.createBiquadFilter(); filter.type = "lowpass"; filter.frequency.value = 380;
            const buffer = audio.createBuffer(1, audio.sampleRate * 3, audio.sampleRate); const data = buffer.getChannelData(0);
            let previous = 0;
            for (let i = 0; i < data.length; i++) { previous = (previous + (Math.random() * 2 - 1) * .025) / 1.025; data[i] = previous * 3; }
            noiseSource = audio.createBufferSource(); noiseSource.buffer = buffer; noiseSource.loop = true; noiseSource.connect(filter); filter.connect(master); master.connect(audio.destination); noiseSource.start();
        }
        try { await audio.resume(); state.muted = !state.muted; master.gain.setTargetAtTime(state.muted ? 0 : .12, audio.currentTime, .3); announce(state.muted ? "声景已关闭。" : "声景已开启：低风与远处的电流声。"); }
        catch { announce("点击声景开关后可开启声音。"); }
    }
    function tone() {
        if (!audio || state.muted || audio.state !== "running") return;
        const oscillator = audio.createOscillator(), gain = audio.createGain();
        oscillator.type = "sine"; oscillator.frequency.setValueAtTime(420, audio.currentTime); oscillator.frequency.exponentialRampToValueAtTime(210, audio.currentTime + .045);
        gain.gain.setValueAtTime(.025, audio.currentTime); gain.gain.exponentialRampToValueAtTime(.0001, audio.currentTime + .055);
        oscillator.connect(gain); gain.connect(master); oscillator.start(); oscillator.stop(audio.currentTime + .06);
    }
    async function fullscreen() {
        try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
        catch { announce("可使用浏览器的全屏功能。"); }
    }
    function toggleReader(force = null) {
        state.reader = force === null ? !state.reader : force;
        document.body.classList.toggle("canvas-mode", !state.reader);
        const toggle = document.getElementById("reader-toggle"); toggle.textContent = state.reader ? "返回字符桌面" : "切换文字模式"; toggle.classList.toggle("reader-visible", state.reader);
        if (state.reader) { reader.removeAttribute("hidden"); window.scrollTo(0, 0); }
        else { canvas.focus({preventScroll: true}); resize(); }
    }
    document.getElementById("reader-toggle").addEventListener("click", () => toggleReader());

    function render(now) {
        hits = []; background(now);
        if (state.page === "home") home(now);
        else { mainLayout(); if (state.page === "gallery") galleryPage(); else documentPage(now); }
        if (state.transition && !reduced.matches) {
            const elapsed = now - state.transition;
            if (elapsed < 220) {
                const amount = 1 - elapsed / 220;
                for (let r = 2; r < rows - 3; r++) if (((r * 37) % 101) / 101 < amount * .72) fill(1, r, cols - 2, .12, "#07050388");
            } else state.transition = 0;
        }
        chrome(now); canvas.dataset.help = "false";
        if (state.help) helpPanel();
        if (state.command) commandPanel(now);
        canvas.dataset.page = state.page;
        canvas.dataset.model = models[state.model].label;
        canvas.dataset.scrollRow = Math.round(state.scroll);
        canvas.dataset.command = state.command;
        canvas.regions = hits.map(({x, y, w, h, label}) => ({x, y, w, h, label}));
        dirty = false;
    }
    function tick(now) {
        raf = 0;
        if (document.hidden) return;
        const animated = !reduced.matches && !state.reader && (state.page === "home" || state.transition || Math.abs(state.scroll - state.targetScroll) > .05 || state.command);
        if (!state.reader && now - lastDraw >= 40 && (dirty || animated || now < state.noticeUntil + 60)) { render(now); lastDraw = now; }
        raf = requestAnimationFrame(tick);
    }
    function activeHit(x, y) { return [...hits].reverse().find(item => x >= item.x && x < item.x + item.w && y >= item.y && y < item.y + item.h); }
    let touchStart = null, touchMoved = false, lastWheel = 0;
    canvas.addEventListener("pointerdown", event => { if (state.command) input.focus({preventScroll: true}); else canvas.focus({preventScroll: true}); if (event.pointerType !== "mouse") { canvas.setPointerCapture(event.pointerId); touchStart = {x: event.clientX, y: event.clientY, last: event.clientY}; touchMoved = false; } });
    canvas.addEventListener("pointercancel", () => { touchStart = null; touchMoved = false; });
    canvas.addEventListener("pointermove", event => {
        state.pointer = {x: event.clientX, y: event.clientY}; dirty = true;
        if (touchStart && event.pointerType !== "mouse") {
            if (Math.abs(event.clientY - touchStart.y) + Math.abs(event.clientX - touchStart.x) > 10) touchMoved = true;
            if (!state.help && !state.command && state.page !== "home" && state.page !== "gallery") scroll((touchStart.last - event.clientY) / ch);
            touchStart.last = event.clientY; return;
        }
        const item = activeHit(event.clientX, event.clientY); canvas.style.cursor = item ? "pointer" : state.page === "home" ? "crosshair" : "default"; item?.hover?.();
    });
    canvas.addEventListener("pointerleave", () => { state.pointer = null; dirty = true; });
    canvas.addEventListener("pointerup", event => {
        if (touchStart && touchMoved) { if (!state.help && !state.command && state.page === "gallery" && Math.abs(event.clientX - touchStart.x) > 32) selectImage(state.image + (event.clientX < touchStart.x ? 1 : -1)); touchStart = null; return; }
        touchStart = null; const item = activeHit(event.clientX, event.clientY); if (item) { item.action(); dirty = true; }
    });
    canvas.addEventListener("wheel", event => {
        event.preventDefault();
        if (state.help || state.command || viewer.open) return;
        if (state.page === "home" || state.page === "gallery") {
            if (performance.now() - lastWheel < 180) return; lastWheel = performance.now();
            if (state.page === "home") { state.menu = (state.menu - 1 + (event.deltaY > 0 ? 1 : 5)) % 6 + 1; dirty = true; }
            else selectImage(state.image + (event.deltaY > 0 ? 1 : -1));
        } else scroll(event.deltaY / ch * .6);
    }, {passive: false});
    input.addEventListener("input", () => { state.suggestion = 0; dirty = true; });
    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => { composing = false; dirty = true; });
    document.addEventListener("keydown", event => {
        if (event.ctrlKey || event.metaKey || event.altKey || viewer.open || state.reader || composing || event.isComposing || document.activeElement.id === "reader-toggle") return;
        const key = event.key;
        if (state.command) {
            if (key === "Escape") { event.preventDefault(); closeCommand(); }
            else if (key === "Enter") { event.preventDefault(); executeCommand(); }
            else if (key === "ArrowDown" || key === "ArrowUp") { event.preventDefault(); const total = commandMatches().length; state.suggestion = Math.max(0, Math.min(total - 1, state.suggestion + (key === "ArrowDown" ? 1 : -1))); dirty = true; }
            else if (key === "Tab") { event.preventDefault(); const item = commandMatches()[state.suggestion]; if (item) input.value = `/${item.name}`; dirty = true; }
            return;
        }
        if (state.help) { if (key === "Escape" || key === "?" || key === "Enter") { event.preventDefault(); state.help = false; dirty = true; } return; }
        if (key === "Tab") return;
        const lower = key.toLowerCase();
        if (/^[1-6]$/.test(key)) { event.preventDefault(); screen(pages[Number(key)]); }
        else if (key === "Escape" || key === "0") { event.preventDefault(); screen("home"); }
        else if (key === "/" || key === ":") { event.preventDefault(); openCommand(); }
        else if (key === "?") { event.preventDefault(); state.help = true; dirty = true; }
        else if (lower === "t") changeTheme();
        else if (lower === "l") { state.lamp = !state.lamp; announce(state.lamp ? "手电已开启。" : "手电已关闭。"); }
        else if (lower === "m") toggleAudio();
        else if (lower === "f") fullscreen();
        else if (lower === "g") openLink("https://github.com/satoshinji2992/autokeeper");
        else if (lower === "c" && state.page === "install") copyInstall();
        else if (lower === "p" && state.page === "install") { state.model = (state.model + 1) % models.length; announce(`${models[state.model].label}：${models[state.model].note}`); }
        else if (["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "Home", "End", "Enter", " "].includes(key)) {
            event.preventDefault();
            if (state.page === "home") {
                if (key === "Enter" || key === " ") screen(pages[state.menu]);
                else if (key.startsWith("Arrow")) { state.menu = (state.menu - 1 + (["ArrowDown", "ArrowRight"].includes(key) ? 1 : 5)) % 6 + 1; tone(); }
            } else if (state.page === "gallery") {
                if (key === "Enter" || key === " ") openImage();
                else if (["ArrowRight", "ArrowDown", "PageDown"].includes(key)) selectImage(state.image + 1);
                else if (["ArrowLeft", "ArrowUp", "PageUp"].includes(key)) selectImage(state.image - 1);
            } else if (key === "ArrowLeft" || key === "ArrowRight") {
                const at = pages.indexOf(state.page); screen(pages[1 + (at - 1 + (key === "ArrowRight" ? 1 : 5)) % 6]);
            } else if (key === "Home") state.targetScroll = 0;
            else if (key === "End") state.targetScroll = state.maxScroll;
            else scroll(key === "ArrowUp" ? -1 : key === "ArrowDown" ? 1 : key === "PageUp" ? -Math.max(5, rows - 14) : Math.max(5, rows - 14));
            dirty = true;
        }
    });
    window.addEventListener("resize", resize); window.visualViewport?.addEventListener("resize", resize);
    reduced.addEventListener("change", () => { dirty = true; });
    window.addEventListener("hashchange", () => screen(location.hash.slice(1) || "home", false));
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) { cancelAnimationFrame(raf); raf = 0; if (audio) audio.suspend(); }
        else { dirty = true; if (!raf) raf = requestAnimationFrame(tick); if (audio && !state.muted) audio.resume().catch(() => {}); }
    });
    document.body.classList.add("canvas-mode"); resize(); screen(location.hash.slice(1) || "home", false); raf = requestAnimationFrame(tick);
    document.fonts.load('12px "AK Pixel"').then(() => {
        canvas.dataset.typeface = document.fonts.check('12px "AK Pixel"') ? "Fusion Pixel" : "fallback";
        resize();
    }).catch(() => { canvas.dataset.typeface = "fallback"; });
    Promise.all([
        fetch("site/cover-data.json").then(response => { if (!response.ok) throw new Error("Art unavailable"); return response.json(); }).then(value => { art = value; artCache = null; dirty = true; }),
        fetch("site/gallery.json").then(response => { if (!response.ok) throw new Error("Gallery unavailable"); return response.json(); }).then(value => { frames = value; if (state.page === "gallery") loadImage(state.image); dirty = true; })
    ]).catch(() => announce("部分画面暂未载入，文字介绍仍可阅读。"));
})();

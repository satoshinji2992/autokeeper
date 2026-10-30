// The page's few moving parts: the title painting and the name drawn from the game's own character
// cells (site/cover-data.json, exported by dev/export_site_art.py), pictures opened over the page,
// and copy buttons on the commands. Everything else is plain HTML that reads without them.
"use strict";

// Block characters as rectangles of a cell (x, y, w, h, in fractions of it), drawn in the cell's
// foreground over its background: a font would leave gaps between the blocks.
const SHAPES = {
    "▀": [[0, 0, 1, .5]], "▐": [[.5, 0, .5, 1]],
    "▖": [[0, .5, .5, .5]], "▗": [[.5, .5, .5, .5]], "▘": [[0, 0, .5, .5]], "▝": [[.5, 0, .5, .5]],
    "▙": [[0, 0, .5, 1], [.5, .5, .5, .5]], "▚": [[0, 0, .5, .5], [.5, .5, .5, .5]], "▛": [[0, 0, 1, .5], [0, .5, .5, .5]],
    "▜": [[0, 0, 1, .5], [.5, .5, .5, .5]], "▞": [[.5, 0, .5, .5], [0, .5, .5, .5]], "▟": [[.5, 0, .5, 1], [0, .5, .5, .5]],
};
[..."▁▂▃▄▅▆▇█"].forEach((c, i) => { SHAPES[c] = [[0, 1 - (i + 1) / 8, 1, (i + 1) / 8]]; });
[..."▏▎▍▌▋▊▉"].forEach((c, i) => { SHAPES[c] = [[0, 0, (i + 1) / 8, 1]]; });
const SHADES = { "░": .25, "▒": .5, "▓": .75 };

function cell(ctx, x, y, w, h, char, fg, bg) {
    if (bg) { ctx.fillStyle = bg; ctx.fillRect(x, y, w, h); }
    const shape = SHAPES[char], shade = SHADES[char];
    if (!shape && shade === undefined) return;
    ctx.fillStyle = fg;
    ctx.globalAlpha = shade ?? 1;
    for (const [sx, sy, sw, sh] of shape || [[0, 0, 1, 1]]) ctx.fillRect(x + sx * w, y + sy * h, sw * w, sh * h);
    ctx.globalAlpha = 1;
}

function drawCover(canvas, data) {
    const scene = innerWidth / innerHeight > 1.05 ? data.scenes.wide : data.scenes.tall;
    const w = 4, h = 8;  // a terminal cell is twice as tall as it is wide
    canvas.width = scene.cols * w;
    canvas.height = scene.rows * h;
    const ctx = canvas.getContext("2d");
    scene.cells.forEach((row, r) => row.forEach(([char, fg, bg], c) =>
        cell(ctx, c * w, r * h, w, h, char, scene.palette[fg], scene.palette[bg])));
}

function drawLogo(canvas, lines) {
    const w = 6, h = 12, cols = Math.max(...lines.map(line => [...line].length));
    canvas.width = cols * w;
    canvas.height = lines.length * h;
    const ctx = canvas.getContext("2d");
    lines.forEach((line, r) => {
        const fg = `hsl(8 ${78 - r * 3}% ${56 - r * 4}%)`;  // the game's title red, darker towards its foot
        [...line].forEach((char, c) => cell(ctx, c * w, r * h, w, h, char, fg, null));
    });
}

async function title() {
    const cover = document.getElementById("cover"), logo = document.getElementById("logo");
    if (!cover || !logo) return;
    try {
        const response = await fetch("site/cover-data.json");
        if (!response.ok) return;
        const data = await response.json();
        drawLogo(logo, data.logos.wide);
        document.documentElement.classList.add("drawn");
        drawCover(cover, data);
        let wide = innerWidth / innerHeight > 1.05;
        addEventListener("resize", () => {
            const now = innerWidth / innerHeight > 1.05;
            if (now !== wide) { wide = now; drawCover(cover, data); }
        });
    } catch {
        // the plain title stays: the painting is decoration
    }
}

function viewer() {
    const dialog = document.getElementById("viewer");
    if (!dialog) return;
    const image = dialog.querySelector("img"), caption = dialog.querySelector("[data-caption]"), link = dialog.querySelector("a");
    document.querySelectorAll("main figure img").forEach(img => {
        img.addEventListener("click", () => {
            image.src = img.currentSrc || img.src;
            image.alt = img.alt;
            caption.textContent = img.closest("figure").querySelector("figcaption")?.textContent || img.alt;
            link.href = img.src;
            dialog.showModal();
        });
    });
    dialog.addEventListener("click", event => { if (event.target === dialog || event.target.matches("button")) dialog.close(); });
}

function copying() {
    document.querySelectorAll("pre[data-copy]").forEach(pre => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "copy";
        button.textContent = "复制";
        button.addEventListener("click", async () => {
            const text = pre.querySelector("code").textContent.trim();
            try {
                await navigator.clipboard.writeText(text);
                button.textContent = "已复制";
            } catch {
                button.textContent = "请手动复制";
            }
            setTimeout(() => { button.textContent = "复制"; }, 1600);
        });
        pre.append(button);
    });
}

title();
viewer();
copying();

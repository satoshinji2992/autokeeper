"use strict";

(() => {
    const frames = [
    {
        "src": "img/game.png",
        "label": "调查桌面",
        "caption": "在阿诺兹堡图书馆查阅旧报，完成一次图书馆使用检定。",
        "width": 1800,
        "height": 1171
    },
    {
        "src": "img/map.png",
        "label": "地图",
        "caption": "只显示调查员已知的地点与路，记录各处见过的人、找到的线索。",
        "width": 1800,
        "height": 1171
    },
    {
        "src": "img/showcase.png",
        "label": "大场景",
        "caption": "追书人开场：早班火车停靠阿诺兹堡车站。",
        "width": 1800,
        "height": 1246
    },
    {
        "src": "img/sheet.png",
        "label": "人物卡",
        "caption": "调查员的背景、属性与完整技能列表。",
        "width": 1800,
        "height": 1171
    },
    {
        "src": "img/cards.png",
        "label": "角色库",
        "caption": "自己的角色、存档里的调查员和模组预设，都可以带进新的一局。",
        "width": 1800,
        "height": 1246
    },
    {
        "src": "img/plugins.png",
        "label": "规则插件",
        "caption": "查看本局生效的特殊规则与可用行动，或用一句话添加家规。",
        "width": 1800,
        "height": 1171
    },
    {
        "src": "img/saves.png",
        "label": "存档",
        "caption": "预览调查进度，继续、改名、复制或删除存档。",
        "width": 1800,
        "height": 1187
    },
    {
        "src": "img/lobby.png",
        "label": "开房间",
        "caption": "冬雪之下：分配真人与 AI 调查员的座位，开启浏览器加入。",
        "width": 1800,
        "height": 1187
    },
    {
        "src": "img/module.png",
        "label": "选模组",
        "caption": "仓库自带三本模组，第一次玩推荐追书人。",
        "width": 1800,
        "height": 1187
    },
    {
        "src": "img/scenes.png",
        "label": "场景",
        "caption": "追书人的车站、金博尔家、书房、大街、公墓与陵墓。",
        "width": 1781,
        "height": 751
    },
    {
        "src": "img/portraits.png",
        "label": "肖像",
        "caption": "由历史照片与画作转成彩色字符画的 NPC 肖像。",
        "width": 975,
        "height": 715
    },
    {
        "src": "img/title.png",
        "label": "标题画面",
        "caption": "克苏鲁与拉莱耶，AutoKeeper 的标题画面。",
        "width": 1800,
        "height": 1246
    }
];
    const gallery = document.querySelector(".gallery-tabs");
    const panel = document.querySelector("#gallery-panel");
    const image = document.querySelector("#gallery-image");
    const link = document.querySelector("#gallery-link");
    const caption = document.querySelector("#gallery-caption");
    const count = document.querySelector("#gallery-count");

    function chooseFrame(index) {
        const frame = frames[index];
        gallery.querySelectorAll("button").forEach((button, i) => {
            button.setAttribute("aria-selected", String(i === index));
            button.tabIndex = i === index ? 0 : -1;
        });
        image.src = frame.src;
        image.alt = `${frame.label}：${frame.caption}`;
        image.width = frame.width;
        image.height = frame.height;
        link.href = frame.src;
        link.dataset.caption = frame.caption;
        caption.textContent = frame.caption;
        count.textContent = `${String(index + 1).padStart(2, "0")} / ${frames.length}`;
        panel.setAttribute("aria-labelledby", `frame-${index}`);
    }

    frames.forEach((frame, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.id = `frame-${index}`;
        button.textContent = frame.label;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-controls", "gallery-panel");
        button.addEventListener("click", () => chooseFrame(index));
        gallery.append(button);
    });
    chooseFrame(0);
    gallery.hidden = false;

    function keyboardTabs(container) {
        container.addEventListener("keydown", event => {
            const buttons = [...container.querySelectorAll('[role="tab"]')];
            const index = buttons.indexOf(document.activeElement);
            if (index < 0) return;
            let next;
            if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
            if (event.key === "ArrowLeft") next = (index - 1 + buttons.length) % buttons.length;
            if (event.key === "Home") next = 0;
            if (event.key === "End") next = buttons.length - 1;
            if (next === undefined) return;
            event.preventDefault();
            buttons[next].focus();
            buttons[next].click();
        });
    }
    keyboardTabs(gallery);

    const viewer = document.querySelector("#image-viewer");
    let focusBeforeViewer;
    document.querySelectorAll("[data-enlarge]").forEach(anchor => {
        anchor.addEventListener("click", event => {
            if (!viewer.showModal || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            focusBeforeViewer = document.activeElement;
            const fullImage = document.querySelector("#viewer-image");
            fullImage.src = anchor.href;
            fullImage.alt = anchor.querySelector("img")?.alt || anchor.dataset.caption || "实机画面";
            document.querySelector("#viewer-caption").textContent = anchor.dataset.caption || "实机画面";
            document.querySelector("#viewer-original").href = anchor.href;
            viewer.showModal();
            document.body.classList.add("viewer-open");
        });
    });
    document.querySelector(".viewer-close").addEventListener("click", () => viewer.close());
    viewer.addEventListener("close", () => {
        document.body.classList.remove("viewer-open");
        focusBeforeViewer?.focus({preventScroll: true});
    });
    viewer.addEventListener("click", event => {
        const rect = viewer.getBoundingClientRect();
        if (event.target === viewer && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) viewer.close();
    });

    const models = {
        claude: {command: "uv run autokeeper", note: "使用本机已登录的 Claude Code，也可以设置 ANTHROPIC_API_KEY。"},
        deepseek: {command: "uv run autokeeper --preset deepseek", note: "先在本机设置 DEEPSEEK_API_KEY。具体写法见游玩指南的模型与花费一节。"},
        codex: {command: "uv run autokeeper --model codex:default", note: "使用本机安装并已登录的 Codex CLI。也可以只在导入、生成等环节使用 Codex。"},
        ollama: {command: "uv run autokeeper --model ollama:qwen3:14b --search none", note: "先安装 Ollama 并下载相应模型。离线游玩时还需关闭 AI 画图，并确认使用的模型都在本机。"}
    };
    const modelTabs = document.querySelector(".model-tabs");
    modelTabs.querySelectorAll("button").forEach(button => {
        button.addEventListener("click", () => {
            const chosen = models[button.dataset.model];
            modelTabs.querySelectorAll("button").forEach(other => {
                other.setAttribute("aria-selected", String(other === button));
                other.tabIndex = other === button ? 0 : -1;
            });
            document.querySelector("#launch-command").textContent = chosen.command;
            document.querySelector("#model-note").textContent = chosen.note;
            document.querySelector("#launch-panel").setAttribute("aria-labelledby", button.id);
        });
    });
    modelTabs.hidden = false;
    keyboardTabs(modelTabs);

    const status = document.querySelector("#copy-status");
    let toastTimer;
    function notify(text) {
        clearTimeout(toastTimer);
        status.textContent = text;
        status.classList.add("visible");
        toastTimer = setTimeout(() => status.classList.remove("visible"), 2400);
    }
    document.querySelectorAll("[data-copy]").forEach(button => {
        button.hidden = false;
        button.addEventListener("click", async () => {
            const code = document.getElementById(button.dataset.copy);
            try {
                if (!navigator.clipboard) throw new Error("Clipboard unavailable");
                await navigator.clipboard.writeText(code.textContent);
                notify("已复制");
            } catch {
                const range = document.createRange();
                range.selectNodeContents(code);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                notify("已选中命令，请手动复制");
            }
        });
    });

    document.addEventListener("keydown", event => {
        if (!["1", "2", "3"].includes(event.key)) return;
        if (event.ctrlKey || event.metaKey || event.altKey || viewer.open) return;
        const focused = document.activeElement;
        if (focused?.isContentEditable || focused?.closest("input, textarea, select")) return;
        const shortcut = document.querySelector(`.keybar [data-shortcut="${event.key}"]`);
        if (!shortcut) return;
        event.preventDefault();
        shortcut.click();
    });

    if ("IntersectionObserver" in window) {
        const observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                document.querySelectorAll(".masthead nav a").forEach(anchor => {
                    if (anchor.hash === `#${entry.target.id}`) anchor.setAttribute("aria-current", "location");
                    else anchor.removeAttribute("aria-current");
                });
            });
        }, {rootMargin: "-15% 0px -60% 0px"});
        document.querySelectorAll("main > section[id]").forEach(section => observer.observe(section));
    }
})();

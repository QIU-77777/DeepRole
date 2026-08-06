marked.setOptions({ breaks: true, gfm: true });

document.addEventListener("alpine:init", () => {
  Alpine.data("agentGalApp", () => ({
    busy: false,
    consolidating: false,
    consolidatingPollTimer: null,
    consolidatingPollGen: 0,
    noticeText: "",
    noticeTone: "",
    noticeTimer: null,
    storyModalOpen: false,
    confirmDialog: {
      open: false,
      title: "",
      body: "",
      confirmText: "确定",
    },
    confirmAction: null,
    ...window.agentGalMemoryGraph.createState(),
    ...window.agentGalWorldline.createState(),
    ...window.agentGalSearch.createState(),
    ...window.agentGalChat.createState(),
    hasSave: false,
    isCompact: false,
    drawerOpen: false,
    mobileMenuOpen: false,
    mmSection: null,
    inputText: "",
    stories: [],
    characters: [],
    charactersLoading: false,
    _fetchCharactersSeq: 0,
    initialRecent: [],
    initialChoices: [],
    toastTimers: new Map(),
    toasts: [],
    mediaQuery: null,
    visualViewport: null,
    viewportBaseline: 0,
    keyboardVisible: false,
    inputFocused: false,
    handleViewportChangeBound: null,
    handleVisualViewportChangeBound: null,

    async init() {
      if ("scrollRestoration" in window.history) {
        window.history.scrollRestoration = "manual";
      }
      this.setupViewportListener();
      this.setupVisualViewportListener();

      try {
        const storiesResponse = await this.fetchJson("/api/stories");
        this.stories = storiesResponse.stories || [];
      } catch (error) {
        this.setNotice("故事列表加载失败，稍后仍可重新尝试。", "error", true);
      }

      try {
        const initState = await this.fetchJson("/api/init");
        this.hasSave = Boolean(initState.has_save);
        this.characterCount = initState.character_count || 0;
        this.initialRecent = initState.recent || [];
        this.initialChoices = initState.last_choices || [];
        if (this.hasSave) {
          this.continueGame(this.initialRecent, this.initialChoices, { silent: true });
        } else {
          this.storyModalOpen = true;
        }
      } catch (error) {
        this.setNotice("初始化失败，部分功能可能暂时不可用。", "error", true);
        this.storyModalOpen = true;
      }

      await this.refreshSaves({ quiet: true });
      await this.fetchCharacters();
    },

    destroy() {
      if (this.mediaQuery && this.handleViewportChangeBound) {
        this.mediaQuery.removeEventListener("change", this.handleViewportChangeBound);
      }
      if (this.visualViewport && this.handleVisualViewportChangeBound) {
        this.visualViewport.removeEventListener("resize", this.handleVisualViewportChangeBound);
      }
      this.destroyMemoryGraphNetwork();
      this.cancelActiveStream({ silent: true });
      this.stopConsolidationPolling();
      if (this.noticeTimer) {
        window.clearTimeout(this.noticeTimer);
      }
      this.toastTimers.forEach(timeoutId => window.clearTimeout(timeoutId));
      this.toastTimers.clear();
    },

    setupViewportListener() {
      this.mediaQuery = window.matchMedia("(max-width: 920px)");
      this.handleViewportChangeBound = (event) => {
        this.isCompact = event.matches;
        this.drawerOpen = !event.matches;
        if (!event.matches) {
          this.inputFocused = false;
          this.mobileMenuOpen = false;
          this.mmSection = null;
        }
        this.updateKeyboardState();
        this.$nextTick(() => this.resizeComposer());
      };
      this.handleViewportChangeBound(this.mediaQuery);
      this.mediaQuery.addEventListener("change", this.handleViewportChangeBound);
    },

    setupVisualViewportListener() {
      if (!window.visualViewport) return;
      this.visualViewport = window.visualViewport;
      this.handleVisualViewportChangeBound = () => this.updateKeyboardState();
      this.updateKeyboardState();
      this.visualViewport.addEventListener("resize", this.handleVisualViewportChangeBound);
    },

    updateKeyboardState() {
      const viewportHeight = this.visualViewport ? this.visualViewport.height : window.innerHeight;
      if (!viewportHeight) return;
      if (!this.isCompact) {
        this.viewportBaseline = viewportHeight;
        this.keyboardVisible = false;
        return;
      }
      if (!this.viewportBaseline || viewportHeight > this.viewportBaseline) {
        this.viewportBaseline = viewportHeight;
      }
      this.keyboardVisible = this.inputFocused && (this.viewportBaseline - viewportHeight > 120);
    },

    handleEscape() {
      if (this.confirmDialog.open) {
        this.closeConfirm();
        return;
      }
      if (this.memoryGraphOpen) {
        this.closeMemoryGraph();
        return;
      }
      if (this.worldlineOpen) {
        this.closeWorldline();
        return;
      }
      if (this.historySearchOpen) {
        this.closeHistorySearch();
        return;
      }
      if (this.storyModalOpen && this.canDismissStoryModal()) {
        this.closeStoryModal();
        return;
      }
      if (this.drawerOpen && this.isCompact) {
        this.closeDrawer();
      }
    },

    noticeBadge() {
      if (this.noticeTone === "error") return "!";
      if (this.noticeTone === "success") return "OK";
      return "i";
    },

    isInputMode() {
      return this.isCompact && (this.inputFocused || this.keyboardVisible);
    },

    shouldShowNotice() {
      if (!this.noticeText) return false;
      return !this.isInputMode() || this.noticeTone === "error";
    },

    shouldShowChoices() {
      return this.choiceStatus !== "hidden" && !this.isInputMode();
    },

    choiceRailSubtitle() {
      if (this.choiceStatus === "loading") return "选项生成中，也可以直接输入你想说的话。";
      if (this.choiceStatus === "empty") return "这一轮没有预设选项。";
      return "选项只是建议，也可以直接输入你想说的话。";
    },

    trimmedInput() {
      return String(this.inputText || "").trim();
    },

    canDismissStoryModal() {
      return this.messages.length > 0;
    },

    storyCardClass(index) {
      return index % 2 === 0 ? "choice-card warm" : "choice-card cool";
    },

    storyCardCue(index) {
      if (index === 0) return "主线开场";
      return "另一种氛围";
    },

    toggleDrawer() {
      if (this.isCompact) {
        this.blurComposer();
      }
      this.drawerOpen = !this.drawerOpen;
    },

    closeDrawer() {
      this.drawerOpen = false;
    },

    toggleMobileMenu() {
      this.mobileMenuOpen = !this.mobileMenuOpen;
      if (!this.mobileMenuOpen) {
        this.mmSection = null;
        this.clearHistorySearch();
      }
    },

    closeMobileMenu() {
      this.mobileMenuOpen = false;
      this.mmSection = null;
    },

    resetFromDrawer() {
      this.closeDrawer();
      this.showResetConfirmation();
    },

    async fetchJson(url, options = {}) {
      const response = await fetch(url, options);
      if (!response.ok) {
        let detail = "";
        try {
          const text = await response.text();
          detail = text;
          if (text) {
            try {
              const parsed = JSON.parse(text);
              if (parsed && typeof parsed === "object") {
                detail = parsed.detail || parsed.error || text;
              }
            } catch (error) {
              detail = text;
            }
          }
        } catch (error) {
          detail = "";
        }
        throw new Error(`${url} failed: ${response.status}${detail ? ` ${detail.slice(0, 120)}` : ""}`);
      }
      return response.json();
    },

    deleteJson(url) {
      return this.fetchJson(url, { method: "DELETE" });
    },

    postJson(url, body) {
      return this.fetchJson(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },

    clearNotice() {
      this.noticeText = "";
      this.noticeTone = "";
      if (this.noticeTimer) {
        window.clearTimeout(this.noticeTimer);
        this.noticeTimer = null;
      }
    },

    setNotice(text, tone = "success", sticky = false) {
      this.noticeText = text;
      this.noticeTone = tone;
      if (this.noticeTimer) {
        window.clearTimeout(this.noticeTimer);
        this.noticeTimer = null;
      }
      if (!sticky) {
        this.noticeTimer = window.setTimeout(() => {
          this.noticeText = "";
          this.noticeTone = "";
          this.noticeTimer = null;
        }, 3600);
      }
    },

    pushToast(message, tone = "success") {
      const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      this.toasts.push({ id, message, tone });
      const timeoutId = window.setTimeout(() => {
        this.dismissToast(id);
      }, 3200);
      this.toastTimers.set(id, timeoutId);
    },

    dismissToast(id) {
      this.toasts = this.toasts.filter(toast => toast.id !== id);
      const timeoutId = this.toastTimers.get(id);
      if (timeoutId) {
        window.clearTimeout(timeoutId);
        this.toastTimers.delete(id);
      }
    },

    openConfirm({ title, body, confirmText = "确定", onConfirm }) {
      this.confirmDialog = {
        open: true,
        title,
        body,
        confirmText,
      };
      this.confirmAction = onConfirm;
    },

    closeConfirm() {
      this.confirmDialog = {
        open: false,
        title: "",
        body: "",
        confirmText: "确定",
      };
      this.confirmAction = null;
    },

    async runConfirm() {
      const action = this.confirmAction;
      this.closeConfirm();
      if (action) {
        await action();
      }
    },

    closeStoryModal() {
      if (!this.canDismissStoryModal()) return;
      this.storyModalOpen = false;
    },

    showResetConfirmation() {
      if (this.busy) {
        this.setNotice("请等待当前回合完成后再重开。", "error");
        return;
      }
      if (this.consolidating) {
        this.setNotice("记忆整理进行中，请稍后再重开。", "error");
        return;
      }
      this.openConfirm({
        title: "重新选择故事？",
        body: "当前运行时数据会被新开局覆盖。确认后会回到故事选择界面。",
        confirmText: "继续",
        onConfirm: async () => {
          this.storyModalOpen = true;
        },
      });
    },

    recentPreview() {
      const recent = [...this.initialRecent].reverse().find(message => String(message.content || "").trim());
      if (!recent) return "发现一个可继续的进度，读取后会恢复最近对话和建议行动。";
      return this.excerptPlain(recent.content, 88);
    },

    excerptPlain(content, maxLength = 72) {
      const plain = this.stripMarkdown(content || "").replace(/\s+/g, " ").trim();
      if (plain.length <= maxLength) return plain;
      return `${plain.slice(0, maxLength - 1)}…`;
    },

    stripMarkdown(value) {
      return String(value || "")
        .replace(/!\[[^\]]*]\([^)]*\)/g, "")
        .replace(/\[([^\]]+)]\([^)]*\)/g, "$1")
        .replace(/[`*_>#~-]/g, "")
        .replace(/\s+/g, " ");
    },

    handleComposerInput(event) {
      this.resizeComposer(event.target);
    },

    handleComposerFocus() {
      this.inputFocused = true;
      this.updateKeyboardState();
    },

    handleComposerBlur() {
      this.inputFocused = false;
      this.keyboardVisible = false;
    },

    blurComposer() {
      const textarea = this.$refs?.composerInput;
      if (textarea) {
        textarea.blur();
      }
    },

    resizeComposer(target = null) {
      const textarea = target || this.$refs?.composerInput;
      if (!textarea) return;
      const baseHeight = this.isCompact ? 52 : 108;
      const maxHeight = this.isCompact ? 136 : 220;
      textarea.style.height = `${baseHeight}px`;
      if (!textarea.value) return;
      textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, baseHeight), maxHeight)}px`;
    },

    resetComposer() {
      this.inputText = "";
      this.$nextTick(() => this.resizeComposer());
    },

    setConsolidating(running) {
      this.consolidating = running;
      if (running) {
        this.startConsolidationPolling();
      } else {
        this.stopConsolidationPolling();
      }
    },

    startConsolidationPolling() {
      if (this.consolidatingPollTimer) return;
      // 用 generation 序号防止 stop 后正在飞的 tick 在 await 完成后又把自己排进下一轮
      const gen = (this.consolidatingPollGen || 0) + 1;
      this.consolidatingPollGen = gen;
      const tick = async () => {
        if (this.consolidatingPollGen !== gen) return;
        try {
          const response = await fetch("/api/status");
          if (this.consolidatingPollGen !== gen) return;
          if (response.ok) {
            const data = await response.json();
            if (!data.consolidating) {
              this.stopConsolidationPolling();
              this.consolidating = false;
              if (data.new_episodes && data.new_episodes.length) {
                for (const ep of data.new_episodes) {
                  const title = ep.title ? `「${ep.title}」` : "";
                  this.pushToast(`${ep.display_name}记忆新增事件${title}`, "success");
                }
              }
              return;
            }
          }
        } catch (error) {
          // 网络抖动忽略，下次再试
          if (this.consolidatingPollGen !== gen) return;
        }
        this.consolidatingPollTimer = setTimeout(tick, 1500);
      };
      this.consolidatingPollTimer = setTimeout(tick, 1500);
    },

    stopConsolidationPolling() {
      this.consolidatingPollGen = (this.consolidatingPollGen || 0) + 1;
      if (this.consolidatingPollTimer) {
        clearTimeout(this.consolidatingPollTimer);
        this.consolidatingPollTimer = null;
      }
    },

    async fetchCharacters() {
      const seq = ++this._fetchCharactersSeq;
      // 已有数据时静默刷新，不触发 loading 闪烁；首次加载才显示骨架屏
      const isFirstLoad = this.characters.length === 0;
      if (isFirstLoad) {
        this.charactersLoading = true;
      }
      try {
        const response = await this.fetchJson("/api/characters");
        // 只采纳最新一次请求的结果，丢弃被后续请求超越的旧响应
        if (seq === this._fetchCharactersSeq) {
          this.characters = response.characters || [];
        }
      } catch (_error) {
        // 后台刷新失败时保留旧数据，不清空已展示内容
      } finally {
        this.charactersLoading = false;
      }
    },
  }));
});

(function () {
  // 历史检索与游戏内日期跳转：搜索框/日期菜单的开合、定位、查询与片段高亮。
  // 从 app.js 主组件抽出，沿用 createState() 模式；state 仍平铺进同一个 Alpine 组件 this。
  function createState() {
    return {
      gameDates: [],
      gameDatesLoaded: false,
      gameDatesLoading: false,
      dateMenuOpen: false,
      searchQuery: "",
      searchResults: [],
      searchLoading: false,
      searchTimer: null,
      historySearchOpen: false,

      async ensureGameDates() {
        if (this.gameDatesLoaded || this.gameDatesLoading) return;
        this.gameDatesLoading = true;
        try {
          const response = await this.fetchJson("/api/history/dates");
          this.gameDates = response.anchors || [];
          this.gameDatesLoaded = true;
        } catch (error) {
          this.setNotice("加载历史日期失败。", "error");
        } finally {
          this.gameDatesLoading = false;
        }
      },

      async toggleDateMenu() {
        this.dateMenuOpen = !this.dateMenuOpen;
        if (this.dateMenuOpen) {
          this.closeHistorySearch();
          await this.ensureGameDates();
          await this.$nextTick();
          this.positionDateMenu();
        }
      },

      positionDateMenu() {
        const btn = this.$refs.dateBtn;
        const menu = this.$refs.dateMenu;
        if (!btn || !menu) return;
        const rect = btn.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 8}px`;
        menu.style.right = `${Math.max(8, window.innerWidth - rect.right)}px`;
      },

      async jumpToTurn(targetTurn) {
        const target = Number(targetTurn);
        if (!target || target < 1) return;
        this.dateMenuOpen = false;
        this.historySearchOpen = false;
        this.closeMobileMenu();
        const container = this.$refs.messages;
        let exists = this.messages.some(m => m.turn === target);
        let safety = 20;
        while (!exists && this.oldestLoadedTurn != null && this.oldestLoadedTurn > target && safety-- > 0) {
          const requestLimit = Math.min(200, Math.max(30, this.oldestLoadedTurn - target));
          try {
            const response = await this.fetchJson(`/api/history?before_turn=${this.oldestLoadedTurn}&limit=${requestLimit}`);
            const fetched = response.messages || [];
            if (!fetched.length) {
              this.historyExhausted = true;
              break;
            }
            this.prependHistory(fetched);
            if (fetched.length < requestLimit) this.historyExhausted = true;
          } catch (error) {
            this.setNotice("跳转加载失败。", "error");
            return;
          }
          exists = this.messages.some(m => m.turn === target);
        }
        if (!exists) return;
        await this.$nextTick();
        if (container) {
          const node = container.querySelector(`[data-turn="${target}"]`);
          if (node) {
            node.scrollIntoView({ behavior: "smooth", block: "start" });
            setTimeout(() => this.updateLatestState(), 260);
          }
        }
      },

      toggleHistorySearch() {
        if (this.historySearchOpen) {
          this.closeHistorySearch();
        } else {
          this.openHistorySearch();
        }
      },

      positionHistorySearch() {
        const btn = this.$refs.searchBtn;
        const popover = this.$refs.searchPopover;
        if (!btn || !popover) return;
        const rect = btn.getBoundingClientRect();
        const width = Math.min(430, window.innerWidth - 24);
        const left = Math.min(
          Math.max(12, rect.right - width),
          window.innerWidth - width - 12
        );
        popover.style.top = `${rect.bottom + 8}px`;
        popover.style.left = `${left}px`;
        popover.style.right = "auto";
        popover.style.width = `${width}px`;
      },

      openHistorySearch() {
        this.historySearchOpen = true;
        this.dateMenuOpen = false;
        this.$nextTick(() => {
          this.positionHistorySearch();
          this.$refs.historySearchInput?.focus();
          if (this.searchQuery.trim() && !this.searchResults.length && !this.searchLoading) {
            this.onSearchInput();
          }
        });
      },

      closeHistorySearch() {
        this.historySearchOpen = false;
      },

      clearHistorySearch({ keepOpen = false } = {}) {
        this.searchQuery = "";
        this.searchResults = [];
        this.searchLoading = false;
        this.historySearchOpen = keepOpen;
        if (this.searchTimer) {
          clearTimeout(this.searchTimer);
          this.searchTimer = null;
        }
        if (keepOpen) {
          this.$nextTick(() => this.$refs.historySearchInput?.focus());
        }
      },

      onSearchInput() {
        if (this.searchTimer) clearTimeout(this.searchTimer);
        const q = this.searchQuery.trim();
        if (!q) {
          this.searchResults = [];
          this.searchLoading = false;
          this.historySearchOpen = false;
          return;
        }
        if (!this.historySearchOpen) this.historySearchOpen = true;
        this.searchLoading = true;
        this.searchTimer = setTimeout(() => this.runSearch(q), 250);
      },

      async runSearch(q) {
        try {
          const response = await this.fetchJson(`/api/history/search?q=${encodeURIComponent(q)}&limit=50`);
          if (this.searchQuery.trim() !== q) return;
          this.searchResults = response.messages || [];
        } catch (error) {
          this.setNotice("搜索失败。", "error");
        } finally {
          if (this.searchQuery.trim() === q) {
            this.searchLoading = false;
          }
        }
      },

      buildSearchSnippet(content, query) {
        const text = String(content || "");
        const q = String(query || "");
        if (!q) return { before: text.slice(0, 60), match: "", after: "" };
        const idx = text.toLowerCase().indexOf(q.toLowerCase());
        if (idx === -1) return { before: text.slice(0, 60), match: "", after: "" };
        const start = Math.max(0, idx - 24);
        const end = Math.min(text.length, idx + q.length + 36);
        const beforeBody = text.slice(start, idx).replace(/\s+/g, " ");
        const matchBody = text.slice(idx, idx + q.length);
        const afterBody = text.slice(idx + q.length, end).replace(/\s+/g, " ");
        return {
          before: (start > 0 ? "…" : "") + beforeBody,
          match: matchBody,
          after: afterBody + (end < text.length ? "…" : ""),
        };
      },

      renderSearchSnippet(content, query) {
        const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const { before, match, after } = this.buildSearchSnippet(content, query);
        return `${esc(before)}<mark>${esc(match)}</mark>${esc(after)}`;
      },
    };
  }

  window.agentGalSearch = { createState };
})();

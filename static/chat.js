(function () {
  // 对话主流程：消息渲染/markdown 清洗/场景分组、消息数组与滚动·历史分页、
  // 发送与 SSE 流式生命周期（含 startNewGame/continueGame/resetConversation）。
  // 从 app.js 主组件抽出，沿用 createState() 模式；state 仍平铺进同一个 Alpine 组件 this，
  // 通过 this 调用外壳的 composer/consolidation/fetchCharacters/notice 等。
  function createState() {
    return {
      characterCount: 0,
      messages: [],
      choices: [],
      choiceStatus: "hidden",
      nextMessageId: 1,
      oldestLoadedTurn: null,
      historyExhausted: false,
      loadingOlder: false,
      nearLatest: true,
      unreadNewMessages: false,
      activeStreamController: null,
      activeStreamId: 0,
      agentStyleByAuthor: {},
      agentPalette: [
        { color: "#b45a64", border: "rgba(180, 90, 100, 0.18)", background: "rgba(180, 90, 100, 0.08)" },
        { color: "#6a8165", border: "rgba(106, 129, 101, 0.2)", background: "rgba(106, 129, 101, 0.08)" },
        { color: "#5b7d86", border: "rgba(91, 125, 134, 0.2)", background: "rgba(91, 125, 134, 0.08)" },
        { color: "#95684d", border: "rgba(149, 104, 77, 0.2)", background: "rgba(149, 104, 77, 0.08)" },
      ],
      // 固定角色配色（按显示名），不随出场顺序变化：林溪=红，沈知意=黄
      agentColorOverrides: {
        "林溪": { color: "#b45a64", border: "rgba(180, 90, 100, 0.18)", background: "rgba(180, 90, 100, 0.08)" },
        "沈知意": { color: "#c2a14d", border: "rgba(194, 161, 77, 0.22)", background: "rgba(194, 161, 77, 0.08)" },
      },

      parseNarratorLine(rawLine) {
        const line = String(rawLine || "").replace(/\*\*/g, "").trim();
        const chineseSeparator = line.indexOf("：");
        const separatorIndex = chineseSeparator >= 0 ? chineseSeparator : line.indexOf(":");
        if (separatorIndex <= 0) return { key: "", value: "" };
        return { key: line.slice(0, separatorIndex).trim(), value: line.slice(separatorIndex + 1).trim() };
      },

      normalizeNarratorText(content) {
        const lines = String(content || "").split("\n");
        const normalized = [];
        const hiddenMetaKeys = new Set(["时间", "地点", "场景"]);

        for (const rawLine of lines) {
          const line = rawLine.trimEnd();
          const trimmed = line.trim();

          if (!trimmed) {
            normalized.push("");
            continue;
          }

          if (trimmed === "旁白" || trimmed === "**旁白**") {
            continue;
          }

          const { key: metaKey, value: metaValue } = this.parseNarratorLine(line);
          if (hiddenMetaKeys.has(metaKey)) {
            continue;
          }
          if (metaKey === "在场") {
            if (metaValue) normalized.push(metaValue);
            continue;
          }

          normalized.push(line);
        }

        return normalized.join("\n").replace(/\n{3,}/g, "\n\n").replace(/^\n+/, "");
      },

      isSafeLink(href) {
        const value = String(href || "").trim().toLowerCase();
        return value.startsWith("http://") || value.startsWith("https://") || value.startsWith("mailto:");
      },

      sanitizeHtml(html) {
        const template = document.createElement("template");
        template.innerHTML = html;

        const allowedTags = new Set([
          "a",
          "blockquote",
          "br",
          "code",
          "em",
          "h1",
          "h2",
          "h3",
          "hr",
          "li",
          "ol",
          "p",
          "pre",
          "strong",
          "ul",
        ]);
        const dropTags = new Set(["script", "style", "iframe", "object", "embed", "form", "input", "button", "textarea"]);
        const nodes = Array.from(template.content.querySelectorAll("*")).reverse();

        nodes.forEach(node => {
          const tag = node.tagName.toLowerCase();
          if (dropTags.has(tag)) {
            node.remove();
            return;
          }
          if (!allowedTags.has(tag)) {
            const parent = node.parentNode;
            if (!parent) return;
            while (node.firstChild) {
              parent.insertBefore(node.firstChild, node);
            }
            parent.removeChild(node);
            return;
          }

          Array.from(node.attributes).forEach(attr => {
            const name = attr.name.toLowerCase();
            if (tag === "a" && ["href", "title"].includes(name)) return;
            node.removeAttribute(attr.name);
          });

          if (tag === "a") {
            const href = node.getAttribute("href");
            if (!this.isSafeLink(href)) {
              node.removeAttribute("href");
            } else {
              node.setAttribute("target", "_blank");
              node.setAttribute("rel", "noopener noreferrer");
            }
          }
        });

        return template.innerHTML;
      },

      renderMarkdown(content, { narrator = false } = {}) {
        const source = narrator ? this.normalizeNarratorText(content) : String(content || "");
        return this.sanitizeHtml(marked.parse(source));
      },

      extractNarratorField(content, fields) {
        const wanted = new Set(fields);
        for (const rawLine of String(content || "").split("\n")) {
          const { key, value } = this.parseNarratorLine(rawLine);
          if (!wanted.has(key)) continue;
          if (value) return value;
        }
        return "";
      },

      messageGroups() {
        const groups = [];
        let current = null;

        this.messages.forEach(message => {
          if (message.kind === "narrator") {
            const payload = message.payload || null;
            current = {
              key: `scene-${message.id}`,
              title: (payload && payload.location) || this.extractNarratorField(message.content, ["地点", "场景"]) || "新的场面",
              time: payload ? [payload.date, payload.time].filter(Boolean).join(" ") : this.extractNarratorField(message.content, ["时间"]),
              narrator: message,
              messages: [message],
            };
            groups.push(current);
            return;
          }

          if (!current) {
            current = {
              key: `open-${message.id}`,
              title: "",
              time: "",
              narrator: null,
              messages: [],
            };
            groups.push(current);
          }
          current.messages.push(message);
        });

        return groups;
      },

      normalizeNarratorPayload(payload) {
        if (!payload || typeof payload !== "object") return null;
        const present = payload.present_characters && typeof payload.present_characters === "object"
          ? payload.present_characters
          : {};
        return {
          targets: Array.isArray(payload.targets) ? payload.targets : [],
          date: String(payload.date || "").trim(),
          time: String(payload.time || "").trim(),
          location: String(payload.location || "").trim(),
          present_characters: Object.fromEntries(
            Object.entries(present)
              .map(([name, value]) => [String(name || "").trim(), String(value || "").trim()])
              .filter(([name, value]) => name && value)
          ),
          scene_description: String(payload.scene_description || "").trim(),
          new_characters: Array.isArray(payload.new_characters) ? payload.new_characters : [],
        };
      },

      presenceEntries(payload) {
        const present = payload && payload.present_characters && typeof payload.present_characters === "object"
          ? payload.present_characters
          : {};
        return Object.entries(present);
      },

      getAgentStyle(author) {
        const key = author || "角色";
        if (!this.agentStyleByAuthor[key]) {
          const style =
            this.agentColorOverrides[key] ||
            this.agentPalette[Object.keys(this.agentStyleByAuthor).length % this.agentPalette.length];
          this.agentStyleByAuthor[key] = style;
        }
        return this.agentStyleByAuthor[key];
      },

      _makeMessage(kind, author, content, turn = 0, { payload = null } = {}) {
        const normalizedPayload = kind === "narrator" ? this.normalizeNarratorPayload(payload) : null;
        const text = String(content || "").trim();
        if (!text && !normalizedPayload) return null;
        const resolvedAuthor = kind === "player" ? "你" : (author || "旁白");
        const style = kind === "agent" ? this.getAgentStyle(resolvedAuthor) : {
          color: "#6a8165",
          border: "rgba(106, 129, 101, 0.3)",
          background: "rgba(106, 129, 101, 0.12)",
        };
        return {
          id: this.nextMessageId++,
          kind,
          author: resolvedAuthor,
          color: style.color,
          border: style.border,
          background: style.background,
          content: text,
          payload: normalizedPayload,
          html: normalizedPayload ? "" : this.renderMarkdown(text, { narrator: kind === "narrator" }),
          turn: Number(turn) || 0,
        };
      },

      _buildHistoryMessages(items) {
        const built = [];
        let minTurn = null;
        (items || []).forEach(message => {
          const role = message.role || "";
          let kind = "agent";
          if (role === "player") kind = "player";
          else if (role === "narrator") kind = "narrator";
          const author = kind === "player" ? "你" : (message.author || role);
          const turn = Number(message.turn) || 0;
          const obj = this._makeMessage(kind, author, message.content, turn, { payload: message.payload || null });
          if (!obj) return;
          built.push(obj);
          if (turn > 0 && (minTurn === null || turn < minTurn)) minTurn = turn;
        });
        return { built, minTurn };
      },

      isNearLatest(buffer = 120) {
        const el = this.$refs.messages;
        if (!el) return true;
        return el.scrollHeight - el.scrollTop - el.clientHeight <= buffer;
      },

      updateLatestState() {
        this.nearLatest = this.isNearLatest();
        if (this.nearLatest) {
          this.unreadNewMessages = false;
        }
      },

      shouldAutoFollowLatest() {
        return !this.$refs.messages || this.nearLatest;
      },

      shouldShowJumpLatest() {
        return this.messages.length > 0 && !this.isInputMode() && (!this.nearLatest || this.unreadNewMessages);
      },

      jumpToLatest() {
        this.scrollToLatest({ settle: true });
      },

      _afterPush(shouldFollow) {
        if (shouldFollow) {
          this.scrollToLatest();
        } else {
          this.unreadNewMessages = true;
        }
      },

      addMessage(kind, author, content, turn = 0, { forceScroll = false, payload = null } = {}) {
        const message = this._makeMessage(kind, author, content, turn, { payload });
        if (!message) return;
        const shouldFollow = forceScroll || this.shouldAutoFollowLatest();
        this.messages.push(message);
        this._afterPush(shouldFollow);
      },

      addSystemMessage({ title = "系统", name = "", identity = "", characterId = "" } = {}) {
        const displayName = String(name || characterId || "新角色").trim();
        const identityText = String(identity || "").trim();
        const shouldFollow = this.shouldAutoFollowLatest();

        this.messages.push({
          id: this.nextMessageId++,
          kind: "system",
          title: String(title || "系统").trim(),
          name: displayName,
          identity: identityText,
          characterId: String(characterId || "").trim(),
        });

        this._afterPush(shouldFollow);
      },

      addHistory(messages) {
        const { built, minTurn } = this._buildHistoryMessages(messages);
        if (!built.length) return;
        this.messages.push(...built);
        if (minTurn !== null) this.oldestLoadedTurn = minTurn;
        this.scrollToLatest();
      },

      scrollToLatest({ settle = false } = {}) {
        const stick = () => {
          const el = this.$refs.messages;
          if (el) el.scrollTop = el.scrollHeight;
        };
        this.$nextTick(() => {
          requestAnimationFrame(() => {
            requestAnimationFrame(stick);
          });
        });
        setTimeout(stick, 250);
        if (settle) {
          [80, 500, 1000].forEach(delay => setTimeout(stick, delay));
        }
        this.unreadNewMessages = false;
        this.nearLatest = true;
      },

      prependHistory(messages) {
        const { built, minTurn } = this._buildHistoryMessages(messages);
        if (!built.length) return [];
        this.messages.unshift(...built);
        if (minTurn !== null && (this.oldestLoadedTurn == null || minTurn < this.oldestLoadedTurn)) {
          this.oldestLoadedTurn = minTurn;
        }
        return built;
      },

      async loadOlderHistory() {
        if (this.loadingOlder || this.historyExhausted) return;
        if (this.oldestLoadedTurn == null || this.oldestLoadedTurn <= 1) {
          this.historyExhausted = true;
          return;
        }
        this.loadingOlder = true;
        const container = this.$refs.messages;
        const prevHeight = container ? container.scrollHeight : 0;
        const prevTop = container ? container.scrollTop : 0;
        try {
          const requestLimit = 30;
          const response = await this.fetchJson(`/api/history?before_turn=${this.oldestLoadedTurn}&limit=${requestLimit}`);
          const fetched = response.messages || [];
          if (!fetched.length) {
            this.historyExhausted = true;
            return;
          }
          const inserted = this.prependHistory(fetched);
          if (fetched.length < requestLimit) {
            this.historyExhausted = true;
          }
          if (!inserted.length) return;
          await this.$nextTick();
          if (container) {
            container.scrollTop = container.scrollHeight - prevHeight + prevTop;
            this.updateLatestState();
          }
        } catch (error) {
          this.setNotice("加载更早的对话失败。", "error");
        } finally {
          this.loadingOlder = false;
        }
      },

      handleMessagesScroll() {
        if (!this.$refs.messages) return;
        const near = this.isNearLatest();
        if (near !== this.nearLatest) this.nearLatest = near;
        if (near) this.unreadNewMessages = false;
        if (this.$refs.messages.scrollTop < 80) {
          this.loadOlderHistory();
        }
      },

      resetConversation() {
        this.messages = [];
        this.choices = [];
        this.choiceStatus = "hidden";
        this.nextMessageId = 1;
        this.agentStyleByAuthor = {};
        this.oldestLoadedTurn = null;
        this.historyExhausted = false;
        this.loadingOlder = false;
        this.gameDates = [];
        this.gameDatesLoaded = false;
        this.dateMenuOpen = false;
        this.searchQuery = "";
        this.searchResults = [];
        this.searchLoading = false;
        this.historySearchOpen = false;
        this.nearLatest = true;
        this.unreadNewMessages = false;
        this.characters = [];
        clearTimeout(this.searchTimer);
        this.searchTimer = null;
        this.clearNotice();
        this.resetComposer();
      },

      async startNewGame(storyId) {
        if (this.busy) return;
        if (this.consolidating) {
          this.setNotice("记忆整理进行中，请稍后再开始新故事。", "error");
          return;
        }
        this.cancelActiveStream({ silent: true });
        this.storyModalOpen = false;
        this.resetConversation();
        this.busy = true;

        try {
          const response = await this.postJson("/api/new_game", { story_id: storyId });
          if (response.intro) this.addMessage("narrator", "旁白", response.intro);
          if (response.opening) this.addMessage("narrator", "旁白", response.opening);
          this.choices = response.choices || [];
          this.choiceStatus = this.choices.length ? "ready" : "empty";
          this.characterCount = response.character_count || 0;
          this.hasSave = true;
          this.initialRecent = [];
          this.initialChoices = this.choices;
          await this.refreshSaves({ quiet: true });
          await this.fetchCharacters();
          this.pushToast("新故事已开始", "success");
        } catch (error) {
          this.storyModalOpen = true;
          this.setNotice("新故事启动失败，请稍后再试。", "error", true);
        } finally {
          this.busy = false;
        }
      },

      continueGame(recent, lastChoices, { silent = false } = {}) {
        this.storyModalOpen = false;
        this.resetConversation();
        this.choices = lastChoices || [];
        this.choiceStatus = this.choices.length ? "ready" : "hidden";
        this.addHistory(recent);
        this.scrollToLatest({ settle: true });
        if (this.isCompact) this.closeDrawer();
        if (!silent) {
          this.setNotice("已恢复最近进度。", "success");
        }
      },

      submitCurrentInput() {
        this.submitMessage(this.inputText);
      },

      handleInputKeydown(event) {
        if (this.isCompact) return;
        if (event.isComposing) return;
        if (event.key !== "Enter" || event.shiftKey) return;
        event.preventDefault();
        this.submitCurrentInput();
      },

      cancelActiveStream({ silent = false } = {}) {
        if (!this.activeStreamController) return;
        this.activeStreamController.abort();
        this.activeStreamController = null;
        this.activeStreamId += 1;
        this.busy = false;
        if (!silent) {
          this.pushToast("当前请求已取消。", "success");
        }
      },

      async submitMessage(text) {
        const message = String(text || "").trim();
        if (!message || this.busy) return;
        if (this.activeStreamController) {
          this.activeStreamController.abort();
          this.activeStreamController = null;
        }

        if (this.isCompact) {
          this.blurComposer();
        }
        this.addMessage("player", "你", message, 0, { forceScroll: true });
        this.resetComposer();
        this.choices = [];
        this.choiceStatus = "loading";
        this.busy = true;
        const streamId = this.activeStreamId + 1;
        this.activeStreamId = streamId;

        try {
          await this.streamChat(message, streamId);
        } catch (error) {
          if (this.activeStreamId === streamId && error.name !== "AbortError") {
            this.setNotice("消息发送失败，请检查连接后重试。", "error", true);
            this.pushToast("发送失败", "error");
          }
        } finally {
          if (this.activeStreamId === streamId) {
            if (this.choiceStatus === "loading") {
              this.choiceStatus = "empty";
            }
            this.busy = false;
            this.activeStreamController = null;
          }
        }
      },

      async streamChat(message, streamId) {
        const controller = new AbortController();
        this.activeStreamController = controller;
        await this.ensureToken();
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-User-Token": this.token,
          },
          body: JSON.stringify({ message }),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`/api/chat failed: ${response.status}`);
        }
        if (this.activeStreamId !== streamId) {
          return;
        }
        if (!response.body) {
          throw new Error("SSE body is empty");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (this.activeStreamId !== streamId) {
            await reader.cancel();
            return;
          }
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() || "";
          chunks.forEach(chunk => this.handleStreamPart(chunk, streamId));
        }

        if (buffer.trim()) {
          this.handleStreamPart(buffer, streamId);
        }
      },

      handleStreamPart(part, streamId = this.activeStreamId) {
        if (streamId !== this.activeStreamId) return;
        const payload = part
          .split("\n")
          .filter(line => line.startsWith("data:"))
          .map(line => line.replace(/^data:\s*/, ""))
          .join("");

        if (!payload) return;

        try {
          const event = JSON.parse(payload);
          if (event.type === "narrator") {
            this.addMessage("narrator", event.author || "旁白", event.content, 0, { payload: event.payload || null });
          } else if (event.type === "system") {
            this.addSystemMessage({
              title: event.title || "系统",
              name: event.name || "",
              identity: event.identity || "",
              characterId: event.character_id || "",
            });
            if (event.title === "角色已创建") {
              this.fetchCharacters();
            }
          } else if (event.type === "agent") {
            this.addMessage("agent", event.author, event.content);
          } else if (event.type === "choices") {
            this.choices = event.choices || [];
            this.choiceStatus = this.choices.length ? "ready" : "empty";
          } else if (event.type === "done") {
            this.setConsolidating(Boolean(event.consolidating));
            this.fetchCharacters();
          } else if (event.type === "response_done") {
            this.busy = false;
            this.setConsolidating(Boolean(event.consolidating));
          }
        } catch (error) {
          this.setNotice("实时消息解析失败，界面可能没有完整更新。", "error", true);
        }
      },
    };
  }

  window.agentGalChat = { createState };
})();

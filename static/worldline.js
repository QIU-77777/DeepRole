(function () {
  // 世界线视图：存档分支树布局、缩放/拖拽、读档与存档 CRUD。
  // 从 app.js 主组件抽出，沿用 memory_graph.js 的 createState() 模式；
  // state 仍平铺进同一个 Alpine 组件 this，模块间通过 this 互访。
  function createState() {
    return {
      saves: [],
      worlds: [],
      activeWorldId: "",
      currentSaveId: "",
      currentStoryId: "",
      worldlineOpen: false,
      worldlineZoom: 1,
      worldlineDrag: null,
      isSaving: false,
      saveLoading: false,
      saveError: "",

      async createSave() {
        await this.saveGame();
      },

      async openWorldline() {
        if (this.isCompact) {
          this.closeDrawer();
        }
        this.worldlineOpen = true;
        if (!this.worlds.length && !this.saveLoading) {
          await this.refreshSaves({ quiet: true });
        }
        this.ensureActiveWorld();
      },

      closeWorldline() {
        this.worldlineOpen = false;
      },

      selectWorld(storyId) {
        this.activeWorldId = storyId || "";
        this.worldlineZoom = 1;
        this.$nextTick(() => {
          const target = this.$refs?.worldlineScroll;
          if (!target) return;
          target.scrollLeft = 0;
          target.scrollTop = 0;
        });
      },

      normalizeSave(save) {
        return typeof save === "string"
          ? { filename: save, display: save, focus: "" }
          : {
              ...save,
              filename: save.filename,
              display: save.display_time || save.filename,
              focus: save.focus || "",
            };
      },

      storyLabel(storyId) {
        const story = this.stories.find(item => item.id === storyId);
        return story?.label || story?.title || storyId || "未知世界观";
      },

      storySummary(storyId) {
        const story = this.stories.find(item => item.id === storyId);
        return story?.summary || "这个世界观下的存档会按父子关系显示为分支树。";
      },

      normalizeWorld(world) {
        const storyId = world.story_id || "unknown";
        return {
          ...world,
          story_id: storyId,
          display_name: this.storyLabel(storyId),
          roots: world.roots || [],
          orphans: world.orphans || [],
          save_count: Number(world.save_count) || 0,
          root_count: Number(world.root_count) || 0,
          orphan_count: Number(world.orphan_count) || 0,
        };
      },

      worldTabs() {
        const byId = new Map();
        this.stories.forEach(story => {
          byId.set(story.id, {
            story_id: story.id,
            display_name: story.label || story.title || story.id,
            roots: [],
            orphans: [],
            save_count: 0,
            root_count: 0,
            orphan_count: 0,
          });
        });
        this.worlds.forEach(world => {
          byId.set(world.story_id, {
            ...(byId.get(world.story_id) || {}),
            ...world,
            display_name: this.storyLabel(world.story_id),
          });
        });
        return Array.from(byId.values());
      },

      ensureActiveWorld() {
        const tabs = this.worldTabs();
        if (!tabs.length) {
          this.activeWorldId = "";
          return;
        }
        if (this.activeWorldId && tabs.some(world => world.story_id === this.activeWorldId)) return;
        if (this.currentStoryId && tabs.some(world => world.story_id === this.currentStoryId)) {
          this.activeWorldId = this.currentStoryId;
          return;
        }
        this.activeWorldId = tabs[0].story_id;
      },

      activeWorld() {
        const tabs = this.worldTabs();
        return tabs.find(world => world.story_id === this.activeWorldId) || tabs[0] || null;
      },

      activeWorldLabel() {
        const world = this.activeWorld();
        return world?.display_name || "世界观";
      },

      activeWorldDescription() {
        const world = this.activeWorld();
        return this.storySummary(world?.story_id || "");
      },

      worldlineGameLatestKey(node) {
        if (!node) return "";
        const childLatest = (node.children || []).reduce((latest, child) => {
          const childKey = this.worldlineGameLatestKey(child);
          return childKey > latest ? childKey : latest;
        }, "");
        const ownKey = node.latest_created_at || node.created_at || node.display || node.filename || "";
        return ownKey > childLatest ? ownKey : childLatest;
      },

      worldlineGameRoots() {
        const world = this.activeWorld();
        if (!world) return [];
        const games = [
          ...(world.roots || []).map(node => ({
            node,
            orphan: false,
          })),
          ...(world.orphans || []).map(node => ({
            node,
            orphan: true,
          })),
        ];
        games.sort((a, b) => this.worldlineGameLatestKey(b.node).localeCompare(this.worldlineGameLatestKey(a.node)));
        let gameIndex = 0;
        let orphanIndex = 0;
        return games.map((game, index) => {
          if (game.orphan) {
            orphanIndex += 1;
            return { ...game, index, label: `断裂分支 ${orphanIndex}` };
          }
          gameIndex += 1;
          return { ...game, index, label: `Game ${gameIndex}` };
        });
      },

      layoutWorldlineTree(root, { orphan = false, index = 0, label = "Game" } = {}) {
        const cardWidth = 196;
        const cardHeight = 126;
        const cardOffsetX = 26;
        const cardOffsetY = -38;
        const siblingGap = 276;
        const levelGap = 198;
        const padX = 48;
        const padY = 62;
        let leafIndex = 0;
        let maxDepth = 0;

        const build = (node, depth = 0) => {
          maxDepth = Math.max(maxDepth, depth);
          const childLayouts = (node.children || []).map(child => build(child, depth + 1));
          const x = childLayouts.length
            ? childLayouts.reduce((sum, child) => sum + child.x, 0) / childLayouts.length
            : padX + leafIndex++ * siblingGap;
          return {
            node,
            depth,
            x,
            y: padY + depth * levelGap,
            children: childLayouts,
          };
        };

        const rootLayout = build(root);
        const nodes = [];
        const links = [];
        let order = 0;
        let maxRight = 0;
        let maxBottom = 0;

        const flatten = (layout, parent = null) => {
          const dotX = layout.x;
          const dotY = layout.y;
          const cardX = dotX + cardOffsetX;
          const cardY = Math.max(16, dotY + cardOffsetY);
          const item = {
            ...layout.node,
            depth: layout.depth,
            dotX,
            dotY,
            cardX,
            cardY,
            order: order++,
            child_count: (layout.node.children || []).length,
            can_delete: Boolean(parent) && !(layout.node.children || []).length,
            parent_missing: Boolean(layout.node.parent_missing || (orphan && layout.depth === 0)),
          };
          nodes.push(item);
          maxRight = Math.max(maxRight, cardX + cardWidth, dotX + 18);
          maxBottom = Math.max(maxBottom, cardY + cardHeight, dotY + 18);

          if (parent) {
            const startX = parent.x;
            const startY = parent.y;
            const endX = dotX;
            const endY = dotY;
            const midY = startY + Math.max(42, (endY - startY) * 0.5);
            const linkId = `${parent.node.save_id || parent.node.filename}->${layout.node.save_id || layout.node.filename}`;
            links.push({
              id: linkId,
              path: `M ${startX} ${startY} C ${startX} ${midY}, ${endX} ${midY}, ${endX} ${endY}`,
              startX,
              startY,
              endX,
              endY,
            });
          }

          layout.children.forEach(child => flatten(child, layout));
        };

        flatten(rootLayout);

        const width = Math.max(330, maxRight + 44, padX * 2 + cardWidth + Math.max(0, leafIndex - 1) * siblingGap);
        const height = Math.max(250, maxBottom + 52, padY * 2 + cardHeight + maxDepth * levelGap);

        return {
          id: root.save_id || root.filename || `${label}-${index}`,
          index,
          root,
          title: root.title || root.focus || root.filename || label,
          kicker: orphan ? label : label,
          orphan,
          nodes,
          links,
          width,
          height,
          frameWidth: Math.max(320, width),
          frameHeight: Math.max(290, height + 56),
        };
      },

      activeWorldGames() {
        return this.worldlineGameRoots().map(item => this.layoutWorldlineTree(item.node, item));
      },

      worldlineLinkSvg(links) {
        return (links || []).map(link => {
          const path = String(link.path || "");
          const startX = Number(link.startX);
          const startY = Number(link.startY);
          const endX = Number(link.endX);
          const endY = Number(link.endY);
          if (!path || !Number.isFinite(startX) || !Number.isFinite(startY) || !Number.isFinite(endX) || !Number.isFinite(endY)) {
            return "";
          }
          return [
            `<path class="worldline-game-link-shadow" d="${path}"></path>`,
            `<path class="worldline-game-link" d="${path}"></path>`,
            `<circle class="worldline-game-link-joint" cx="${startX}" cy="${startY}" r="3.2"></circle>`,
            `<circle class="worldline-game-link-joint" cx="${endX}" cy="${endY}" r="3.2"></circle>`,
          ].join("");
        }).join("");
      },

      clampWorldlineZoom(value) {
        const scale = Number(value);
        if (!Number.isFinite(scale)) return 1;
        return Math.min(2.4, Math.max(0.45, scale));
      },

      worldlineZoomValue() {
        return this.clampWorldlineZoom(this.worldlineZoom || 1);
      },

      worldlineZoomLabel() {
        return `${Math.round(this.worldlineZoomValue() * 100)}%`;
      },

      worldlineCssNumber(name, fallback) {
        const shell = this.$refs?.worldlineShell;
        if (!shell) return fallback;
        const value = Number.parseFloat(getComputedStyle(shell).getPropertyValue(name));
        return Number.isFinite(value) ? value : fallback;
      },

      worldlineRowGeometry() {
        return {
          gap: this.worldlineCssNumber("--worldline-row-gap", 72),
          padX: this.worldlineCssNumber("--worldline-row-pad-x", 34),
          padY: this.worldlineCssNumber("--worldline-row-pad-y", 30),
        };
      },

      worldlineSurfaceMetrics() {
        const games = this.activeWorldGames();
        const { gap, padX, padY } = this.worldlineRowGeometry();
        const { contentWidth, contentHeight } = games.reduce(
          (acc, game) => ({
            contentWidth: acc.contentWidth + (Number(game.frameWidth) || 0),
            contentHeight: Math.max(acc.contentHeight, Number(game.frameHeight) || 0),
          }),
          { contentWidth: 0, contentHeight: 0 }
        );
        const width = padX * 2 + contentWidth + Math.max(0, games.length - 1) * gap;
        const height = padY * 2 + contentHeight + 12;
        return {
          width: Math.max(320, width),
          height: Math.max(260, height),
        };
      },

      worldlineSurfaceStyle() {
        const zoom = this.worldlineZoomValue();
        const metrics = this.worldlineSurfaceMetrics();
        const viewport = this.$refs?.worldlineScroll;
        const width = Math.max(viewport?.clientWidth || 0, metrics.width * zoom);
        const height = Math.max(viewport?.clientHeight || 0, metrics.height * zoom);
        return `width: ${Math.ceil(width)}px; height: ${Math.ceil(height)}px;`;
      },

      worldlineRowStyle() {
        return `transform: scale(${this.worldlineZoomValue()});`;
      },

      setWorldlineZoom(nextZoom, anchor = null) {
        const target = this.$refs?.worldlineScroll;
        const oldZoom = this.worldlineZoomValue();
        const next = this.clampWorldlineZoom(nextZoom);
        if (Math.abs(next - oldZoom) < 0.001) return;

        let anchorX = 0;
        let anchorY = 0;
        let logicalX = 0;
        let logicalY = 0;
        if (target) {
          const rect = target.getBoundingClientRect();
          anchorX = anchor?.x ?? rect.width / 2;
          anchorY = anchor?.y ?? rect.height / 2;
          logicalX = (target.scrollLeft + anchorX) / oldZoom;
          logicalY = (target.scrollTop + anchorY) / oldZoom;
        }

        this.worldlineZoom = next;

        if (target) {
          this.$nextTick(() => {
            target.scrollLeft = logicalX * next - anchorX;
            target.scrollTop = logicalY * next - anchorY;
          });
        }
      },

      zoomWorldlineBy(multiplier, anchor = null) {
        this.setWorldlineZoom(this.worldlineZoomValue() * multiplier, anchor);
      },

      resetWorldlineZoom() {
        this.setWorldlineZoom(1);
      },

      handleWorldlineWheel(event) {
        if (!this.worldlineOpen || this.saveLoading || this.saveError) return;
        event.preventDefault();

        const target = event.currentTarget;
        let deltaY = Number(event.deltaY) || 0;
        if (event.deltaMode === 1) deltaY *= 16;
        if (event.deltaMode === 2) deltaY *= target?.clientHeight || 600;
        if (!deltaY) return;

        const rect = target.getBoundingClientRect();
        const anchor = {
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
        };
        this.zoomWorldlineBy(Math.exp(-deltaY * 0.0011), anchor);
      },

      startWorldlineDrag(event) {
        if (!this.worldlineOpen || event.button !== 0) return;
        if (event.target.closest("button, a, input, textarea, select")) return;
        const target = this.$refs?.worldlineScroll;
        if (!target) return;
        this.worldlineDrag = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          scrollLeft: target.scrollLeft,
          scrollTop: target.scrollTop,
        };
        target.classList.add("dragging");
        target.setPointerCapture?.(event.pointerId);
      },

      dragWorldline(event) {
        const drag = this.worldlineDrag;
        const target = this.$refs?.worldlineScroll;
        if (!drag || !target || drag.pointerId !== event.pointerId) return;
        event.preventDefault();
        target.scrollLeft = drag.scrollLeft - (event.clientX - drag.startX);
        target.scrollTop = drag.scrollTop - (event.clientY - drag.startY);
      },

      stopWorldlineDrag(event) {
        const drag = this.worldlineDrag;
        const target = this.$refs?.worldlineScroll;
        if (!drag || (event?.pointerId != null && drag.pointerId !== event.pointerId)) return;
        if (target?.hasPointerCapture?.(drag.pointerId)) {
          target.releasePointerCapture(drag.pointerId);
        }
        target?.classList.remove("dragging");
        this.worldlineDrag = null;
      },

      findWorldlineNode(saveId) {
        if (!saveId) return null;
        for (const world of this.worlds) {
          const stack = [...(world.roots || []), ...(world.orphans || [])];
          while (stack.length) {
            const node = stack.shift();
            if (node?.save_id === saveId) return node;
            stack.push(...(node?.children || []));
          }
        }
        return null;
      },

      currentWorldlineText() {
        if (!this.currentSaveId) return "当前进度尚未保存为节点";
        const node = this.findWorldlineNode(this.currentSaveId);
        if (!node) return this.currentSaveId;
        return node.title || node.focus || node.filename;
      },

      worldlineNodeKicker(item) {
        if (item.save_id && item.save_id === this.currentSaveId) return "当前节点";
        if (item.parent_missing) return "父节点缺失";
        if (!item.depth) return "根节点";
        if (item.child_count) return `${item.child_count} 条分支`;
        return "节点";
      },

      captureWorldlineGameRects() {
        if (!this.worldlineOpen) return {};
        const rects = {};
        document.querySelectorAll(".worldline-page .worldline-game[data-worldline-game-id]").forEach(element => {
          rects[element.dataset.worldlineGameId] = element.getBoundingClientRect();
        });
        return rects;
      },

      playWorldlineGameFlip(beforeRects) {
        if (!this.worldlineOpen || !beforeRects || !Object.keys(beforeRects).length) return;

        requestAnimationFrame(() => {
          const moved = [];
          document.querySelectorAll(".worldline-page .worldline-game[data-worldline-game-id]").forEach(element => {
            const before = beforeRects[element.dataset.worldlineGameId];
            if (!before) return;

            const after = element.getBoundingClientRect();
            const zoom = this.worldlineZoomValue();
            const dx = (before.left - after.left) / zoom;
            const dy = (before.top - after.top) / zoom;
            if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;

            element.style.transition = "none";
            element.style.transform = `translate(${dx}px, ${dy}px)`;
            element.style.opacity = "0.98";
            moved.push(element);
          });

          if (!moved.length) return;
          requestAnimationFrame(() => {
            moved.forEach(element => {
              element.style.transition = "transform 320ms cubic-bezier(0.16, 1, 0.3, 1), opacity 220ms ease";
              element.style.transform = "translate(0, 0)";
              element.style.opacity = "1";
              window.setTimeout(() => {
                element.style.transition = "";
                element.style.transform = "";
                element.style.opacity = "";
              }, 360);
            });
          });
        });
      },

      worldlineNodeLatestKey(node) {
        if (!node) return "";
        const childLatest = (node.children || []).reduce((latest, child) => {
          const childKey = this.worldlineNodeLatestKey(child);
          return childKey > latest ? childKey : latest;
        }, "");
        const ownKey = node.created_at || node.display || node.display_time || node.filename || "";
        return ownKey > childLatest ? ownKey : childLatest;
      },

      countWorldlineNodes(nodes) {
        return (nodes || []).reduce(
          (total, node) => total + 1 + this.countWorldlineNodes(node.children || []),
          0,
        );
      },

      pruneWorldlineNodes(nodes, deletedFilenames) {
        return (nodes || []).flatMap(node => {
          if (deletedFilenames.has(node.filename)) return [];
          const children = this.pruneWorldlineNodes(node.children || [], deletedFilenames);
          const next = {
            ...node,
            children,
            child_count: children.length,
          };
          next.latest_created_at = this.worldlineNodeLatestKey(next);
          return [next];
        });
      },

      removeSaveFilenamesFromWorlds(filenames) {
        const deletedFilenames = new Set((filenames || []).filter(Boolean));
        if (!deletedFilenames.size) return;

        let removedCurrentNode = false;
        const checkCurrent = nodes => {
          (nodes || []).forEach(node => {
            if (deletedFilenames.has(node.filename) && node.save_id && node.save_id === this.currentSaveId) {
              removedCurrentNode = true;
            }
            checkCurrent(node.children || []);
          });
        };
        this.worlds.forEach(world => {
          checkCurrent(world.roots || []);
          checkCurrent(world.orphans || []);
        });

        this.saves = this.saves.filter(save => !deletedFilenames.has(save.filename));
        this.worlds = this.worlds.map(world => {
          const roots = this.pruneWorldlineNodes(world.roots || [], deletedFilenames);
          const orphans = this.pruneWorldlineNodes(world.orphans || [], deletedFilenames);
          return {
            ...world,
            roots,
            orphans,
            save_count: this.countWorldlineNodes(roots) + this.countWorldlineNodes(orphans),
            root_count: roots.length,
            orphan_count: orphans.length,
          };
        });

        if (removedCurrentNode) {
          this.currentSaveId = "";
        }
        this.ensureActiveWorld();
      },

      syncSavesAfterWorldlineAnimation() {
        window.setTimeout(() => {
          this.refreshSaves({ quiet: true, silent: true });
        }, 380);
      },

      async refreshSaves({ quiet = false, silent = false } = {}) {
        if (!silent) {
          this.saveLoading = true;
        }
        this.saveError = "";
        try {
          const response = await this.fetchJson("/api/saves");
          this.saves = (response.saves || []).map(save => this.normalizeSave(save));
          this.worlds = (response.worlds || []).map(world => this.normalizeWorld(world));
          this.currentSaveId = response.current_save_id || "";
          this.currentStoryId = response.current_story_id || "";
          this.ensureActiveWorld();
        } catch (error) {
          this.saveError = "存档列表加载失败，请稍后再试。";
          if (!quiet) {
            this.setNotice(this.saveError, "error", true);
          }
        } finally {
          if (!silent) {
            this.saveLoading = false;
          }
        }
      },

      promptLoadSave(filename) {
        if (this.busy) {
          this.setNotice("请等待当前回合结束后再读档。", "error");
          return;
        }
        if (this.consolidating) {
          this.setNotice("记忆整理进行中，请稍后再读档。", "error");
          return;
        }
        this.openConfirm({
          title: "读取这个存档？",
          body: `当前运行时会恢复到 "${filename}" 记录的世界线节点。之后新建存档会从该节点继续分支。`,
          confirmText: "读取",
          onConfirm: async () => {
            await this.loadSave(filename);
          },
        });
      },

      promptDeleteGame(game) {
        if (this.busy) {
          this.setNotice("请等待当前回合结束后再删除 Game。", "error");
          return;
        }
        if (this.consolidating) {
          this.setNotice("记忆整理进行中，请稍后再删除 Game。", "error");
          return;
        }
        const rootFilename = game?.root?.filename || "";
        if (!rootFilename) return;
        this.openConfirm({
          title: "删除这个 Game？",
          body: `将永久删除 "${game.title || rootFilename}" 以及它下面的 ${game.nodes?.length || 1} 个存档节点。此操作无法撤销。`,
          confirmText: "删除",
          onConfirm: async () => {
            await this.deleteGame(rootFilename);
          },
        });
      },

      promptDeleteLeafSave(node) {
        if (this.busy) {
          this.setNotice("请等待当前回合结束后再删除存档。", "error");
          return;
        }
        if (this.consolidating) {
          this.setNotice("记忆整理进行中，请稍后再删除存档。", "error");
          return;
        }
        const filename = node?.filename || "";
        if (!filename) return;
        if ((node.child_count || 0) > 0) {
          this.setNotice("这个存档已经是其他分支的 parent，不能单独删除。", "error");
          return;
        }
        this.openConfirm({
          title: "删除这个存档节点？",
          body: `将永久删除 "${node.title || node.focus || filename}"。只有没有子分支的存档节点可以这样删除。`,
          confirmText: "删除",
          onConfirm: async () => {
            await this.deleteLeafSave(filename);
          },
        });
      },

      async deleteLeafSave(filename) {
        this.busy = true;
        try {
          const response = await this.deleteJson(`/api/save-node/${encodeURIComponent(filename)}`);
          const deleted = response.deleted?.length ? response.deleted : [filename];
          const beforeRects = this.captureWorldlineGameRects();
          this.removeSaveFilenamesFromWorlds(deleted);
          this.playWorldlineGameFlip(beforeRects);
          this.syncSavesAfterWorldlineAnimation();
          const count = response.deleted?.length || 0;
          this.pushToast(count ? "存档节点已删除" : "存档已删除", "success");
        } catch (error) {
          this.pushToast("删除失败：只能删除没有子分支的存档。", "error");
        } finally {
          this.busy = false;
        }
      },

      async deleteGame(rootFilename) {
        this.busy = true;
        try {
          const response = await this.deleteJson(`/api/save/${encodeURIComponent(rootFilename)}`);
          const deleted = response.deleted?.length ? response.deleted : [rootFilename];
          const beforeRects = this.captureWorldlineGameRects();
          this.removeSaveFilenamesFromWorlds(deleted);
          this.playWorldlineGameFlip(beforeRects);
          this.syncSavesAfterWorldlineAnimation();
          const count = response.deleted?.length || 0;
          this.pushToast(count ? `Game 已删除（${count} 个节点）` : "Game 已删除", "success");
        } catch (error) {
          this.pushToast("删除失败", "error");
        } finally {
          this.busy = false;
        }
      },

      async loadSave(filename) {
        this.cancelActiveStream({ silent: true });
        this.busy = true;

        try {
          const response = await this.postJson("/api/load", { filename });
          if (!response.ok) {
            throw new Error("load failed");
          }
          this.hasSave = true;
          this.characterCount = response.character_count || 0;
          this.initialRecent = response.recent || [];
          this.initialChoices = response.last_choices || [];
          this.continueGame(response.recent, response.last_choices);
          await this.refreshSaves({ quiet: true });
          await this.fetchCharacters();
          this.closeWorldline();
          this.pushToast("存档已加载", "success");
        } catch (error) {
          this.setNotice("存档读取失败，请稍后再试。", "error", true);
          this.pushToast("读档失败", "error");
        } finally {
          this.busy = false;
        }
      },

      async saveGame() {
        if (this.busy || this.isSaving) return;
        if (!this.messages.length) {
          this.setNotice("还没有可保存的对话内容。", "error");
          return;
        }

        this.isSaving = true;
        try {
          const response = await this.postJson("/api/save", {});

          if (response.ok) {
            this.hasSave = true;
            await this.refreshSaves({ quiet: true });
            const savedName = response.filename || "新节点";
            this.setNotice(`已记录世界线节点：${savedName}`, "success");
            this.pushToast("世界线节点已创建", "success");
          } else {
            throw new Error(response.detail || "存档失败");
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : "存档失败，请稍后再试。";
          this.setNotice(`存档失败：${message}`, "error", true);
          this.pushToast("存档失败", "error");
        } finally {
          this.isSaving = false;
        }
      },
    };
  }

  window.agentGalWorldline = { createState };
})();

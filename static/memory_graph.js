(function () {
  const graphOptions = {
    autoResize: true,
    groups: {
      understanding: {
        shape: "dot",
        color: {
          background: "#b45a64",
          border: "#93444e",
          highlight: { background: "#e8707e", border: "#ffffff" },
          hover: { background: "#bc6370", border: "#93444e" },
        },
        font: { color: "#2f2324" },
      },
      episode: {
        shape: "dot",
        color: {
          background: "#5b7d86",
          border: "#3f6670",
          highlight: { background: "#72a8b8", border: "#ffffff" },
          hover: { background: "#638892", border: "#3f6670" },
        },
        font: { color: "#2f2324" },
      },
      missing_episode: {
        shape: "triangle",
        color: {
          background: "#d9c9bb",
          border: "#9f8174",
          highlight: { background: "#e8d8cc", border: "#ffffff" },
          hover: { background: "#ddd0c4", border: "#9f8174" },
        },
        font: { color: "#6e5753" },
      },
    },
    nodes: {
      borderWidth: 1,
      borderWidthSelected: 3,
      scaling: { min: 10, max: 34 },
      font: {
        face: "Outfit, PingFang SC, Hiragino Sans GB, sans-serif",
        size: 13,
        strokeWidth: 4,
        strokeColor: "rgba(255, 251, 247, 0.9)",
      },
    },
    edges: {
      color: { color: "rgba(113, 84, 74, 0.28)", highlight: "#b45a64", hover: "rgba(113, 84, 74, 0.68)" },
      width: 1,
      hoverWidth: 0,
      smooth: { type: "dynamic" },
      selectionWidth: 2,
    },
    interaction: {
      dragNodes: true,
      hover: true,
      multiselect: false,
      selectable: true,
      selectConnectedEdges: false,
      zoomView: true,
      dragView: true,
    },
    physics: {
      enabled: true,
      solver: "forceAtlas2Based",
      maxVelocity: 14,
      minVelocity: 0.45,
      adaptiveTimestep: true,
      forceAtlas2Based: {
        gravitationalConstant: -42,
        centralGravity: 0.006,
        springLength: 145,
        springConstant: 0.045,
        damping: 0.78,
        avoidOverlap: 0.45,
      },
      stabilization: { enabled: true, iterations: 260, fit: true },
    },
  };

  // All node visual states are managed explicitly via DataSet updates.
  // graphOptions.groups / graphOptions.edges.color are the single source of truth for the base (restored) state.

  // Hover-adjacent + selected: brightest state, hue-shifted, with a soft glow.
  const NODE_SELECTED_STYLES = {
    understanding: {
      color: {
        background: "#d9607a",
        border: "#b84868",
        highlight: { background: "#d9607a", border: "#b84868" },
        hover: { background: "#d9607a", border: "#b84868" },
      },
      font: { color: "#2f2324" },
      borderWidth: 3,
      shadow: { enabled: true, color: "rgba(217, 96, 122, 0.38)", size: 14, x: 0, y: 0 },
    },
    episode: {
      color: {
        background: "#5aaabb",
        border: "#3d8898",
        highlight: { background: "#5aaabb", border: "#3d8898" },
        hover: { background: "#5aaabb", border: "#3d8898" },
      },
      font: { color: "#2f2324" },
      borderWidth: 3,
      shadow: { enabled: true, color: "rgba(90, 170, 187, 0.38)", size: 14, x: 0, y: 0 },
    },
    missing_episode: {
      color: {
        background: "#d4b0a0",
        border: "#b89080",
        highlight: { background: "#d4b0a0", border: "#b89080" },
        hover: { background: "#d4b0a0", border: "#b89080" },
      },
      font: { color: "#6e5753" },
      borderWidth: 3,
      shadow: { enabled: true, color: "rgba(180, 140, 120, 0.32)", size: 12, x: 0, y: 0 },
    },
  };

  // Hover not-adjacent + selected: preserves hue shift direction so it reads as "still selected", but pulled back.
  const NODE_SELECTED_DIM_STYLES = {
    understanding: {
      color: {
        background: "#b85a6c",
        border: "#c86878",
        highlight: { background: "#b85a6c", border: "#c86878" },
        hover: { background: "#b85a6c", border: "#c86878" },
      },
      font: { color: "rgba(47, 35, 36, 0.72)" },
      borderWidth: 2,
    },
    episode: {
      color: {
        background: "#588898",
        border: "#6898a8",
        highlight: { background: "#588898", border: "#6898a8" },
        hover: { background: "#588898", border: "#6898a8" },
      },
      font: { color: "rgba(47, 35, 36, 0.72)" },
      borderWidth: 2,
    },
    missing_episode: {
      color: {
        background: "#c8b0a0",
        border: "#b09080",
        highlight: { background: "#c8b0a0", border: "#b09080" },
        hover: { background: "#c8b0a0", border: "#b09080" },
      },
      font: { color: "rgba(110, 87, 83, 0.64)" },
      borderWidth: 2,
    },
  };

  const NODE_DIM_STYLES = {
    understanding: {
      color: { background: "#d3a2a3", border: "#c29798", highlight: { background: "#d3a2a3", border: "#c29798" }, hover: { background: "#d3a2a3", border: "#c29798" } },
      font: { color: "rgba(47, 35, 36, 0.4)" },
    },
    episode: {
      color: { background: "#a6b4b4", border: "#98a8a9", highlight: { background: "#a6b4b4", border: "#98a8a9" }, hover: { background: "#a6b4b4", border: "#98a8a9" } },
      font: { color: "rgba(47, 35, 36, 0.4)" },
    },
    missing_episode: {
      color: { background: "#e5dacf", border: "#c8b6ab", highlight: { background: "#e5dacf", border: "#c8b6ab" }, hover: { background: "#e5dacf", border: "#c8b6ab" } },
      font: { color: "rgba(110, 87, 83, 0.4)" },
    },
  };
  const EDGE_DIM_COLOR = {
    color: "rgba(113, 84, 74, 0.06)",
    highlight: "rgba(113, 84, 74, 0.06)",
    hover: "rgba(113, 84, 74, 0.06)",
  };

  function isReady() {
    return Boolean(window.vis && window.vis.Network && window.vis.DataSet);
  }

  function clampZoom(value) {
    const scale = Number(value);
    if (!Number.isFinite(scale)) return 1;
    return Math.min(2.5, Math.max(0.2, scale));
  }

  function splitHistoryClauses(text) {
    const normalized = String(text || "").replace(/\s+/g, " ").trim();
    if (!normalized) return [];
    const clauses = normalized.match(/[^；;。！？!?，,\n]+[；;。！？!?，,]?/g);
    return (clauses || [normalized]).map(item => item.trim()).filter(Boolean);
  }

  function diffHistoryClauses(previousText, currentText) {
    const previous = splitHistoryClauses(previousText);
    const current = splitHistoryClauses(currentText);
    if (!previous.length) {
      return current.map(text => ({ type: "added", text }));
    }
    if (!current.length) return [];

    const dp = Array.from({ length: previous.length + 1 }, () =>
      Array(current.length + 1).fill(0),
    );
    for (let i = previous.length - 1; i >= 0; i -= 1) {
      for (let j = current.length - 1; j >= 0; j -= 1) {
        dp[i][j] =
          previous[i] === current[j]
            ? dp[i + 1][j + 1] + 1
            : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }

    const diff = [];
    let i = 0;
    let j = 0;
    while (i < previous.length && j < current.length) {
      if (previous[i] === current[j]) {
        diff.push({ type: "same", text: current[j] });
        i += 1;
        j += 1;
      } else if (dp[i + 1][j] >= dp[i][j + 1]) {
        diff.push({ type: "removed", text: previous[i] });
        i += 1;
      } else {
        diff.push({ type: "added", text: current[j] });
        j += 1;
      }
    }
    while (i < previous.length) {
      diff.push({ type: "removed", text: previous[i] });
      i += 1;
    }
    while (j < current.length) {
      diff.push({ type: "added", text: current[j] });
      j += 1;
    }
    return diff;
  }

  function createNetwork({ container, nodes, edges, onSelectNode, onSelectEdge, onBlank, onZoom }) {
    if (!isReady()) {
      throw new Error("vis-network is not available");
    }
    const nodesDataset = new window.vis.DataSet(nodes);
    const edgesDataset = new window.vis.DataSet(edges);
    const network = new window.vis.Network(
      container,
      { nodes: nodesDataset, edges: edgesDataset },
      graphOptions,
    );

    let selectedNodeId = null;

    // Updates ALL nodes on every call so hover-focus and selection-focus are always consistent.
    function dimExcept(focusNodeIds, focusEdgeIds) {
      const updatedNodes = nodesDataset.get().map(n => {
        const inFocus = focusNodeIds.includes(n.id);
        const isSelected = n.id === selectedNodeId;
        if (inFocus && isSelected) {
          const s = NODE_SELECTED_STYLES[n.group] || NODE_SELECTED_STYLES.episode;
          return { id: n.id, color: s.color, font: s.font, borderWidth: s.borderWidth, shadow: s.shadow, chosen: false };
        }
        if (inFocus) {
          const base = graphOptions.groups[n.group] || graphOptions.groups.episode;
          return { id: n.id, color: base.color, font: base.font, borderWidth: 1, shadow: { enabled: false }, chosen: true };
        }
        if (isSelected) {
          const s = NODE_SELECTED_DIM_STYLES[n.group] || NODE_SELECTED_DIM_STYLES.episode;
          return { id: n.id, color: s.color, font: s.font, borderWidth: s.borderWidth, shadow: { enabled: false }, chosen: false };
        }
        const dim = NODE_DIM_STYLES[n.group] || NODE_DIM_STYLES.episode;
        return { id: n.id, color: dim.color, font: dim.font, borderWidth: 1, shadow: { enabled: false }, chosen: false };
      });
      const updatedEdges = edgesDataset.getIds().map(id => ({
        id,
        color: focusEdgeIds.includes(id) ? graphOptions.edges.color : EDGE_DIM_COLOR,
      }));
      nodesDataset.update(updatedNodes);
      edgesDataset.update(updatedEdges);
    }

    function restoreOpacity() {
      const restoredNodes = nodesDataset.get().map(n => {
        const base = graphOptions.groups[n.group] || graphOptions.groups.episode;
        return { id: n.id, color: base.color, font: base.font, borderWidth: 1, shadow: { enabled: false }, chosen: true };
      });
      const restoredEdges = edgesDataset.getIds()
        .map(id => ({ id, color: graphOptions.edges.color }));
      nodesDataset.update(restoredNodes);
      edgesDataset.update(restoredEdges);
    }

    function applySelectionFocus(nodeId) {
      if (nodeId === null) {
        restoreOpacity();
      } else {
        const neighborEdges = network.getConnectedEdges(nodeId);
        const neighborNodes = network.getConnectedNodes(nodeId);
        dimExcept([nodeId, ...neighborNodes], neighborEdges);
      }
    }

    network.on("hoverNode", params => {
      const neighborEdges = network.getConnectedEdges(params.node);
      const neighborNodes = network.getConnectedNodes(params.node);
      dimExcept([params.node, ...neighborNodes], neighborEdges);
    });
    network.on("hoverEdge", params => {
      const edge = edgesDataset.get(params.edge);
      const keepNodes = edge ? [edge.from, edge.to] : [];
      dimExcept(keepNodes, [params.edge]);
    });
    network.on("blurNode", () => applySelectionFocus(selectedNodeId));
    network.on("blurEdge", () => applySelectionFocus(selectedNodeId));

    // Single entry point for changing selection: keeps selectedNodeId and the visual focus in sync.
    function setSelectedNode(nodeId) {
      selectedNodeId = nodeId;
      applySelectionFocus(nodeId);
    }

    network.on("click", params => {
      const nodeId = params.nodes[0] || network.getNodeAt(params.pointer.DOM);
      if (nodeId && onSelectNode) {
        setSelectedNode(nodeId);
        onSelectNode(nodeId);
        return;
      }
      const edgeId = params.edges[0] || network.getEdgeAt(params.pointer.DOM);
      if (edgeId && onSelectEdge) {
        setSelectedNode(null);
        onSelectEdge(edgeId);
        return;
      }
      if (onBlank) {
        setSelectedNode(null);
        onBlank();
      }
    });

    // External callers (e.g. openEpisodeFromHistory) can notify the network about programmatic selection.
    network.setSelectedNode = setSelectedNode;

    const fireZoom = scale => onZoom && onZoom(clampZoom(scale));
    network.on("zoom", params => fireZoom(params.scale));
    network.once("stabilizationIterationsDone", () => {
      network.stopSimulation();
      syncNetworkSize(network, container);
      fireZoom(network.getScale());
    });

    return network;
  }

  function syncNetworkSize(network, container) {
    network.setSize("100%", "100%");
    fitNetwork(network);
  }

  function fitNetwork(network) {
    network.redraw();
    network.fit({
      animation: { duration: 420, easingFunction: "easeInOutQuad" },
    });
  }

  function afterPaint(callback) {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(callback);
    });
  }

  function createState() {
    return {
      memoryGraphOpen: false,
      memoryGraphLoading: false,
      memoryGraphError: "",
      memoryGraphAgent: "",
      memoryGraphAgents: [],
      memoryGraphStats: {},
      memoryGraphNodes: [],
      memoryGraphEdges: [],
      memoryGraphSelected: null,
      memoryGraphNetwork: null,
      memoryGraphZoom: 1,
      memoryGraphDetailCollapsed: false,
      memoryGraphDetailPrev: null,

      async openMemoryGraph(agentName = null) {
        if (this.isCompact) {
          this.closeDrawer();
        }
        if (agentName) {
          this.memoryGraphAgent = agentName;
        }
        this.memoryGraphOpen = true;
        await this.$nextTick();
        await this.loadMemoryGraph(this.memoryGraphAgent || null);
      },

      closeMemoryGraph() {
        this.memoryGraphOpen = false;
        this.destroyMemoryGraphNetwork();
      },

      toggleMemoryGraphDetail() {
        this.memoryGraphDetailCollapsed = !this.memoryGraphDetailCollapsed;
      },

      destroyMemoryGraphNetwork() {
        if (!this.memoryGraphNetwork) return;
        this.memoryGraphNetwork.destroy();
        this.memoryGraphNetwork = null;
      },

      memoryGraphSubtitle() {
        if (this.memoryGraphError) return this.memoryGraphError;
        const agent = this.memoryGraphAgents.find(item => item.name === this.memoryGraphAgent);
        if (!agent) return "Understanding / Episode";
        return `${agent.display_name} · ${agent.understanding_count || 0} understandings · ${agent.episode_count || 0} episodes`;
      },

      memoryGraphEmptyText() {
        if (this.memoryGraphLoading) return "正在加载记忆图谱。";
        if (this.memoryGraphError) return this.memoryGraphError;
        if (!this.memoryGraphAgents.length) return "当前没有可展示的角色记忆。";
        return "当前角色还没有 understanding 或 episode。";
      },

      memoryGraphMetaPills() {
        const item = this.memoryGraphSelected;
        if (!item) return [];
        if (item.type === "edge") {
          const pills = [];
          if (item.episode_id) pills.push(`episode ${item.episode_id}`);
          if (item.understanding_id) pills.push(`understanding ${item.understanding_id}`);
          return pills;
        }
        const pills = [];
        if (item.location) pills.push(item.location);
        if (item.participants) pills.push(item.participants);
        if (Array.isArray(item.linked_episodes)) {
          pills.push(`${item.linked_episodes.length} linked episodes`);
        }
        (item.keywords || []).slice(0, 8).forEach(keyword => pills.push(keyword));
        return pills;
      },

      memoryGraphEpisodeFacts() {
        const item = this.memoryGraphSelected;
        if (!item || !["episode", "edge"].includes(item.type)) return [];
        const source = item.type === "edge" ? item.episode : item;
        if (!source) return [];
        const facts = [];
        const date = String(source.date || "").trim();
        const time = String(source.time || "").trim();
        const dateLabel =
          date && time ? (time.startsWith(date) ? time : `${date} · ${time}`) : date || time;
        if (dateLabel) facts.push(dateLabel);
        if (source.importance) facts.push(`importance ${source.importance}`);
        return facts;
      },

      memoryGraphHistoryHeading(entry) {
        if (!entry) return "";
        const date = (entry.date || "").trim();
        const title = (entry.title || "").trim();
        return [date, title].filter(Boolean).join(" · ");
      },

      memoryGraphHistoryEntries() {
        const item = this.memoryGraphSelected;
        if (!item || !Array.isArray(item.history)) return [];
        return item.history
          .filter(entry => String(entry.content || "").trim())
          .map((entry, index, entries) => ({
            ...entry,
            diff: diffHistoryClauses(index > 0 ? entries[index - 1].content : "", entry.content),
          }))
          .reverse();
      },

      memoryGraphEdgeDiffSegments() {
        const item = this.memoryGraphSelected;
        if (!item || item.type !== "edge" || !Array.isArray(item.diff)) return [];
        return item.diff;
      },

      async loadMemoryGraph(agentName = null) {
        this.memoryGraphLoading = true;
        this.memoryGraphError = "";
        this.memoryGraphSelected = null;
        this.memoryGraphDetailPrev = null;
        this.memoryGraphZoom = 1;
        this.destroyMemoryGraphNetwork();

        try {
          const query = agentName
            ? `?${new URLSearchParams({ agent: agentName }).toString()}`
            : "";
          const response = await this.fetchJson(`/api/memory-graph${query}`);
          this.memoryGraphAgents = response.agents || [];
          this.memoryGraphAgent = response.selected_agent || agentName || "";
          this.memoryGraphStats = response.stats || {};
          this.memoryGraphNodes = response.nodes || [];
          this.memoryGraphEdges = response.edges || [];
        } catch (error) {
          this.memoryGraphError = "记忆图谱加载失败。";
          this.memoryGraphStats = {};
          this.memoryGraphNodes = [];
          this.memoryGraphEdges = [];
        } finally {
          this.memoryGraphLoading = false;
        }

        await this.$nextTick();
        this.renderMemoryGraphNetwork();
      },

      renderMemoryGraphNetwork() {
        if (!this.memoryGraphOpen || this.memoryGraphLoading || this.memoryGraphError) return;
        if (!this.memoryGraphNodes.length || !this.$refs.memoryGraphNetwork) return;
        if (!isReady()) {
          this.memoryGraphError = "vis-network 未加载。";
          return;
        }

        this.destroyMemoryGraphNetwork();
        this.memoryGraphNetwork = createNetwork({
          container: this.$refs.memoryGraphNetwork,
          nodes: this.memoryGraphNodes,
          edges: this.memoryGraphEdges,
          onSelectNode: nodeId => this.selectMemoryGraphNode(nodeId),
          onSelectEdge: edgeId => this.selectMemoryGraphEdge(edgeId),
          onBlank: () => {
            this.memoryGraphDetailCollapsed = true;
          },
          onZoom: scale => {
            if (scale !== this.memoryGraphZoom) this.memoryGraphZoom = scale;
          },
        });
        afterPaint(() => {
          if (this.memoryGraphNetwork) {
            syncNetworkSize(this.memoryGraphNetwork, this.$refs.memoryGraphNetwork);
          }
        });
        if (this.memoryGraphNodes[0]) {
          const firstId = this.memoryGraphNodes[0].id;
          this.selectMemoryGraphNode(firstId);
          if (this.memoryGraphNetwork) {
            this.memoryGraphNetwork.setSelectedNode(firstId);
          }
        }
      },

      selectMemoryGraphNode(nodeId) {
        const node = this.memoryGraphNodes.find(item => item.id === nodeId);
        this.memoryGraphSelected = node ? node.meta : null;
        this.memoryGraphDetailPrev = null;
        if (node) {
          this.memoryGraphDetailCollapsed = false;
        }
      },

      selectMemoryGraphEdge(edgeId) {
        const edge = this.memoryGraphEdges.find(item => item.id === edgeId);
        if (!edge) {
          this.memoryGraphSelected = null;
          return;
        }
        const fromNode = this.memoryGraphNodes.find(item => item.id === edge.from);
        const toNode = this.memoryGraphNodes.find(item => item.id === edge.to);
        const candidates = [fromNode, toNode].filter(Boolean);
        const episodeNode = candidates.find(item =>
          ["episode", "missing_episode"].includes(item.meta && item.meta.type),
        );
        const understandingNode = candidates.find(item => item.meta && item.meta.type === "understanding");
        if (!episodeNode || !understandingNode) {
          this.memoryGraphSelected = null;
          return;
        }

        const episode = episodeNode.meta;
        const understanding = understandingNode.meta;
        const episodeId = (edge.meta && edge.meta.episode_id) || episode.id;
        const historyEntries = Array.isArray(understanding.history)
          ? understanding.history.filter(entry => String(entry.content || "").trim())
          : [];
        const historyIndex = historyEntries.findIndex(
          entry => String(entry.episode_id || "") === String(episodeId || ""),
        );
        const historyEntry = historyIndex >= 0 ? historyEntries[historyIndex] : null;
        const diff = historyEntry
          ? diffHistoryClauses(
              historyIndex > 0 ? historyEntries[historyIndex - 1].content : "",
              historyEntry.content,
            )
          : [];
        const hasVisibleDiff = diff.some(segment => segment.type !== "same");

        this.memoryGraphDetailPrev = null;
        this.memoryGraphSelected = {
          id: edge.id,
          type: "edge",
          type_label: "Episode Impact",
          title: [episode.title, understanding.title].filter(Boolean).join(" / "),
          episode,
          understanding,
          episode_id: episodeId,
          understanding_id: (edge.meta && edge.meta.understanding_id) || understanding.id,
          history_entry: historyEntry,
          diff: hasVisibleDiff ? diff : [],
        };
        this.memoryGraphDetailCollapsed = false;
      },

      openEpisodeFromHistory(episodeId) {
        if (!episodeId) return;
        const node = this.memoryGraphNodes.find(
          n => n.meta && String(n.meta.id || "") === String(episodeId) &&
               ["episode", "missing_episode"].includes(n.meta.type),
        );
        if (!node) return;
        const currentMeta = this.memoryGraphSelected;
        const currentNode =
          currentMeta && currentMeta.type !== "edge"
            ? this.memoryGraphNodes.find(n => n.meta && n.meta.id === currentMeta.id)
            : null;
        this.memoryGraphDetailPrev = {
          selected: currentMeta,
          nodeId: currentNode ? currentNode.id : null,
        };
        this.memoryGraphSelected = node.meta;
        if (this.memoryGraphNetwork) {
          this.memoryGraphNetwork.selectNodes([node.id]);
          this.memoryGraphNetwork.setSelectedNode(node.id);
        }
      },

      goBackMemoryGraphDetail() {
        const prev = this.memoryGraphDetailPrev;
        this.memoryGraphSelected = prev ? prev.selected : null;
        this.memoryGraphDetailPrev = null;
        if (this.memoryGraphNetwork && prev && prev.nodeId) {
          this.memoryGraphNetwork.selectNodes([prev.nodeId]);
        }
      },

      setMemoryGraphZoom(value) {
        if (!this.memoryGraphNetwork) return;
        const scale = clampZoom(value);
        this.memoryGraphZoom = scale;
        this.memoryGraphNetwork.moveTo({ scale, animation: false });
      },
    };
  }

  window.agentGalMemoryGraph = {
    createState,
    createNetwork,
    isReady,
  };
})();

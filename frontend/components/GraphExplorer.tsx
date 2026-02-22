"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { fetchCitationGraph } from "@/lib/api";
import type { APICitationGraph, GraphEdge, GraphNode } from "@/types";
import { fmt } from "@/lib/utils";

// ── Visual constants ─────────────────────────────────────────────────────────
const NODE_COLORS: Record<GraphNode["type"], string> = {
  paper: "#2563EB",
  author: "#059669",
  topic: "#D97706",
  institution: "#7C3AED",
};
const CANVAS_BG = "#F9F8F6";
const PANEL_BG = "#FFFFFF";
const BORDER = "#E8E5E0";
const TEXT_PRIMARY = "#111111";
const TEXT_MUTED = "#888888";

// ── Simulation node extends GraphNode ────────────────────────────────────────
interface SimNode extends GraphNode {
  vx: number;
  vy: number;
  pinned: boolean;
}

interface Transform {
  x: number;
  y: number;
  scale: number;
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function getRadius(n: SimNode | GraphNode, focusId: string | null): number {
  if (n.id === focusId) return 28;
  if (n.type === "paper") return 18;
  if (n.type === "author") return 15;
  if (n.type === "institution") return 14;
  return 11;
}

function hexToRgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${a})`;
}

function buildSimNodes(
  graph: APICitationGraph,
  focusId: string,
  w: number,
  h: number
): SimNode[] {
  const cx = w / 2;
  const cy = h / 2;
  return graph.nodes.map((n) => {
    const isFocus = n.id === focusId;
    const angle = Math.random() * Math.PI * 2;
    const r = isFocus ? 0 : 60 + Math.random() * 100;
    return {
      id: n.id,
      label: n.title ?? n.id,
      type: "paper" as const,
      year: n.publication_year ?? undefined,
      citations: n.citation_count ?? undefined,
      x: isFocus ? cx : cx + Math.cos(angle) * r,
      y: isFocus ? cy : cy + Math.sin(angle) * r,
      vx: isFocus ? 0 : (Math.random() - 0.5) * 3,
      vy: isFocus ? 0 : (Math.random() - 0.5) * 3,
      pinned: isFocus,
    };
  });
}

// ── Force simulation tick ─────────────────────────────────────────────────────
function tickSimulation(
  nodes: SimNode[],
  edges: GraphEdge[],
  alpha: number,
  w: number,
  h: number
): number {
  const REPULSION = 5500;
  const SPRING_K = 0.07;
  const IDEAL_LEN = 130;
  const GRAVITY = 0.04;
  const DAMP = 0.82;
  const cx = w / 2;
  const cy = h / 2;

  const map = new Map(nodes.map((n) => [n.id, n]));

  // Gravity toward center
  for (const n of nodes) {
    if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
    n.vx += (cx - n.x) * GRAVITY * alpha;
    n.vy += (cy - n.y) * GRAVITY * alpha;
  }

  // Pairwise repulsion (O(n²), fine for ≤ 500 nodes)
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = Math.max(dx * dx + dy * dy, 1);
      const d = Math.sqrt(d2);
      const f = (REPULSION / d2) * alpha;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      if (!a.pinned) { a.vx += fx; a.vy += fy; }
      if (!b.pinned) { b.vx -= fx; b.vy -= fy; }
    }
  }

  // Spring attraction along edges
  for (const e of edges) {
    const src = map.get(e.source);
    const tgt = map.get(e.target);
    if (!src || !tgt) continue;
    const dx = tgt.x - src.x;
    const dy = tgt.y - src.y;
    const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
    const f = SPRING_K * (d - IDEAL_LEN) * alpha;
    const fx = (dx / d) * f;
    const fy = (dy / d) * f;
    if (!src.pinned) { src.vx += fx; src.vy += fy; }
    if (!tgt.pinned) { tgt.vx -= fx; tgt.vy -= fy; }
  }

  // Integrate
  for (const n of nodes) {
    if (n.pinned) continue;
    n.vx *= DAMP;
    n.vy *= DAMP;
    n.x += n.vx;
    n.y += n.vy;
  }

  return alpha * 0.98;
}

// ── Canvas draw ───────────────────────────────────────────────────────────────
function drawGraph(
  ctx: CanvasRenderingContext2D,
  nodes: SimNode[],
  edges: GraphEdge[],
  transform: Transform,
  focusId: string | null,
  selectedId: string | null,
  hoveredId: string | null,
  typeFilter: Record<GraphNode["type"], boolean>
) {
  const dpr = window.devicePixelRatio || 1;
  const logW = ctx.canvas.width / dpr;
  const logH = ctx.canvas.height / dpr;

  ctx.save();
  ctx.scale(dpr, dpr);

  // Background
  ctx.fillStyle = CANVAS_BG;
  ctx.fillRect(0, 0, logW, logH);

  const visNodes = nodes.filter((n) => typeFilter[n.type]);
  const visIds = new Set(visNodes.map((n) => n.id));
  const visEdges = edges.filter((e) => visIds.has(e.source) && visIds.has(e.target));

  ctx.translate(transform.x, transform.y);
  ctx.scale(transform.scale, transform.scale);

  const nodeMap = new Map(visNodes.map((n) => [n.id, n]));
  const invScale = 1 / transform.scale;

  // ── Edges ────────────────────────────────────────────────────────────────
  for (const edge of visEdges) {
    const src = nodeMap.get(edge.source);
    const tgt = nodeMap.get(edge.target);
    if (!src || !tgt) continue;

    const highlighted =
      edge.source === selectedId || edge.target === selectedId ||
      edge.source === hoveredId || edge.target === hoveredId;

    const angle = Math.atan2(tgt.y - src.y, tgt.x - src.x);
    const srcR = getRadius(src, focusId);
    const tgtR = getRadius(tgt, focusId);
    const ex = tgt.x - Math.cos(angle) * (tgtR + 3);
    const ey = tgt.y - Math.sin(angle) * (tgtR + 3);
    const sx = src.x + Math.cos(angle) * srcR;
    const sy = src.y + Math.sin(angle) * srcR;

    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(ex, ey);
    ctx.strokeStyle = highlighted ? "rgba(0,0,0,0.45)" : "rgba(0,0,0,0.12)";
    ctx.lineWidth = (highlighted ? 1.5 : 0.8) * invScale;
    ctx.stroke();

    // Arrowhead
    const arrowLen = 7 * invScale;
    ctx.beginPath();
    ctx.moveTo(ex, ey);
    ctx.lineTo(
      ex - arrowLen * Math.cos(angle - 0.42),
      ey - arrowLen * Math.sin(angle - 0.42)
    );
    ctx.lineTo(
      ex - arrowLen * Math.cos(angle + 0.42),
      ey - arrowLen * Math.sin(angle + 0.42)
    );
    ctx.closePath();
    ctx.fillStyle = highlighted ? "rgba(0,0,0,0.45)" : "rgba(0,0,0,0.18)";
    ctx.fill();
  }

  // ── Nodes ─────────────────────────────────────────────────────────────────
  for (const n of visNodes) {
    const r = getRadius(n, focusId);
    const col = NODE_COLORS[n.type];
    const isFocus = n.id === focusId;
    const isSel = n.id === selectedId;
    const isHov = n.id === hoveredId;

    // Outer glow
    if (isFocus || isSel || isHov) {
      const glowR = r + (isFocus ? 22 : 16);
      const grad = ctx.createRadialGradient(n.x, n.y, r * 0.4, n.x, n.y, glowR);
      grad.addColorStop(0, hexToRgba(col, isFocus ? 0.22 : isSel ? 0.18 : 0.12));
      grad.addColorStop(1, hexToRgba(col, 0));
      ctx.beginPath();
      ctx.arc(n.x, n.y, glowR, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    // Outer ring
    ctx.beginPath();
    ctx.arc(n.x, n.y, r + 3 * invScale, 0, Math.PI * 2);
    ctx.fillStyle = isSel ? col : hexToRgba(col, isFocus ? 0.25 : isHov ? 0.18 : 0.1);
    ctx.fill();

    // Inner fill
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fillStyle =
      isFocus || isSel ? col : isHov ? hexToRgba(col, 0.18) : "#FFFFFF";
    ctx.fill();

    // Stroke
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.strokeStyle = col;
    ctx.lineWidth = (isFocus || isSel ? 2.5 : isHov ? 2 : 1.5) * invScale;
    ctx.stroke();

    // Type glyph
    const glyphSize = (r >= 18 ? 11 : 9) * invScale;
    ctx.font = `bold ${glyphSize}px sans-serif`;
    ctx.fillStyle = isFocus || isSel ? "#FFF" : col;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("P", n.x, n.y);

    // Label below (focus always, others on hover/select)
    if (isFocus || isSel || isHov) {
      const text = n.label.length > 34 ? n.label.slice(0, 34) + "…" : n.label;
      const labelSize = 11 * invScale;
      ctx.font = `${labelSize}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      const tw = ctx.measureText(text).width;
      const ty = n.y + r + 7 * invScale;
      const pad = 4 * invScale;
      const br = 3 * invScale;
      const bx = n.x - tw / 2 - pad;
      const by = ty - pad * 0.4;
      const bw = tw + pad * 2;
      const bh = labelSize + pad * 1.2;

      // Rounded rect background
      ctx.beginPath();
      ctx.moveTo(bx + br, by);
      ctx.arcTo(bx + bw, by, bx + bw, by + bh, br);
      ctx.arcTo(bx + bw, by + bh, bx, by + bh, br);
      ctx.arcTo(bx, by + bh, bx, by, br);
      ctx.arcTo(bx, by, bx + bw, by, br);
      ctx.closePath();
      ctx.fillStyle = "rgba(255,255,255,0.92)";
      ctx.fill();
      ctx.strokeStyle = hexToRgba(col, 0.25);
      ctx.lineWidth = 0.5 * invScale;
      ctx.stroke();

      ctx.fillStyle = "#111";
      ctx.fillText(text, n.x, ty);
    }
  }

  ctx.restore();
}

// ── Sidebar / drawer shared styles ───────────────────────────────────────────
const drawerBtn = (variant: "primary" | "default" | "danger"): React.CSSProperties => ({
  width: "100%",
  padding: "9px 14px",
  borderRadius: 8,
  fontSize: 13,
  fontWeight: 500,
  cursor: "pointer",
  fontFamily: "var(--font-dm-sans), sans-serif",
  textAlign: "center",
  transition: "opacity 0.15s",
  ...(variant === "primary"
    ? { background: "#111", color: "#FFF", border: "none" }
    : variant === "danger"
    ? { background: PANEL_BG, color: "#DC2626", border: `1px solid #FECACA` }
    : { background: PANEL_BG, color: TEXT_PRIMARY, border: `1px solid ${BORDER}` }),
});

// ── Graph canvas component ────────────────────────────────────────────────────
function GraphCanvas() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const focusType = searchParams.get("type") as GraphNode["type"] | null;
  const focusId = searchParams.get("id");

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Simulation state (refs = no re-render on tick)
  const simNodesRef = useRef<SimNode[]>([]);
  const simEdgesRef = useRef<GraphEdge[]>([]);
  const alphaRef = useRef(0);
  const transformRef = useRef<Transform>({ x: 0, y: 0, scale: 1 });
  const rafRef = useRef<number | null>(null);
  const hoveredIdRef = useRef<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const draggingNodeRef = useRef<SimNode | null>(null);
  const dragMovedRef = useRef(false);
  const isPanningRef = useRef(false);
  const panStartRef = useRef<{ mx: number; my: number; tx: number; ty: number } | null>(null);
  const typeFilterRef = useRef<Record<GraphNode["type"], boolean>>({
    paper: true, author: true, topic: true, institution: true,
  });
  const focusIdRef = useRef(focusId);

  // React state (drives re-renders only where UI needs updating)
  const [loading, setLoading] = useState(false);
  const [hasNodes, setHasNodes] = useState(false);
  const [selectedNode, setSelectedNode] = useState<SimNode | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [citDepth, setCitDepth] = useState(1);
  const [typeFilter, setTypeFilter] = useState<Record<GraphNode["type"], boolean>>({
    paper: true, author: true, topic: true, institution: true,
  });
  const [stats, setStats] = useState({ nodes: 0, edges: 0 });

  // Keep refs in sync with state/props
  useEffect(() => { typeFilterRef.current = typeFilter; }, [typeFilter]);
  useEffect(() => { selectedIdRef.current = selectedNode?.id ?? null; }, [selectedNode]);
  useEffect(() => { focusIdRef.current = focusId; }, [focusId]);

  // ── Canvas setup ─────────────────────────────────────────────────────────
  const setupCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = container.clientWidth * dpr;
    canvas.height = container.clientHeight * dpr;
    canvas.style.width = `${container.clientWidth}px`;
    canvas.style.height = `${container.clientHeight}px`;
  }, []);

  useEffect(() => {
    setupCanvas();
    const ro = new ResizeObserver(setupCanvas);
    if (containerRef.current) ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [setupCanvas]);

  // ── Render loop ───────────────────────────────────────────────────────────
  const loop = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;

    if (alphaRef.current > 0.005) {
      alphaRef.current = tickSimulation(
        simNodesRef.current,
        simEdgesRef.current,
        alphaRef.current,
        w,
        h
      );
    }

    drawGraph(
      ctx,
      simNodesRef.current,
      simEdgesRef.current,
      transformRef.current,
      focusIdRef.current,
      selectedIdRef.current,
      hoveredIdRef.current,
      typeFilterRef.current
    );

    rafRef.current = requestAnimationFrame(loop);
  }, []);

  const startLoop = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(loop);
  }, [loop]);

  useEffect(() => () => { if (rafRef.current != null) cancelAnimationFrame(rafRef.current); }, []);

  // ── Load graph data ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!focusId || focusType !== "paper") {
      simNodesRef.current = [];
      simEdgesRef.current = [];
      alphaRef.current = 0;
      setHasNodes(false);
      return;
    }
    setLoading(true);
    setHasNodes(false);
    setSelectedNode(null);
    setDrawerOpen(false);
    selectedIdRef.current = null;

    fetchCitationGraph(focusId, citDepth).then((graph) => {
      if (graph && graph.nodes.length > 0) {
        const canvas = canvasRef.current;
        const dpr = window.devicePixelRatio || 1;
        const w = canvas ? canvas.width / dpr : 800;
        const h = canvas ? canvas.height / dpr : 600;
        simNodesRef.current = buildSimNodes(graph, focusId, w, h);
        simEdgesRef.current = graph.edges.map((e) => ({
          source: e.source,
          target: e.target,
          type: "citation" as const,
        }));
        alphaRef.current = 1.0;
        transformRef.current = { x: 0, y: 0, scale: 1 };
        setStats({ nodes: simNodesRef.current.length, edges: simEdgesRef.current.length });
        setHasNodes(true);
        startLoop();
      }
      setLoading(false);
    });
  }, [focusId, focusType, citDepth, startLoop]);

  // ── Coordinate helpers ────────────────────────────────────────────────────
  const clientToSim = useCallback((clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const t = transformRef.current;
    return {
      x: (clientX - rect.left - t.x) / t.scale,
      y: (clientY - rect.top - t.y) / t.scale,
    };
  }, []);

  const hitTest = useCallback((sx: number, sy: number): SimNode | null => {
    const vis = simNodesRef.current.filter((n) => typeFilterRef.current[n.type]);
    for (let i = vis.length - 1; i >= 0; i--) {
      const n = vis[i];
      if (Math.hypot(sx - n.x, sy - n.y) < getRadius(n, focusIdRef.current) + 5) return n;
    }
    return null;
  }, []);

  // ── Mouse handlers ────────────────────────────────────────────────────────
  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const { x, y } = clientToSim(e.clientX, e.clientY);
    const hit = hitTest(x, y);
    if (hit) {
      draggingNodeRef.current = hit;
      dragMovedRef.current = false;
      hit.pinned = true;
      alphaRef.current = Math.max(alphaRef.current, 0.3);
    } else {
      isPanningRef.current = true;
      panStartRef.current = {
        mx: e.clientX, my: e.clientY,
        tx: transformRef.current.x, ty: transformRef.current.y,
      };
    }
  }, [clientToSim, hitTest]);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const { x, y } = clientToSim(e.clientX, e.clientY);

    if (draggingNodeRef.current) {
      draggingNodeRef.current.x = x;
      draggingNodeRef.current.y = y;
      draggingNodeRef.current.vx = 0;
      draggingNodeRef.current.vy = 0;
      dragMovedRef.current = true;
      return;
    }
    if (isPanningRef.current && panStartRef.current) {
      transformRef.current = {
        ...transformRef.current,
        x: panStartRef.current.tx + e.clientX - panStartRef.current.mx,
        y: panStartRef.current.ty + e.clientY - panStartRef.current.my,
      };
      return;
    }
    const hit = hitTest(x, y);
    hoveredIdRef.current = hit?.id ?? null;
    if (canvasRef.current) {
      canvasRef.current.style.cursor = hit ? "pointer" : "grab";
    }
  }, [clientToSim, hitTest]);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (draggingNodeRef.current) {
      const node = draggingNodeRef.current;
      const moved = dragMovedRef.current;
      if (node.id !== focusIdRef.current) node.pinned = false;
      draggingNodeRef.current = null;
      dragMovedRef.current = false;
      // Treat as click if node wasn't actually dragged
      if (!moved) {
        selectedIdRef.current = node.id;
        setSelectedNode(node);
        setDrawerOpen(true);
      }
      return;
    }
    if (isPanningRef.current) {
      isPanningRef.current = false;
      panStartRef.current = null;
    }
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const t = transformRef.current;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const ns = Math.max(0.08, Math.min(10, t.scale * factor));
    transformRef.current = {
      x: mx + (t.x - mx) * (ns / t.scale),
      y: my + (t.y - my) * (ns / t.scale),
      scale: ns,
    };
  }, []);

  const resetView = useCallback(() => {
    transformRef.current = { x: 0, y: 0, scale: 1 };
    // Briefly re-heat so nodes settle nicely
    for (const n of simNodesRef.current) {
      if (!n.pinned) {
        n.vx = (Math.random() - 0.5) * 2;
        n.vy = (Math.random() - 0.5) * 2;
      }
    }
    alphaRef.current = Math.max(alphaRef.current, 0.4);
  }, []);

  const zoomBy = useCallback((factor: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = rect.width / 2;
    const my = rect.height / 2;
    const t = transformRef.current;
    const ns = Math.max(0.08, Math.min(10, t.scale * factor));
    transformRef.current = {
      x: mx + (t.x - mx) * (ns / t.scale),
      y: my + (t.y - my) * (ns / t.scale),
      scale: ns,
    };
  }, []);

  const isEmpty = !loading && !hasNodes;

  // ── Sidebar section header ────────────────────────────────────────────────
  const sectionLabel = (text: string) => (
    <div style={{
      fontSize: 10, fontWeight: 600, color: TEXT_MUTED,
      letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 9,
    }}>
      {text}
    </div>
  );

  return (
    <div style={{
      display: "flex", height: "calc(100vh - 56px)", marginTop: 56,
      background: CANVAS_BG, overflow: "hidden",
    }}>
      {/* ── Left sidebar ── */}
      <aside style={{
        width: panelOpen ? 268 : 48, flexShrink: 0,
        background: PANEL_BG, borderRight: `1px solid ${BORDER}`,
        transition: "width 0.25s cubic-bezier(0.4,0,0.2,1)",
        overflow: "hidden", display: "flex", flexDirection: "column",
      }}>
        {/* Header */}
        <div style={{
          padding: "13px 12px", borderBottom: `1px solid ${BORDER}`,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <button
            onClick={() => setPanelOpen((p) => !p)}
            title={panelOpen ? "Collapse panel" : "Expand panel"}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              background: "none", border: "none", cursor: "pointer",
              color: TEXT_MUTED, fontSize: 12, fontWeight: 500,
              fontFamily: "var(--font-dm-sans), sans-serif", whiteSpace: "nowrap", padding: 0,
            }}
          >
            <span style={{ fontSize: 16, color: NODE_COLORS.paper, lineHeight: 1 }}>⬡</span>
            {panelOpen && <span style={{ color: TEXT_PRIMARY }}>Graph Explorer</span>}
          </button>
        </div>

        {panelOpen && (
          <div style={{
            padding: "18px 16px", flex: 1, overflowY: "auto",
            display: "flex", flexDirection: "column", gap: 22,
          }}>
            {/* Focus node */}
            {focusId && (
              <div>
                {sectionLabel("Focus Node")}
                <div style={{
                  background: CANVAS_BG, borderRadius: 8, padding: "10px 12px",
                  border: `1px solid ${BORDER}`,
                }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: NODE_COLORS.paper }}>{focusType}</div>
                  <div style={{
                    fontSize: 10, color: TEXT_MUTED, marginTop: 3,
                    fontFamily: "monospace", wordBreak: "break-all",
                  }}>
                    {focusId}
                  </div>
                </div>
              </div>
            )}

            {/* Citation depth */}
            {focusType === "paper" && focusId && (
              <div>
                {sectionLabel("Citation Depth")}
                <div style={{ display: "flex", gap: 6 }}>
                  {[1, 2, 3].map((d) => (
                    <button
                      key={d}
                      onClick={() => setCitDepth(d)}
                      style={{
                        flex: 1, padding: "7px",
                        borderRadius: 7, fontSize: 13, fontWeight: 600,
                      border: `1px solid ${citDepth === d ? NODE_COLORS.paper : BORDER}`,
                      background: citDepth === d ? "#EEF3FF" : "#FAFAF8",
                      color: citDepth === d ? NODE_COLORS.paper : TEXT_MUTED,
                        cursor: "pointer", fontFamily: "var(--font-dm-sans), sans-serif",
                        transition: "all 0.15s",
                      }}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Node types */}
            <div>
              {sectionLabel("Node Types")}
              {(Object.entries(typeFilter) as [GraphNode["type"], boolean][]).map(([type, enabled]) => (
                <label
                  key={type}
                  style={{
                    display: "flex", alignItems: "center", gap: 9,
                    padding: "5px 0", cursor: "pointer", fontSize: 13,
                    color: enabled ? TEXT_PRIMARY : TEXT_MUTED,
                    transition: "color 0.15s",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => setTypeFilter((prev) => ({ ...prev, [type]: !prev[type] }))}
                    style={{ accentColor: NODE_COLORS[type], cursor: "pointer", width: 13, height: 13 }}
                  />
                  <span style={{
                    display: "inline-block", width: 9, height: 9,
                    borderRadius: "50%", background: NODE_COLORS[type],
                    flexShrink: 0, opacity: enabled ? 1 : 0.35,
                    transition: "opacity 0.15s",
                  }} />
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </label>
              ))}
            </div>

            {/* Stats & controls */}
            <div>
              {sectionLabel("Controls")}
              <button
                onClick={resetView}
                style={{
                  display: "block", width: "100%", textAlign: "left",
                  padding: "7px 10px", borderRadius: 7, fontSize: 12,
                  border: `1px solid ${BORDER}`, background: CANVAS_BG,
                  cursor: "pointer", color: TEXT_MUTED,
                  fontFamily: "var(--font-dm-sans), sans-serif",
                  marginBottom: 10, transition: "color 0.15s",
                }}
              >
                ↺ Reset View
              </button>

              <div style={{
                fontSize: 12, color: TEXT_MUTED,
                background: CANVAS_BG, borderRadius: 7, padding: "7px 10px",
                border: `1px solid ${BORDER}`,
              }}>
                <b style={{ color: TEXT_PRIMARY }}>{stats.nodes}</b> nodes ·{" "}
                <b style={{ color: TEXT_PRIMARY }}>{stats.edges}</b> edges
              </div>

              <div style={{
                fontSize: 11, color: TEXT_MUTED, marginTop: 12, lineHeight: 1.7,
              }}>
                Scroll to zoom · drag canvas to pan<br />
                Drag nodes to reposition
              </div>
            </div>
          </div>
        )}
      </aside>

      {/* ── Canvas area ── */}
      <div ref={containerRef} style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        {/* Loading overlay */}
        {loading && (
          <div style={{
            position: "absolute", inset: 0, display: "flex",
            alignItems: "center", justifyContent: "center",
            background: CANVAS_BG, zIndex: 10, flexDirection: "column", gap: 14,
          }}>
            <div style={{
              width: 32, height: 32,
              border: "2px solid #E5E2DD", borderTopColor: NODE_COLORS.paper,
              borderRadius: "50%", animation: "spin 0.7s linear infinite",
            }} />
            <span style={{
              fontSize: 13, color: TEXT_MUTED,
              fontFamily: "var(--font-dm-sans), sans-serif",
            }}>
              Loading graph…
            </span>
            <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
          </div>
        )}

        {/* Empty state */}
        {isEmpty && (
          <div style={{
            position: "absolute", inset: 0, display: "flex",
            alignItems: "center", justifyContent: "center",
            flexDirection: "column", gap: 14, background: CANVAS_BG,
          }}>
            {focusId && focusType !== "paper" ? (
              <>
                <div style={{ fontSize: 44, opacity: 0.15, color: NODE_COLORS.paper }}>⬡</div>
                <div style={{
                  fontSize: 15, fontWeight: 500, color: TEXT_MUTED,
                  fontFamily: "var(--font-dm-serif), Georgia, serif",
                }}>
                  Graph not available for this entity type
                </div>
                <div style={{ fontSize: 13, color: TEXT_MUTED, fontFamily: "var(--font-dm-sans), sans-serif" }}>
                  Open a paper and click &ldquo;Open in Graph Explorer&rdquo; to visualize its citation network.
                </div>
              </>
            ) : (
              <>
                <div style={{ fontSize: 44, opacity: 0.12, color: NODE_COLORS.paper }}>⬡</div>
                <div style={{
                  fontSize: 15, fontWeight: 500, color: TEXT_PRIMARY,
                  fontFamily: "var(--font-dm-serif), Georgia, serif",
                }}>
                  No graph loaded
                </div>
                <div style={{
                  fontSize: 13, color: TEXT_MUTED,
                  fontFamily: "var(--font-dm-sans), sans-serif",
                  textAlign: "center", maxWidth: 320, lineHeight: 1.7,
                }}>
                  Open a paper and click{" "}
                  <b style={{ color: TEXT_PRIMARY }}>&ldquo;Open in Graph Explorer ↗&rdquo;</b>{" "}
                  to explore its citation network.
                </div>
                <button
                  onClick={() => router.push("/")}
                  style={{
                    marginTop: 4, padding: "8px 22px", borderRadius: 9,
                    fontSize: 13, fontWeight: 500,
                    background: NODE_COLORS.paper, color: "#FFF",
                    border: "none", cursor: "pointer",
                    fontFamily: "var(--font-dm-sans), sans-serif",
                  }}
                >
                  Search Papers
                </button>
              </>
            )}
          </div>
        )}

        <canvas
          ref={canvasRef}
          style={{ display: "block", cursor: "grab" }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onWheel={handleWheel}
          onMouseLeave={() => {
            isPanningRef.current = false;
            if (draggingNodeRef.current) {
              const n = draggingNodeRef.current;
              if (n.id !== focusIdRef.current) n.pinned = false;
              draggingNodeRef.current = null;
            }
            hoveredIdRef.current = null;
          }}
        />

        {/* Zoom controls */}
        {hasNodes && (
          <div style={{
            position: "absolute", bottom: 24, right: 24,
            display: "flex", flexDirection: "column", gap: 5,
          }}>
            {[
              { label: "+", factor: 1.25, title: "Zoom in" },
              { label: "−", factor: 0.8, title: "Zoom out" },
            ].map(({ label, factor, title }) => (
              <button
                key={label}
                title={title}
                onClick={() => zoomBy(factor)}
                style={{
                  width: 34, height: 34, borderRadius: 8,
                  background: PANEL_BG, border: `1px solid ${BORDER}`,
                  color: TEXT_PRIMARY, fontSize: 18, cursor: "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "sans-serif", lineHeight: 1,
                }}
              >
                {label}
              </button>
            ))}
            <button
              title="Reset view"
              onClick={resetView}
              style={{
                width: 34, height: 34, borderRadius: 8,
                background: PANEL_BG, border: `1px solid ${BORDER}`,
                color: TEXT_MUTED, fontSize: 13, cursor: "pointer",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}
            >
              ⌂
            </button>
          </div>
        )}

        {/* Legend */}
        {hasNodes && (
          <div style={{
            position: "absolute", bottom: 24, left: 24,
            background: "rgba(255,255,255,0.92)", border: `1px solid ${BORDER}`,
            borderRadius: 10, padding: "10px 14px", backdropFilter: "blur(8px)",
          }}>
            {(Object.entries(NODE_COLORS) as [GraphNode["type"], string][]).map(([type, color]) => (
              <div key={type} style={{
                display: "flex", alignItems: "center", gap: 8,
                marginBottom: 4, fontSize: 11, color: TEXT_MUTED,
              }}>
                <div style={{
                  width: 8, height: 8, borderRadius: "50%",
                  background: color, flexShrink: 0,
                }} />
                {type.charAt(0).toUpperCase() + type.slice(1)}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Right drawer ── */}
      {drawerOpen && selectedNode && (
        <div style={{
          width: 300, background: PANEL_BG, borderLeft: `1px solid ${BORDER}`,
          display: "flex", flexDirection: "column", overflow: "hidden",
        }}>
          <div style={{
            padding: "13px 16px", borderBottom: `1px solid ${BORDER}`,
            display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            <div style={{
              fontSize: 11, fontWeight: 600, color: NODE_COLORS[selectedNode.type],
              textTransform: "uppercase", letterSpacing: "0.08em",
            }}>
              {selectedNode.type}
            </div>
            <button
              onClick={() => { setDrawerOpen(false); setSelectedNode(null); selectedIdRef.current = null; }}
              style={{
                background: "none", border: "none", cursor: "pointer",
                color: TEXT_MUTED, fontSize: 20, lineHeight: 1,
              }}
            >
              ×
            </button>
          </div>

          <div style={{ padding: "18px 16px", overflowY: "auto", flex: 1 }}>
            <h2 style={{
              fontFamily: "var(--font-dm-serif), Georgia, serif",
              fontSize: 16, fontWeight: 400, color: TEXT_PRIMARY,
              margin: "0 0 16px", lineHeight: 1.5,
            }}>
              {selectedNode.label}
            </h2>

            <div style={{
              display: "flex", flexDirection: "column", gap: 7,
              marginBottom: 22, fontSize: 13, color: TEXT_MUTED,
            }}>
              {selectedNode.year && (
                <div>Year: <b style={{ color: TEXT_PRIMARY }}>{selectedNode.year}</b></div>
              )}
              {selectedNode.citations != null && (
                <div>
                  Citations:{" "}
                  <b style={{ color: NODE_COLORS.paper }}>{fmt(selectedNode.citations)}</b>
                </div>
              )}
              <div style={{
                fontFamily: "monospace", fontSize: 11,
                background: CANVAS_BG, padding: "4px 7px", borderRadius: 5,
                color: TEXT_MUTED, wordBreak: "break-all",
                border: `1px solid ${BORDER}`,
              }}>
                {selectedNode.id}
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <button
                onClick={() => { router.push(`/papers/${selectedNode.id}`); setDrawerOpen(false); }}
                style={drawerBtn("primary")}
              >
                Open Paper ↗
              </button>
              <button
                onClick={() => {
                  router.push(`/graph?type=paper&id=${selectedNode.id}`);
                  setDrawerOpen(false);
                }}
                style={drawerBtn("default")}
              >
                Focus This Node
              </button>
              <button
                onClick={() => {
                  simNodesRef.current = simNodesRef.current.filter((n) => n.id !== selectedNode.id);
                  simEdgesRef.current = simEdgesRef.current.filter(
                    (e) => e.source !== selectedNode.id && e.target !== selectedNode.id
                  );
                  setStats({ nodes: simNodesRef.current.length, edges: simEdgesRef.current.length });
                  setDrawerOpen(false);
                  setSelectedNode(null);
                  selectedIdRef.current = null;
                  alphaRef.current = Math.max(alphaRef.current, 0.2);
                }}
                style={drawerBtn("danger")}
              >
                Remove Node
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function GraphExplorer() {
  return (
    <Suspense
      fallback={
        <div style={{
          height: "calc(100vh - 56px)", marginTop: 56,
          background: CANVAS_BG, display: "flex",
          alignItems: "center", justifyContent: "center",
          color: TEXT_MUTED, fontSize: 14,
          fontFamily: "var(--font-dm-sans), sans-serif",
        }}>
          Loading…
        </div>
      }
    >
      <GraphCanvas />
    </Suspense>
  );
}

type Unhover = (graph: { _fullLayout?: { _hoverlayer?: unknown } }, ...args: unknown[]) => unknown;

type UnhoverModule = { raw: Unhover; __irmsLifecycleGuard?: boolean };

// Plotly retains mouse-out callbacks on SVG nodes after purge removes _fullLayout.
// Guard both entry points: wrapped unhover reads raw, while dragElement caches it.
export function guardPlotlyUnhover(unhover: UnhoverModule, dragElement: { unhoverRaw: Unhover }) {
  if (unhover.__irmsLifecycleGuard) return;
  const raw = unhover.raw;
  unhover.raw = function (graph, ...args) {
    if (!graph?._fullLayout?._hoverlayer) return;
    return raw.call(this, graph, ...args);
  };
  dragElement.unhoverRaw = unhover.raw;
  unhover.__irmsLifecycleGuard = true;
}

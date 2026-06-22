declare module "react-plotly.js" {
  import * as React from "react";

  const Plot: React.ComponentType<Record<string, unknown>>;
  export default Plot;
}

declare module "react-plotly.js/factory" {
  import * as React from "react";

  const createPlotlyComponent: (plotly: unknown) => React.ComponentType<Record<string, unknown>>;
  export default createPlotlyComponent;
}

declare module "plotly.js/lib/core" {
  const Plotly: {
    register: (modules: unknown[]) => void;
  } & Record<string, unknown>;
  export default Plotly;
}

declare module "plotly.js/lib/box" {
  const box: unknown;
  export default box;
}

declare module "plotly.js/lib/calendars" {
  const calendars: unknown;
  export default calendars;
}

declare module "plotly.js/lib/scatter" {
  const scatter: unknown;
  export default scatter;
}

declare module "plotly.js/lib/scatter3d" {
  const scatter3d: unknown;
  export default scatter3d;
}

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

declare module "plotly.js-dist-min" {
  const Plotly: unknown;
  export default Plotly;
}

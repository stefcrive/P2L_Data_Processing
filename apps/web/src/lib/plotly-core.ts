import Plotly from "plotly.js/lib/core";
import box from "plotly.js/lib/box";
import calendars from "plotly.js/lib/calendars";
import scatter from "plotly.js/lib/scatter";
import scatter3d from "plotly.js/lib/scatter3d";

const registeredPlotly = Plotly as typeof Plotly & { __irmsRegistered?: boolean };

if (!registeredPlotly.__irmsRegistered) {
  registeredPlotly.register([scatter, scatter3d, box, calendars]);
  registeredPlotly.__irmsRegistered = true;
}

export default registeredPlotly;


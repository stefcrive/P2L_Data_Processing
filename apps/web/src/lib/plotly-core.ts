import Plotly from "plotly.js/lib/core";
import bar from "plotly.js/lib/bar";
import box from "plotly.js/lib/box";
import calendars from "plotly.js/lib/calendars";
import heatmap from "plotly.js/lib/heatmap";
import scatter from "plotly.js/lib/scatter";
import scatter3d from "plotly.js/lib/scatter3d";

const registeredPlotly = Plotly as typeof Plotly & { __irmsRegistrationVersion?: string };
const registrationVersion = "scatter-scatter3d-bar-box-heatmap-calendars-v1";

if (registeredPlotly.__irmsRegistrationVersion !== registrationVersion) {
  registeredPlotly.register([scatter, scatter3d, bar, box, heatmap, calendars]);
  registeredPlotly.__irmsRegistrationVersion = registrationVersion;
}

export default registeredPlotly;


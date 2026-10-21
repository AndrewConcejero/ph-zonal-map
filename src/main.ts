// Milestone 1 placeholder: basemap only. The choropleth lands in milestone 2.
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

new maplibregl.Map({
  container: 'map',
  style: 'https://tiles.openfreemap.org/styles/positron',
  center: [121.03, 14.58],
  zoom: 10,
});

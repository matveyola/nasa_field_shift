# Dated satellite imagery

In **Fields**, turn on **Dated satellite** to see a Sentinel-2 true-color image over the selected field. A blank date selects the most recent suitable scene. Enter an **on or before** date to compare with an earlier observation. The legend records the actual UTC acquisition date, tile-level cloud cover, and the catalog item.

The API searches Element 84 Earth Search for Sentinel-2 Level-2A scenes in a 35-day window ending on the requested date. It chooses the newest scene with at most 20% cloud cover across the tile and a footprint covering the field bounds. Cloud cover can vary within the tile. If no qualifying scene is available, the layer shows a status message and the regular map remains visible. [Earth Search](https://github.com/Element84/earth-search) · [Sentinel-2 data on AWS](https://registry.opendata.aws/sentinel-2-l2a-cogs/)

The browser requests the field window from the public 10 m true-color Cloud Optimized GeoTIFF, reads byte ranges, and projects the crop from the image's UTM coordinate system to the map's Web Mercator coordinate system. Cropping and reading occur only when the layer is enabled. The map credits the source and links to the original scene. [Copernicus Sentinel data licence](https://cds.climate.copernicus.eu/licences/ec-sentinel)

These images provide visual context for observed dates. The moisture model uses its independently evaluated predictor set. An optical scene describes surface reflectance, and using it as a moisture predictor requires its own evaluation against field measurements.

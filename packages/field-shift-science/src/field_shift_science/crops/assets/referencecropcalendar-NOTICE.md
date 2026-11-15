# MIRCA-OS v2 reference calendar derivative

Source: [MIRCA-OS v2](https://www.hydroshare.org/resource/e4582ca0042148338bb5e0148b749ed6/), March 2026.
Citation: Kebede, E.A., Oluoch, K.O., Siebert, S. et al. *Scientific Data* 12, 208
(2025), [doi:10.1038/s41597-024-04313-w](https://doi.org/10.1038/s41597-024-04313-w).
Licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

This derivative preserves the 2020 rainfed planting/maturity months, native
5-arc-minute coordinate vectors and missing-data mask for Barley, Maize,
Rapeseed, Soybeans, Sunflower, Wheat1 and Wheat2. Rows are losslessly compressed
into runs. Empty runs mean missing values; no new observations are introduced.

The source rasterizes administrative calendars. A cell is not evidence of
cultivation or suitability on an individual field. Wheat cycle identifiers are
not relabeled as globally winter/spring cultivars. Pulses is not mapped to Peas.
There are no profit estimates, daily optimal dates or weather probabilities.

Source hashes, native file paths, reader versions, verification time and the
converter hash are in `referencecropcalendar-v1.receipt.json`. Availability is
conservatively the next UTC day after the official resource's last update;
2020 is the reference year, not publication time.

## Reproduction

Extract the receipt's seven `NetCDF/Rainfed/*.nc` paths from the SHA256-pinned
public archive. In a locked API environment, run the installed offline module:

```
python -m field_shift.modules.fields.planting_opportunities.prepare_reference \
  --input-directory /absolute/extracted/rainfed \
  --archive '/absolute/Gridded Crop Calendar.rar' \
  --output /new/referencecropcalendar-v1.json.gz \
  --receipt /new/referencecropcalendar-v1.receipt.json \
  --verified-at <actual-UTC-verification-timestamp>
```

It makes no network calls, pins each input hash, reads bounded blocks, and
refuses existing output destinations. Gzip timestamps are zero for reproducible
artifact bytes; the receipt records the actual verification timestamp.

# Soil moisture v2 release protocol

This protocol defines the historical surface-moisture comparison. The frozen model estimates daily volumetric moisture at a probe depth near 5 cm. Dynamic predictors come from ERA5-Land. Static predictors come from USDA SSURGO profiles at the same location.

Select the final farms from coordinates, crop cover and sensor metadata before reading target values. Use fresh field locations absent from every v1 cohort. Reserve their entire 2° geographic blocks and a minimum 40 km radius from v2 fitting and calibration. Fit and calibrate on pre-2025 data at separate locations. Carry the v1 regional holdout forward as a diagnostic with no role in model selection.

Choose among a regularized linear residual model, a shallow boosted-tree residual model and their half-strength corrections. Score candidates by the mean normalized MAE and RMSE against ERA5-Land in blocked cross-validation of fitting locations. Compute imputation medians separately from each fold's fitting locations. Freeze candidate coefficients, input order, imputation medians and interval using separate calibration locations before evaluating new farm labels.

Promote only when all conditions hold on paired thawed days in 2025.

- At least three separate agricultural locations each supply at least 30 paired days.
- Farm-balanced MAE and RMSE each improve on ERA5-Land by at least 5%.
- At least two thirds of farms improve on ERA5-Land MAE. No farm's RMSE degrades by more than 10%.
- The nominal 90% interval covers at least 75% of the paired farm observations with equal farm weight.
- The frozen v1 geographic holdout remains at or below ERA5-Land MAE and RMSE on at least three unchanged eligible diagnostic locations.

Report v1 and ERA5-Land on the same held-out samples. An underpowered or unsuccessful result keeps ERA5-Land as the operational historical layer. Forecasts and satellite products need separate comparisons appropriate to their timestamps and data sources.

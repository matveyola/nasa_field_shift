# Strategies workspace

Select a registered field to plan a rotation one sowing at a time or compare two crops. Both modes use the field’s current boundary, available crop history and the same private climate model. Existing factual planting records stay separate from scenario choices.

## Plan one step at a time

Choose the total number of steps, the first sowing date and a priority. The current model supports one spring or summer sowing per year, March through July, from 2022 to 2049. A sequence supports up to six steps within ten calendar years. These are conditional climate scenarios based on GCM weather. Dates identify the modeled sowing and its seasonal context.

Strategies shows the date and crop from the latest current-boundary field profile when available. The registered field crop can supply a missing crop identity. Enter a missing crop or sowing date to refine the calculation. This input is scoped to the scenario. The model uses observed history for crop return and replays previously chosen steps to calculate water carryover.

The model evaluates the supported catalog against field inputs, date and climate. It returns up to ten distinct eligible crops for the chosen priority. Exclusions, incomplete cycles and unavailable metrics affect the count and ranking. When a priority cannot be compared, eligible choices are shown without ranks. A result without trusted land evidence remains a conditional calculation.

Choose a crop explicitly before requesting the next step. Editing an earlier choice invalidates its dependent steps. After all steps are chosen, the summary can be saved for the current field and boundary. Repeating the same save returns the same scenario. Saving leaves crop history and approved planting plans untouched.

## Compare two crops

Choose two distinct crops and a common sowing date. A single calculation supplies the same weather, field inputs, water assumptions and previous crop history to both candidates. The comparison shows the selected metric and its difference when units and values are compatible. Reference yield is identified as such. Revenue after seed subtracts seed or planting-material cost from gross revenue per hectare. It excludes other costs.

Both modes accept optional irrigation. An unreported supply remains unknown. Explicitly unavailable irrigation has its own status. When daily availability is known, the API divides the volume in cubic metres by ten times the registered hectares to obtain millimetres per day. The model identifies the water-management assumption used in the result.

## Application and recovery

The field-scoped handoff API exposes strategy context, submissions and saved scenarios. Submissions use the existing rotation handoff simulation queue with the existing 120-second task bound, three attempts, lease fencing and durable acquire and evaluation checkpoints. The API derives the field location and history, and verifies each referenced selection against a completed job's returned choices. Job history and saved scenarios remain separate. Migration `0017_strategy_scenarios` adds the saved records. To retain these records when reverting code, keep the database at the current revision or restore a verified pre-upgrade backup into a new database.

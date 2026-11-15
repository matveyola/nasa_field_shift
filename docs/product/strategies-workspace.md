# Strategies workspace

Strategies has two tabs for the selected field. Both use the current boundary
and the conditional climate model. The field passport and crop history are
optional.

## Sowing date

Enter a date and select Calculate. The model evaluates supported crops for that
date using water deficit as the initial priority. It displays up to ten distinct
eligible crops. When regional suitability or a comparable ranking is unknown,
the model can still show conditional crop estimates. A selected result can be
saved as a one-step scenario.

The model supports one spring or summer sowing per year, March through July,
from 2022 to 2049. It uses earlier field records where available. Only earlier
crop seasons with reported dates preceding the first proposed sowing inform
the calculation. A field with unknown history sends an empty history. This
leaves history-dependent estimates such as crop return intervals unknown.
Coordinate-based weather and soil evidence can still be required to execute
the model for the field.

## Scenarios

Open a saved scenario or expand Create a scenario to plan up to six steps
within ten calendar years. Choose a crop before advancing to the next step.
Changing an earlier choice invalidates the following results. The model replays
the chosen crops to calculate water carryover. The same factual history cutoff
applies to each step.

A scenario can use a custom priority and optional irrigation. A reported daily
volume in cubic metres is divided by ten times the registered area in hectares
to obtain millimetres per day. Unreported availability remains unknown.
Revenue after seed subtracts seed or planting-material cost from gross revenue
per hectare.

Saving is voluntary and idempotent for the current field and boundary. Saved
scenarios can be read while the model is offline. Factual records and modeled
choices are stored separately.

## Application and recovery

The field-scoped handoff API exposes strategy context, submissions and saved
scenarios. Submissions use the existing rotation handoff simulation queue with
its 120-second task bound, three attempts, lease fencing and durable acquisition
and evaluation checkpoints. Migration `0017_strategy_scenarios` stores the
saved records.

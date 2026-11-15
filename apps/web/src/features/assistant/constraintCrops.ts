// The crops a farmer may choose when editing an assistant-proposed condition.
//
// This app's planning catalogue (PlanningCrop, served by the API's planning_catalog.py) and the agent / simulation
// vocabulary (services/agent agent_service/plain.py CROPS; the rotation results the simulation returns) do not share
// one canonical crop-id system. Only crops that are unmistakably the same crop in both are offered here, under the
// agent's id, so a confirmed restriction matches the crop ids a returned rotation uses:
//
//   planning Maize     = agent maize
//   planning Soy       = agent soybean
//   planning Sunflower = agent sunflower
//
// Not offered, because the identity is ambiguous or missing (a canonical crop contract is a separate task):
//   planning Wheat (generic)  vs agent winter_wheat     planning Peas (generic) vs agent winter_pea
//   planning Barley, Rapeseed: no agent crop             agent sorghum, winter_rye_vetch: no planning crop
// A proposal naming any of those can still be confirmed, rejected or hidden as proposed, but not edited.
import type { PlanningCrop } from '../../api/rotationPlanning'

/** Planning crops whose identity the agent shares, with the agent's crop id. */
export const SHARED_CROP_IDS: Readonly<Partial<Record<PlanningCrop, string>>> = {
  Maize: 'maize',
  Soy: 'soybean',
  Sunflower: 'sunflower',
}

/** Planning crops left out of editing, and why. */
export const UNSHARED_PLANNING_CROPS: Readonly<Partial<Record<PlanningCrop, string>>> = {
  Wheat: 'generic wheat; the agent knows only winter_wheat',
  Peas: 'generic peas; the agent knows only winter_pea',
  Barley: 'no agent crop',
  Rapeseed: 'no agent crop',
}

/** The crop list the widget offers, named as the app names its planning crops in the current language. */
export function editableCropOptions(name: (crop: PlanningCrop) => string): { id: string; label: string }[] {
  return (Object.entries(SHARED_CROP_IDS) as [PlanningCrop, string][]).map(([crop, id]) => ({ id, label: name(crop) }))
}

export const EDITABLE_CROP_IDS: ReadonlySet<string> = new Set(Object.values(SHARED_CROP_IDS))

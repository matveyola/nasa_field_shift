export const priorityLabels: Record<string, [string, string]> = {
  water_stress: ['Water stress days', 'Дні водного стресу'], water_deficit: ['Water deficit', 'Дефіцит води'], water_saving: ['Scheduled irrigation', 'Запланований полив'], yield: ['Yield input', 'Вхідна врожайність'], revenue: ['Gross revenue', 'Виручка'], soil_cover: ['Living soil cover', 'Живий покрив ґрунту'],
}
export const errors: Record<string, [string, string]> = {
  engine_unavailable: ['This calculation is unavailable. Refresh the model catalog.', 'Розрахунок недоступний. Оновіть каталог моделі.'],
  plan_count: ['Use two or three plans with one crop for each of one to three cycles.', 'Оберіть два або три плани з однією культурою на кожен із одного–трьох циклів.'],
  planting_date: ['Enter valid, increasing planting dates from today. Optional end dates must follow planting.', 'Укажіть дійсні дати сівби від сьогодні у зростаючому порядку. Кінцева дата має бути не раніше сівби.'],
  calendar: ['Choose a crop and a sourced catalog calendar for every cycle. A missing calendar cannot be invented.', 'Оберіть культуру та календар із джерелом для кожного циклу. Відсутній календар не підставляється за припущенням.'],
  overlap: ['Crop calendars overlap or finish beyond the three-year calculation limit. Review dates and calendar durations.', 'Календарі культур перекриваються або виходять за трирічну межу розрахунку. Перевірте дати та тривалості.'],
  moisture: ['Opening moisture needs three fractions from 0 to 1 and a source.', 'Початкова вологість потребує трьох часток від 0 до 1 та джерела.'],
  economics: ['Enter nonnegative economic assumptions with a source and a three-letter currency for monetary values. Blank values stay unknown.', 'Укажіть невід’ємні економічні припущення, джерело та трибуквену валюту для грошових значень. Порожні значення залишаються невідомими.'],
  irrigation: ['Irrigation needs a unique date within this plan’s crop periods and positive net millimetres.', 'Для поливу потрібна неповторна дата в межах періодів культур цього плану й додатна норма нетто в міліметрах.'],
  priority: ['Choose a priority supported by this model catalog.', 'Оберіть пріоритет, підтримуваний цим каталогом.'],
  equipment: ['Review equipment choices against the current catalog.', 'Перевірте вибір техніки за поточним каталогом.'],
  live_days: ['The live weather segment supports one to ten days.', 'Сегмент оперативного прогнозу погоди підтримує від одного до десяти днів.'],
  scope_mismatch: ['The returned job does not match this field, boundary revision or saved job. Refresh before continuing.', 'Завдання не відповідає полю, версії меж або збереженому завданню. Оновіть дані перед продовженням.'],
  submission_interrupted: ['The submission response was interrupted. Retry the same request to recover its saved job; this does not create a duplicate.', 'Відповідь на запит перервано. Повторіть той самий запит, щоб отримати збережене завдання без дублювання.'],
}
export const label = (map: Record<string, [string, string]>, id: string, uk: boolean) => map[id]?.[uk ? 1 : 0] ?? id
export const cropNames: Record<string, string> = { wheat_winter_frozen: 'Winter wheat, frozen soil', wheat_winter_nonfrozen: 'Winter wheat, nonfrozen soil', wheat_spring: 'Spring wheat', barley: 'Barley', maize_grain: 'Grain maize', sunflower: 'Sunflower', soybeans: 'Soybean', rapeseed_canola: 'Rapeseed / canola', peas_dry: 'Dry peas', sugar_beet: 'Sugar beet', potato: 'Potato', oats: 'Oats', millet: 'Millet, generic profile' }
export const equipmentNames: Record<string, string> = { tractor: 'Tractor', row_planter: 'Row planter', grain_drill: 'Grain drill', grain_combine: 'Grain combine', potato_planter: 'Potato planter', potato_harvester: 'Potato harvester', beet_planter: 'Beet planter', beet_harvester: 'Beet harvester', sprayer: 'Sprayer', fertilizer_spreader: 'Fertilizer spreader', sprinkler: 'Sprinkler', drip_system: 'Drip system', irrigation_pump: 'Irrigation pump' }

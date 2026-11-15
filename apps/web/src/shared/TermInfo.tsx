import { ContextHelp } from './ContextHelp'
import { useLocale } from './locale'

const definitions = {
  water_stress: ["Share of crop days with drought stress or waterlogging. A day with both is counted once.","Частка днів росту зі стресом від нестачі води або від перезволоження. День з обома видами стресу рахується один раз."],
  water_deficit: ["Share of crop water demand that was not met over the modelled season.","Частка загальної потреби культури у воді, яку не вдалося покрити за змодельований сезон."],
  water_saving: ["Amount of irrigation water scheduled by the model for the season, expressed as depth over the field.","Скільки води модель подала з поливу за сезон, у міліметрах шару на все поле."],
  yield: ["Typical marketable harvest per hectare from the reference data.","Довідкова кількість товарного врожаю з гектара."],
  revenue: ["Reference yield per hectare multiplied by the farm-gate price per tonne.","Довідкова врожайність із гектара, помножена на ціну продажу тонни виробником."],
  revenue_after_seed: ["Gross revenue per hectare less the reference cost of seed or planting material.","Виручка з гектара за вирахуванням довідкової вартості насіння або посадкового матеріалу."],
  soil_cover: ["Modelled number of days when a living crop covers the soil.","Змодельована кількість днів, коли ґрунт зайнятий живою культурою."],
  seasons: ["Number of growing seasons in the rotation. Two crops can be sown in the same year if their growing periods do not overlap and both dates are supported.","Кількість сезонів вирощування в сівозміні. Дві культури можна посіяти за один рік, якщо їхні періоди вирощування не перетинаються й обидві дати підтримуються."],
  sowing: ["The date the model uses to start the selected crop cycle.","Дата, від якої модель починає розраховувати цикл культури."],
  reserve: ["Water available to a crop in its modelled root zone, expressed in millimetres of water over the field.","Вода, доступна культурі в розрахунковій кореневій зоні, у міліметрах шару води на поле."],
  water_balance: ["A daily account of stored water, water entering as rain or irrigation, and water leaving through crop use or drainage.","Щоденний облік запасу води, надходжень з опадів і поливу та втрат через водоспоживання і дренаж."],
  drought: ["A model signal for days when available water in the root zone falls below the reference threshold.","Сигнал моделі для днів, коли запас доступної води в кореневій зоні падає нижче довідкового порога."],
  threshold: ["Reference minimum for the modelled water reserve. Days below it can indicate a shortage.","Довідковий мінімум розрахункового запасу води. Дні нижче цього значення можуть вказувати на нестачу."],
  reference_et0: ["Estimated water loss from a reference grass surface through evaporation and plant transpiration. It reflects weather conditions.","Оцінка втрат води з еталонної трав’яної поверхні через випаровування й транспірацію. Залежить від погоди."],
  surface_moisture: ["Volume of water per volume of soil in the top seven centimetres.","Об’єм води на одиницю об’єму ґрунту у верхніх семи сантиметрах."],
  precipitation: ["Water reaching the ground from rain or snow, expressed as millimetres of water.","Вода, що надходить на поверхню з дощем або снігом, у міліметрах водяного шару."],
  field_capacity: ["Water still held in the soil after excess water has drained away.","Кількість води, яку ґрунт утримує після стікання надлишку."],
  wilting_point: ["Soil moisture at which a plant can no longer extract enough water to recover from wilting.","Вологість ґрунту, за якої рослина вже не може поглинути достатньо води, щоб відновитися після в’янення."],
  organic_carbon: ["Carbon contained in soil organic matter, expressed here in grams per kilogram of soil.","Вуглець у складі органічної речовини ґрунту, тут у грамах на кілограм ґрунту."],
  ph: ["A measure of soil acidity or alkalinity. Lower numbers mean greater acidity.","Показник кислотності або лужності ґрунту. Нижчі значення відповідають більшій кислотності."],
  recharge: ["Estimated time after harvest for the modelled soil water reserve to reach 80% of capacity.","Оцінений час після завершення культури, за який запас доступної води у ґрунті відновлюється до 80% місткості."],
  fallow: ["Days between the modelled end of one crop and sowing of the next crop.","Дні між змодельованим завершенням попередньої культури та сівбою наступної."],
  return_interval: ["The reference interval before growing the same crop on this field again, used to manage crop-specific disease risks.","Довідковий проміжок до повторного вирощування тієї самої культури на полі для зменшення ризику її хвороб."],
  erosion: ["Estimated annual soil loss from rainfall and runoff, usually expressed in tonnes per hectare per year.","Оцінені річні втрати ґрунту від опадів і поверхневого стоку, зазвичай у тоннах на гектар за рік."],
  drainage: ["Water that moves below the modelled soil profile and leaves its water budget.","Вода, що пішла нижче змодельованого шару ґрунту й вибула з його водного балансу."],
  reference_period: ["The years used to calculate a typical rainfall value for the same time of year.","Роки, за які рахують типову кількість опадів для того самого періоду року."],
} as const

export type Term = keyof typeof definitions

export function TermInfo({ term, label }: { term: Term; label: string }) {
  const { language } = useLocale()
  return <ContextHelp label={label}><p>{definitions[term][language === 'uk' ? 1 : 0]}</p></ContextHelp>
}

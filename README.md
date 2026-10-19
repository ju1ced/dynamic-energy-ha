# Dynamic Energy (Home Assistant)

Adviesplannen voor je thuisbatterij en je EV op een dynamisch contract, volledig
binnen Home Assistant. De integratie leest de kwartierprijzen van de
[BE electricity prices](https://github.com/renaudallard/homeassistant_be_electricity_prices)-integratie
(en optioneel je Solcast-PV-voorspelling en je verbruiksgeschiedenis) en bepaalt
per kwartier wat de batterij het best doet en wanneer de EV het goedkoopst laadt,
binnen je prijsplafond en je gezamenlijke importlimiet.

**Alleen advies.** De integratie leest alleen Home Assistant-states. Ze schakelt
geen lader, batterij of omvormer en bevat geen schrijfpad naar apparaten. Je
kunt zelf een automatisatie maken op `binary_sensor.dynamic_energy_ev_charge_advised`.

## Installeren

1. HACS → Integraties → ⋮ → *Aangepaste repositories* → voeg
   `https://github.com/ju1ced/dynamic-energy-ha` toe (categorie *Integratie*).
2. Download *Dynamic Energy* en herstart Home Assistant.
3. Instellingen → Apparaten & services → *Integratie toevoegen* → *Dynamic Energy*.
   Kies de sensor *Huidige prijs* van je dynamische contract (die heeft de
   attributen `today` en `tomorrow`). Alle andere velden zijn optioneel maar
   nodig voor het batterijplan: injectieprijs, laadtoestand van de batterij,
   huisverbruik (vermogen), EV-vermogen (wordt van het huisverbruik afgetrokken),
   maandpiek, de Solcast-sensoren *voorspelling vandaag* en *morgen*, en de
   laadtoestand van de auto.

## Entiteiten

| Entiteit | Betekenis |
|---|---|
| `sensor.dynamic_energy_battery_plan` | Status (`PLANNED`, `NO_ACTION`, `DISABLED`) met het plan als attribuut `schedule` (per kwartier: actie, batterijvermogen, SOC, net, prijzen), totalen, `saving_eur`, `quality_flags`, `grid_cap_kw` |
| `sensor.dynamic_energy_battery_action_now` | Actie van het huidige kwartier: `CHARGE_FROM_GRID`, `STORE_PV_SURPLUS`, `DISCHARGE_TO_LOAD`, `HOLD` |
| `sensor.dynamic_energy_battery_planned_saving` | Geplande besparing (EUR) tegenover niets doen |
| `sensor.dynamic_energy_ev_plan` | Status (`PLANNED`, `INCOMPLETE`, `TARGET_REACHED`, `DISABLED`, `NO_PRICES`) met het plan als attribuut `schedule` en samenvatting (kosten, besparing, nodig prijsplafond) |
| `sensor.dynamic_energy_ev_planned_energy` / `_ev_plan_cost` / `_ev_next_charge_start` | Geplande energie, kost en volgende laadstart |
| `binary_sensor.dynamic_energy_ev_charge_advised` | Aan als het huidige kwartier in het EV-plan zit |
| `number.*`, `time.*`, `switch.*` | Instellingen (zie hieronder) |

Instellingen: importlimiet, prijsplafond, doel-SOC, SOC bij aankomst,
accucapaciteit, laadvermogen, basisverbruik, vertrektijd en planning aan/uit voor
de EV; en voor de batterij bruikbare capaciteit, laad- en ontlaadvermogen,
rendement, minimum- en maximum-SOC, slijtagekost, toegestane piekverhoging,
injectieprijs-terugval en planning aan/uit.

Entiteit-ID's zijn onafhankelijk van de taal van Home Assistant (bijvoorbeeld
`number.dynamic_energy_price_ceiling`, `switch.dynamic_energy_battery_planning`,
`time.dynamic_energy_ev_departure`); alleen de weergavenamen zijn vertaald.

## Hoe het batterijplan werkt

- Horizon: het huidige kwartier tot het einde van de bekende prijzen (vandaag en,
  zodra gepubliceerd, morgen).
- Per kwartier schat de integratie het nettoverbruik van het huis: verbruik min
  PV. Het verbruik komt uit de mediaan per weekdagtype en uur van de laatste 28
  dagen (Recorder-statistieken van de huisverbruiksensor, zonder het EV-aandeel);
  zonder historiek geldt het vaste *basisverbruik*. De PV komt uit de Solcast
  `detailedForecast`.
- Een dynamisch programma (SOC-raster van 0,05 kWh) minimaliseert netkost plus
  slijtage onder het rendement en de netlimiet, en eindigt de horizon op de
  begin-SOC. Overschot wordt eerst opgeslagen; de batterij ontlaadt alleen naar
  het huis, nooit naar het net.
- De netlimiet voor de batterij is de maandpiek (minstens 2,5 kW, de minimale
  gefactureerde piek) plus de toegestane piekverhoging, begrensd door de
  maximale netimport. Zo verhoogt het plan je capaciteitskost niet.
- Daarna plant het EV-plan eromheen: de EV krijgt de ruimte onder de importlimiet
  die huis en batterij overlaten, en neemt PV-overschot-kwartieren eerst.

## Hoe het EV-plan werkt

- De auto hangt nu aan de lader tot de eerstvolgende vertrektijd.
- Nodige energie = (doel-SOC − SOC) × accucapaciteit. De SOC komt uit de
  gekozen autosensor, anders uit *EV SOC bij aankomst*.
- Per kwartier is het laadvermogen `min(max. laadvermogen, importlimiet − netto huis en batterij)`.
- De goedkoopste kwartieren onder het prijsplafond worden gekozen tot het doel
  bereikt is. Kwartieren van vóór nu of ná het vertrek tellen niet mee.
- Staan er nog geen prijzen voor morgen, dan is de status `INCOMPLETE` en wordt
  het plan bijgewerkt zodra ze binnen zijn (meestal rond 13 uur).
- `required_price_ceiling` zegt welk plafond nodig is om het doel te halen.

## Beperkingen

- Batterij en EV worden na elkaar gepland (eerst de batterij, dan de EV eromheen),
  niet gezamenlijk geoptimaliseerd.
- De batterij ontlaadt nooit naar het net, ook niet als de injectieprijs hoog is.
- Het plan is zo goed als de voorspellingen (verbruiksmediaan, Solcast).
- Geen minimaal laadvermogen of faseomschakeling bij de EV.
- Alleen advies: de integratie bestuurt geen batterij, omvormer of lader.

## Ontwikkelen

```sh
pip install -r requirements_test.txt
pytest -q
```

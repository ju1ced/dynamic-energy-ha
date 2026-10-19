# Dynamic Energy (Home Assistant)

Adviesplan voor het laden van je EV op een dynamisch contract, volledig binnen
Home Assistant. De integratie leest de kwartierprijzen van de
[BE electricity prices](https://github.com/renaudallard/homeassistant_be_electricity_prices)-integratie
en bepaalt de goedkoopste laadkwartieren vóór je vertrektijd, binnen je
prijsplafond en je gezamenlijke importlimiet.

**Alleen advies.** De integratie leest alleen Home Assistant-states. Ze schakelt
geen lader, batterij of omvormer en bevat geen schrijfpad naar apparaten. Je
kunt zelf een automatisatie maken op `binary_sensor.dynamic_energy_ev_charging_advised`.

## Installeren

1. HACS → Integraties → ⋮ → *Aangepaste repositories* → voeg
   `https://github.com/ju1ced/dynamic-energy-ha` toe (categorie *Integratie*).
2. Download *Dynamic Energy* en herstart Home Assistant.
3. Instellingen → Apparaten & services → *Integratie toevoegen* → *Dynamic Energy*.
   Kies de sensor *Huidige prijs* van je dynamische contract (die heeft de
   attributen `today` en `tomorrow`) en optioneel de laadtoestand van je auto.

## Entiteiten

| Entiteit | Betekenis |
|---|---|
| `sensor.dynamic_energy_ev_plan` | Status (`PLANNED`, `INCOMPLETE`, `TARGET_REACHED`, `DISABLED`, `NO_PRICES`) met het plan als attribuut `schedule` en samenvatting (kosten, besparing, nodig prijsplafond) |
| `sensor.dynamic_energy_ev_planned_energy` | Geplande energie (kWh) |
| `sensor.dynamic_energy_ev_plan_cost` | Plankost (EUR) |
| `sensor.dynamic_energy_ev_next_charge_start` | Start van het eerstvolgende laadkwartier |
| `binary_sensor.dynamic_energy_ev_charging_advised` | Aan als het huidige kwartier in het plan zit |
| `number.*`, `time.*`, `switch.*` | Instellingen: importlimiet, prijsplafond, doel-SOC, SOC bij aankomst, accucapaciteit, laadvermogen, basisverbruik, vertrektijd, planning aan/uit |

Entiteit-ID's zijn onafhankelijk van de taal van Home Assistant.

## Hoe het plan werkt

- De auto hangt nu aan de lader tot de eerstvolgende vertrektijd.
- Nodige energie = (doel-SOC − SOC) × accucapaciteit. De SOC komt uit de
  gekozen autosensor, anders uit *EV SOC bij aankomst*.
- Per kwartier is het laadvermogen `min(max. laadvermogen, importlimiet − basisverbruik)`.
- De goedkoopste kwartieren onder het prijsplafond worden gekozen tot het doel
  bereikt is. Kwartieren van vóór nu of ná het vertrek tellen niet mee.
- Staan er nog geen prijzen voor morgen, dan is de status `INCOMPLETE` en wordt
  het plan bijgewerkt zodra ze binnen zijn (meestal rond 13 uur).
- `required_price_ceiling` zegt welk plafond nodig is om het doel te halen.

## Beperkingen

- Het basisverbruik van het huis is een vaste instelling, geen voorspelling.
- Geen batterijplanner en geen PV-voorspelling (nog niet).
- Geen minimaal laadvermogen of faseomschakeling.

## Ontwikkelen

```sh
pip install -r requirements_test.txt
pytest -q
```
